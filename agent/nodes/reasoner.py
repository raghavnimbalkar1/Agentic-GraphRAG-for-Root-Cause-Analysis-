"""
agent/nodes/reasoner.py

Layer 3: Cognitive Processing

The LLM receives the context injected by retriever.py and makes one
decision: execute, skip, or escalate.

Key design constraints:
    - The LLM sees ONLY the current skill node's description + the alert
      context. It does NOT see the full graph, all skills, or raw logs.
      This is Progressive Context Injection — the graph did the filtering.

    - Output is strictly validated as JSON. If the LLM returns anything
      other than {"action": "...", "reason": "..."}, we retry once then
      fall back to "execute" with a warning (fail-open for research purposes).

    - Temperature = 0. We want deterministic decisions, not creative ones.

LLM provider is controlled by settings.llm_provider:
    "openai"    → GPT-4o (best tool-calling reliability)
    "anthropic" → Claude 3.5 Sonnet
    "ollama"    → Llama 3.1:8b (local, zero API cost — current dev default)
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from core import get_logger, settings
from core.exceptions import LLMParseError
from agent.state import AgentState

log = get_logger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an autonomous SRE (Site Reliability Engineering) agent.
Your job is to perform root cause analysis and decide whether to execute
a remediation script to fix a live infrastructure failure.

You will receive:
1. The alert that triggered the investigation
2. The identified root cause service and dependency chain
3. ONE specific SOP (Standard Operating Procedure) to evaluate

You must respond with ONLY valid JSON in this exact format:
{"action": "execute", "reason": "brief explanation"}

Valid action values:
- "execute"   → run this SOP script — it matches the failure pattern
- "skip"      → this SOP is not relevant to the current failure
- "escalate"  → the failure is beyond automated remediation

Rules:
- Respond ONLY with the JSON object. No preamble, no markdown, no explanation outside the JSON.
- Keep "reason" under 50 words.
- When in doubt between execute and skip, choose execute."""

# ── LLM factory ──────────────────────────────────────────────────────────

def _get_llm() -> BaseChatModel:
    """Returns the configured LLM client based on settings.llm_provider."""
    provider = settings.llm_provider.value

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.llm_model,
            temperature=0,
            api_key=settings.openai_api_key,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.llm_model,
            temperature=0,
            api_key=settings.anthropic_api_key,
        )
    
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            temperature=0,
            google_api_key=settings.google_api_key,
        )


    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=settings.llm_model,
            base_url=settings.ollama_base_url,
            temperature=0,
    )


    raise ValueError(f"Unknown LLM provider: {provider}")


# ── Prompt builder ────────────────────────────────────────────────────────

def _build_prompt(state: AgentState) -> str:
    """
    Builds the human message. Only injects what the retriever found —
    one skill node's context plus the alert summary.
    """
    previous = state.get("execution_history", [])
    prev_summary = "None" if not previous else "\n".join(
        f"  - {r.skill_name}: exit_code={r.exit_code}, "
        f"success={r.success}, stdout={r.stdout[:200]!r}"
        for r in previous[-2:]   # only last 2 attempts to keep context small
    )

    return f"""ALERT SUMMARY:
  Alert service:     {state['alert_service']}
  Error type:        {state['alert_error_type']}
  Message:           {state['alert_message']}

ROOT CAUSE ANALYSIS (from graph traversal):
  Root cause node:   {state['root_cause_node']}
  Dependency chain:  {' → '.join(state['dependency_chain'])}
  Hops to root:      {state['traversal_depth']}

AVAILABLE SOP:
  Name:        {state['current_skill']}
  Description: {state['current_description']}
  Script:      {state['current_script']}
  Risk level:  {state.get('current_risk_level', 'unknown')}

PREVIOUS ATTEMPTS:
{prev_summary}

Attempt number: {state['attempt_count'] + 1} of {state['max_attempts']}

Respond with JSON only: {{"action": "execute"|"skip"|"escalate", "reason": "..."}}"""


# ── LLM decision node ─────────────────────────────────────────────────────

def llm_decide(state: AgentState) -> AgentState:
    """
    Ask the LLM to evaluate the current SOP and return execute/skip/escalate.
    """
    # If retriever found no skill, escalate immediately — no LLM call needed
    if not state.get("current_skill"):
        log.warning("no_skill_available_escalating",
                    root_cause=state.get("root_cause_node"),
                    visited=state.get("visited_skills"))
        return {
            **state,
            "llm_decision": "escalate",
            "llm_reason":   "No matching skill found in Skill Graph for this failure pattern.",
        }

    llm = _get_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=_build_prompt(state)),
    ]

    log.info(
        "llm_reasoning",
        provider=settings.llm_provider.value,
        model=settings.llm_model,
        skill=state["current_skill"],
        attempt=state["attempt_count"] + 1,
    )

    # Retry once on parse failure. Track tokens across all attempts so the
    # running total in state reflects the true cost of this reasoning step.
    tokens_accumulated = 0

    for attempt in range(2):
        try:
            response  = llm.invoke(messages)
            raw_text  = response.content.strip()

            # Accumulate token usage (Gemini returns a plain dict)
            um = getattr(response, "usage_metadata", None)
            if um:
                if isinstance(um, dict):
                    tokens_accumulated += um.get("total_tokens", 0) or (
                        um.get("input_tokens", 0) + um.get("output_tokens", 0)
                    )
                else:
                    tokens_accumulated += getattr(um, "total_tokens", 0) or (
                        getattr(um, "input_tokens", 0) + getattr(um, "output_tokens", 0)
                    )

            log.debug("llm_tokens", tokens_this_call=tokens_accumulated,
                      total_so_far=state.get("tokens_used", 0) + tokens_accumulated)

            # Strip markdown code fences if LLM wraps in ```json ... ```
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()

            parsed = json.loads(raw_text)
            action = parsed.get("action", "").lower()
            reason = parsed.get("reason", "")

            if action not in ("execute", "skip", "escalate"):
                raise LLMParseError(raw_text)

            log.info(
                "llm_decision_made",
                action=action,
                reason=reason,
                skill=state["current_skill"],
            )

            return {
                **state,
                "llm_decision": action,
                "llm_reason":   reason,
                "tokens_used":  state.get("tokens_used", 0) + tokens_accumulated,
            }

        except (json.JSONDecodeError, LLMParseError) as e:
            log.warning("llm_parse_failed", attempt=attempt + 1,
                        error=str(e), raw=raw_text[:200])
            if attempt == 1:
                # Fail-open: execute rather than stall the pipeline
                log.warning("llm_parse_failed_falling_back_to_execute")
                return {
                    **state,
                    "llm_decision": "execute",
                    "llm_reason":   "LLM returned unparseable output — defaulting to execute.",
                    "tokens_used":  state.get("tokens_used", 0) + tokens_accumulated,
                }

        except Exception as e:
            log.error("llm_call_failed", error=str(e))
            return {
                **state,
                "llm_decision": "escalate",
                "llm_reason":   f"LLM call failed: {e}",
                "tokens_used":  state.get("tokens_used", 0) + tokens_accumulated,
            }