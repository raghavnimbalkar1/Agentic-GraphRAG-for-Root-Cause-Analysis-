"""One synthetic inference request; never connects to the graph or executor."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from agent.nodes.reasoner import SYSTEM_PROMPT, _build_prompt, _get_llm
from core import settings
from core.llm_usage import token_usage


def check() -> dict:
    candidate = "Redis_Config_Reset_SOP"
    state = {
        "alert_service": "frontend",
        "alert_error_type": "HTTP_503",
        "alert_message": "Synthetic connection test: Redis policy differs from the approved baseline.",
        "root_cause_node": "redis-cart",
        "dependency_chain": ["redis-cart", "cartservice", "checkoutservice", "frontend"],
        "traversal_depth": 3,
        "current_trigger": "CONFIG_DRIFT",
        "candidate_skills": [{
            "name": candidate,
            "risk_level": "LOW",
            "description": "Restores the approved Redis maxmemory-policy after measured configuration drift.",
        }],
        "attempt_count": 0,
        "max_attempts": 1,
    }
    report = {
        "check": "synthetic_llm_connection_only",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": settings.llm_provider.value,
        "model": settings.llm_model,
        "remediation_executed": False,
        "passed": False,
    }
    try:
        # Direct invocation intentionally avoids the agent's retry and execution loop.
        response = _get_llm().invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=_build_prompt(state)),
        ])
        decision = json.loads(response.content)
        valid = (
            isinstance(decision, dict)
            and decision.get("action") == "execute"
            and decision.get("chosen_skill") == candidate
            and all(isinstance(decision.get(k), str) and decision[k].strip()
                    for k in ("reason", "root_cause_explanation"))
        )
        report.update(usage=token_usage(response), passed=bool(valid))
        if valid:
            report.update(action=decision["action"], chosen_skill=candidate)
        else:
            report["error"] = "Model response did not satisfy the synthetic decision check."
    except Exception as exc:
        # Provider exception text can contain request details; do not print it.
        report["error_type"] = type(exc).__name__
        report["http_status"] = getattr(exc, "status_code", None)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    report = check()
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
