"""
eval/baselines/zero_shot.py

Baseline B1: Zero-Shot LLM

The simplest possible approach:
  - Feed the raw alert JSON directly to the LLM
  - Ask it to identify the root cause and affected services
  - No graph traversal, no SOP retrieval, no structured knowledge

This represents what a team gets from a "naive" LLM integration —
asking GPT/Gemini directly about an alert with no additional context.
Token cost is low, but accuracy on multi-hop cascades is poor because
the LLM has no knowledge of this specific deployment's dependency graph.

Interface:
    baseline = ZeroShotBaseline()
    result = baseline.resolve(alert_dict)

    alert_dict format (matches eval/scenarios.json "alert" field):
        {"service": str, "error_type": str, "message": str}

    result.root_cause: str            — predicted root cause service name
    result.blast_radius: list[str]    — predicted affected services
    result.tokens_used: int           — total tokens consumed
    result.latency_s: float           — wall-clock seconds for the LLM call
    result.error: str | None          — set if call failed
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from core.config import settings

# The 12 known services in the Online Boutique stack.
# Zero-shot LLM must pick a root cause from this set.
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

When given an alert, you must:
1. Identify the ROOT CAUSE service — the single service whose failure triggered the cascade
2. Identify the BLAST RADIUS — all services that are affected (directly or indirectly)

IMPORTANT: You have NO access to dependency graphs, topology data, or runbooks.
Make your best inference based on the alert content and general microservice knowledge.

Respond ONLY with valid JSON in this exact format:
{{
  "root_cause": "<service_name_from_the_list_above>",
  "blast_radius": ["<service1>", "<service2>"],
  "confidence": "high|medium|low",
  "reasoning": "<one sentence explanation>"
}}

Do not include services in blast_radius that are clearly unaffected.
Do not add any text outside the JSON object."""


@dataclass
class ZeroShotResult:
    root_cause: str
    blast_radius: list[str]
    confidence: str
    reasoning: str
    tokens_used: int
    latency_s: float
    raw_response: str
    error: Optional[str] = None


class ZeroShotBaseline:
    """
    Single LLM call with only the raw alert as context.
    No graph, no retrieval, no structured knowledge.
    """

    def __init__(self) -> None:
        self._llm = None   # lazy-initialised on first call

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

    def _build_prompt(self, alert: dict) -> str:
        return (
            f"ALERT:\n"
            f"  Alerting service: {alert.get('service', 'unknown')}\n"
            f"  Error type:       {alert.get('error_type', 'unknown')}\n"
            f"  Message:          {alert.get('message', '')}\n\n"
            f"Identify the root cause and blast radius. Respond with JSON only."
        )

    def resolve(self, alert: dict) -> ZeroShotResult:
        """
        Run zero-shot inference on the alert.

        Args:
            alert: dict with keys "service", "error_type", "message"

        Returns:
            ZeroShotResult — even on LLM failure, always returns a result
            (error field will be set, root_cause defaults to the alerting service)
        """
        llm = self._get_llm()
        prompt = self._build_prompt(alert)
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]

        t_start = time.perf_counter()
        try:
            response = llm.invoke(messages)
            latency_s = round(time.perf_counter() - t_start, 3)
        except Exception as exc:
            return ZeroShotResult(
                root_cause=alert.get("service", "unknown"),
                blast_radius=[],
                confidence="low",
                reasoning="LLM call failed",
                tokens_used=0,
                latency_s=round(time.perf_counter() - t_start, 3),
                raw_response="",
                error=str(exc),
            )

        raw = response.content.strip()

        # Extract token usage (Gemini returns usage_metadata)
        tokens_used = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            um = response.usage_metadata
            if isinstance(um, dict):
                tokens_used = um.get("total_tokens", 0) or (
                    um.get("input_tokens", 0) + um.get("output_tokens", 0)
                )
            else:
                tokens_used = getattr(um, "total_tokens", 0) or (
                    getattr(um, "input_tokens", 0) + getattr(um, "output_tokens", 0)
                )

        # Parse JSON — strip markdown fences if present
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
            return ZeroShotResult(
                root_cause=alert.get("service", "unknown"),
                blast_radius=[],
                confidence="low",
                reasoning="JSON parse failed",
                tokens_used=tokens_used,
                latency_s=latency_s,
                raw_response=raw,
                error=f"JSON parse error: {exc}",
            )

        return ZeroShotResult(
            root_cause=parsed.get("root_cause", alert.get("service", "unknown")),
            blast_radius=parsed.get("blast_radius", []),
            confidence=parsed.get("confidence", "low"),
            reasoning=parsed.get("reasoning", ""),
            tokens_used=tokens_used,
            latency_s=latency_s,
            raw_response=raw,
        )
