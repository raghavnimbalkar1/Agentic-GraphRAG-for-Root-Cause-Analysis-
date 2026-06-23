"""
eval/baselines/vector_rag.py

Baseline B2: Vector RAG (Retrieval-Augmented Generation)

Represents the state-of-the-art RAG approach used in most AIOps tools:
  - All SOP documentation is embedded as text vectors (sentence-transformers)
  - At query time, the alert is embedded and top-k semantically similar SOPs
    are retrieved via FAISS
  - Retrieved SOP text + alert → LLM to predict root cause + blast radius

This is strictly better than zero-shot (it has SOP knowledge) but has a
fundamental limitation: it retrieves by text similarity, not by causal
graph topology. A cascade failure like "redis-cart OOM → cartservice →
checkoutservice → frontend" requires following DEPENDS_ON edges. Vector
similarity over SOP text finds SOPs that mention similar words, not SOPs
that apply to the upstream root of the observed symptom.

SOP Corpus: Pulled from Neo4j Skill nodes at init time. Each document is:
    "Skill: {name}. Applies to: {service}. Trigger: {trigger_condition}. {description}"

Model: sentence-transformers/all-MiniLM-L6-v2 (384-dim, fast, 80MB)
Index: FAISS IndexFlatL2 (exact search, 9 docs is too small for HNSW)

Interface:
    baseline = VectorRAGBaseline()   # builds FAISS index from Neo4j
    result = baseline.resolve(alert_dict)

    alert_dict: {"service": str, "error_type": str, "message": str}
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import settings

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
TOP_K = 3

KNOWN_SERVICES = [
    "emailservice",
    "checkoutservice",
    "recommendationservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "cartservice",
    "loadgenerator",
    "currencyservice",
    "shippingservice",
    "adservice",
    "redis-cart",
]

SYSTEM_PROMPT = f"""You are an expert Site Reliability Engineer performing root cause analysis
on a cloud-native microservice system (Google's Online Boutique).

The system has these services:
{json.dumps(KNOWN_SERVICES, indent=2)}

You have been given:
1. An alert describing an observed failure
2. The most semantically relevant SOP (Standard Operating Procedure) documents
   retrieved from a knowledge base via similarity search

Using this information, identify:
- ROOT CAUSE: the single service whose failure triggered the cascade
- BLAST RADIUS: all services affected (directly or indirectly)

Respond ONLY with valid JSON in this exact format:
{{
  "root_cause": "<service_name_from_the_list_above>",
  "blast_radius": ["<service1>", "<service2>"],
  "matched_sop": "<name of the most relevant retrieved SOP>",
  "confidence": "high|medium|low",
  "reasoning": "<one sentence explanation>"
}}

Do not add any text outside the JSON object."""


@dataclass
class VectorRAGResult:
    root_cause: str
    blast_radius: list[str]
    matched_sop: str
    retrieved_sops: list[str]       # names of SOPs retrieved by vector search
    confidence: str
    reasoning: str
    tokens_used: int
    latency_s: float
    retrieval_latency_s: float      # just the FAISS lookup time
    raw_response: str
    error: Optional[str] = None


class VectorRAGBaseline:
    """
    Vector RAG baseline: semantic SOP retrieval via FAISS + LLM.

    Build the FAISS index once at init (queries Neo4j for Skill nodes).
    Resolve is thread-safe (FAISS search is read-only after index is built).
    """

    def __init__(self) -> None:
        self._llm = None
        self._encoder = None
        self._index: Optional[faiss.Index] = None
        self._sop_docs: list[dict] = []   # [{name, text, service, trigger}]
        self._built = False

    # ── Build phase (call once before any resolve()) ───────────────────────

    def build_index(self) -> None:
        """
        Pull Skill nodes from Neo4j, embed them with sentence-transformers,
        and build a FAISS FlatL2 index.

        Called automatically on first resolve() if not already built.
        Separated so benchmarks can time index build independently.
        """
        if self._built:
            return

        sop_docs = self._fetch_sops_from_neo4j()
        if not sop_docs:
            raise RuntimeError(
                "No Skill nodes found in Neo4j. "
                "Is the graph populated? (check neo4j/init/ scripts)"
            )

        self._sop_docs = sop_docs
        texts = [doc["text"] for doc in sop_docs]

        encoder = self._get_encoder()
        embeddings = encoder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        embeddings = np.array(embeddings, dtype="float32")

        dim = embeddings.shape[1]
        index = faiss.IndexFlatL2(dim)
        index.add(embeddings)

        self._index = index
        self._built = True

    def _fetch_sops_from_neo4j(self) -> list[dict]:
        """
        Query all Skill nodes from Neo4j and format them as text documents.
        This is the ONLY graph access vector_rag makes — at index build time.
        It does NOT use the graph topology at query time (that's the distinction).
        """
        from graph.graph_client import GraphClient
        gc = GraphClient()
        rows = gc._run("""
            MATCH (skill:Skill)-[:APPLIES_TO]->(svc:Service)
            RETURN skill.name             AS name,
                   skill.description     AS description,
                   skill.trigger_condition AS trigger,
                   skill.script_path     AS script_path,
                   svc.name              AS service
            ORDER BY skill.name
        """)

        docs = []
        for row in rows:
            text = (
                f"Skill: {row['name']}. "
                f"Applies to service: {row['service']}. "
                f"Trigger condition: {row['trigger']}. "
                f"Description: {row['description'] or 'No description'}. "
                f"Script: {row['script_path']}."
            )
            docs.append({
                "name":    row["name"],
                "text":    text,
                "service": row["service"],
                "trigger": row["trigger"],
            })
        return docs

    # ── Resolve ────────────────────────────────────────────────────────────

    def resolve(self, alert: dict) -> VectorRAGResult:
        """
        Run vector RAG inference on the alert.

        Args:
            alert: dict with keys "service", "error_type", "message"

        Returns:
            VectorRAGResult — always returns even on partial failure
        """
        if not self._built:
            self.build_index()

        # ── Retrieval ──────────────────────────────────────────────────────
        query = (
            f"Service: {alert.get('service', '')}. "
            f"Error: {alert.get('error_type', '')}. "
            f"{alert.get('message', '')}"
        )

        t_retrieval_start = time.perf_counter()
        encoder = self._get_encoder()
        query_vec = encoder.encode([query], normalize_embeddings=True, show_progress_bar=False)
        query_vec = np.array(query_vec, dtype="float32")

        k = min(TOP_K, len(self._sop_docs))
        distances, indices = self._index.search(query_vec, k)
        retrieval_latency_s = round(time.perf_counter() - t_retrieval_start, 4)

        retrieved = [self._sop_docs[i] for i in indices[0] if i < len(self._sop_docs)]
        retrieved_names = [doc["name"] for doc in retrieved]

        # ── Prompt construction ────────────────────────────────────────────
        sop_block = "\n\n".join(
            f"[SOP {idx + 1}] {doc['text']}"
            for idx, doc in enumerate(retrieved)
        )
        prompt = (
            f"ALERT:\n"
            f"  Alerting service: {alert.get('service', 'unknown')}\n"
            f"  Error type:       {alert.get('error_type', 'unknown')}\n"
            f"  Message:          {alert.get('message', '')}\n\n"
            f"RETRIEVED SOPs (semantic search, top {k}):\n{sop_block}\n\n"
            f"Identify the root cause and blast radius. Respond with JSON only."
        )

        # ── LLM call ──────────────────────────────────────────────────────
        llm = self._get_llm()
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]

        t_llm_start = time.perf_counter()
        try:
            response = llm.invoke(messages)
            latency_s = round(time.perf_counter() - t_llm_start, 3)
        except Exception as exc:
            return VectorRAGResult(
                root_cause=alert.get("service", "unknown"),
                blast_radius=[],
                matched_sop="",
                retrieved_sops=retrieved_names,
                confidence="low",
                reasoning="LLM call failed",
                tokens_used=0,
                latency_s=round(time.perf_counter() - t_llm_start, 3),
                retrieval_latency_s=retrieval_latency_s,
                raw_response="",
                error=str(exc),
            )

        raw = response.content.strip()

        tokens_used = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            um = response.usage_metadata
            tokens_used = getattr(um, "total_tokens", 0) or (
                getattr(um, "input_tokens", 0) + getattr(um, "output_tokens", 0)
            )

        # ── Parse JSON ────────────────────────────────────────────────────
        clean = raw
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.strip()

        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError as exc:
            return VectorRAGResult(
                root_cause=alert.get("service", "unknown"),
                blast_radius=[],
                matched_sop="",
                retrieved_sops=retrieved_names,
                confidence="low",
                reasoning="JSON parse failed",
                tokens_used=tokens_used,
                latency_s=latency_s,
                retrieval_latency_s=retrieval_latency_s,
                raw_response=raw,
                error=f"JSON parse error: {exc}",
            )

        return VectorRAGResult(
            root_cause=parsed.get("root_cause", alert.get("service", "unknown")),
            blast_radius=parsed.get("blast_radius", []),
            matched_sop=parsed.get("matched_sop", ""),
            retrieved_sops=retrieved_names,
            confidence=parsed.get("confidence", "low"),
            reasoning=parsed.get("reasoning", ""),
            tokens_used=tokens_used,
            latency_s=latency_s,
            retrieval_latency_s=retrieval_latency_s,
            raw_response=raw,
        )

    # ── Lazy singletons ───────────────────────────────────────────────────

    def _get_encoder(self) -> SentenceTransformer:
        if self._encoder is None:
            self._encoder = SentenceTransformer(EMBEDDING_MODEL)
        return self._encoder

    def _get_llm(self):
        if self._llm is not None:
            return self._llm

        if settings.llm_provider.value == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI
            self._llm = ChatGoogleGenerativeAI(
                model=settings.llm_model,
                temperature=0,
                google_api_key=settings.google_api_key,
            )
        elif settings.llm_provider.value == "openai":
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                model=settings.llm_model,
                temperature=0,
                api_key=settings.openai_api_key,
            )
        else:
            raise ValueError(
                f"Unsupported provider for baseline: {settings.llm_provider.value}. "
                "Use 'gemini' or 'openai'."
            )
        return self._llm
