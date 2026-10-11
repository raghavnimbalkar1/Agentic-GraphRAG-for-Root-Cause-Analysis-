"""
eval/baselines/vector_rag.py

Baseline B2: Vector RAG (Retrieval-Augmented Generation)

Represents the state-of-the-art RAG approach used in most AIOps tools:
  - All SOP documentation is embedded as text vectors (sentence-transformers)
  - At query time, the alert is embedded and top-k semantically similar SOPs
    are retrieved via FAISS
  - Retrieved SOP text + alert → LLM to predict root cause + blast radius

It retrieves by text similarity, not dependency traversal. The controlled harness
provides the same observed health snapshot as the other systems. Its relative
accuracy is an experimental question, not an assumed result.

SOP Corpus: Pulled from Neo4j Skill nodes at init time. Each document is:
    "Skill: {name}. Applies to: {service}. Trigger: {trigger_condition}. {description}"

Model: sentence-transformers/all-MiniLM-L6-v2 (384-dim, fast, 80MB)
Index: FAISS IndexFlatL2 (exact search of the seeded applicability documents)

Interface:
    baseline = VectorRAGBaseline()   # builds FAISS index from Neo4j
    result = baseline.resolve(alert_dict)

    alert_dict: {"service": str, "error_type": str, "message": str}
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from langchain_core.messages import HumanMessage, SystemMessage
from core.llm_usage import token_usage
from eval.baselines.zero_shot import _build_system_prompt
from eval.prediction import parse_prediction


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

SYSTEM_PROMPT = _build_system_prompt(KNOWN_SERVICES,
    "Semantic top-k SOP documents are supplied. No dependency topology. "
    "Optionally return matched_sop as the name of the most relevant retrieved SOP.")


@dataclass
class VectorRAGResult:
    root_cause: str | None
    blast_radius: list[str]
    matched_sop: str
    retrieved_sops: list[str]       # names of SOPs retrieved by vector search
    confidence: str
    reasoning: str
    tokens_used: int | None
    latency_s: float
    retrieval_latency_s: float      # just the FAISS lookup time
    raw_response: str
    error: Optional[str] = None
    outcome: str = "root"


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
                "Is the graph populated? (run python -m graph.scripts.init_graph)"
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
            f"OBSERVED HEALTH SNAPSHOT:\n{json.dumps(alert.get('observations', {}), sort_keys=True)}\n\n"
            f"OBSERVATION AGE SECONDS:\n{json.dumps(alert.get('observation_age_seconds', {}), sort_keys=True)}\n\n"
            f"RETRIEVED SOPs (semantic search, top {k}):\n{sop_block}\n\n"
            f"Identify the root cause and blast radius. Respond with JSON only."
        )

        # ── LLM call ──────────────────────────────────────────────────────
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]

        t_llm_start = time.perf_counter()
        try:
            llm = self._get_llm()
            response = llm.invoke(messages)
            latency_s = round(time.perf_counter() - t_llm_start, 3)
        except Exception as exc:
            return VectorRAGResult(
                root_cause=None,
                blast_radius=[],
                matched_sop="",
                retrieved_sops=retrieved_names,
                confidence="low",
                reasoning="LLM call failed",
                tokens_used=None,
                latency_s=round(time.perf_counter() - t_llm_start, 3),
                retrieval_latency_s=retrieval_latency_s,
                raw_response="",
                error=str(exc),
                outcome="review",
            )

        raw = response.content
        tokens_used = token_usage(response)["total_tokens"]
        try:
            parsed = parse_prediction(raw, KNOWN_SERVICES)
        except (ValueError, TypeError, AttributeError) as exc:
            return VectorRAGResult(
                root_cause=None,
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
                outcome="review",
            )
        return VectorRAGResult(
            root_cause=parsed["root_cause"],
            blast_radius=parsed.get("blast_radius", []),
            matched_sop=parsed.get("matched_sop", ""),
            retrieved_sops=retrieved_names,
            confidence=parsed.get("confidence", "low"),
            reasoning=parsed.get("reasoning", ""),
            tokens_used=tokens_used,
            latency_s=latency_s,
            retrieval_latency_s=retrieval_latency_s,
            raw_response=raw,
            outcome=parsed["outcome"],
        )

    # ── Lazy singletons ───────────────────────────────────────────────────

    def _get_encoder(self) -> SentenceTransformer:
        if self._encoder is None:
            self._encoder = SentenceTransformer(EMBEDDING_MODEL)
        return self._encoder

    def _get_llm(self):
        if self._llm is not None:
            return self._llm

        from agent.nodes.reasoner import _get_llm
        self._llm = _get_llm()
        return self._llm
