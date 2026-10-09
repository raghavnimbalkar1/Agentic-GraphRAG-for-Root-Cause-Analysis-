"""Paired candidate-context ablation; configured model calls are opt-in."""

import argparse
import json

from core.audit import write_json_atomic
from core.llm_usage import token_usage
from eval.controlled import load_fixture
from eval.research_benchmark import metadata, result_path


def is_off_target(chosen: str | None, root: str, applicability: dict[str, set[str]]) -> bool:
    return bool(chosen and root not in applicability.get(chosen, set()))


def parse_decision(raw: str) -> dict:
    decision = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    if not isinstance(decision, dict) or decision.get("action") not in {"execute", "skip", "escalate"}:
        raise ValueError("Invalid decision action")
    chosen = decision.get("chosen_skill")
    if ((decision["action"] == "execute" and not isinstance(chosen, str))
            or (decision["action"] != "execute" and chosen is not None)
            or not isinstance(decision.get("reason"), str)
            or not isinstance(decision.get("root_cause_explanation"), str)):
        raise ValueError("Invalid decision schema")
    return decision


def run(reps: int):
    from agent.nodes.reasoner import _get_llm, _build_prompt, SYSTEM_PROMPT
    from graph.graph_client import GraphClient
    from langchain_core.messages import HumanMessage, SystemMessage
    client = GraphClient()
    rows = client._run(
        "MATCH (k:Skill)-[:APPLIES_TO]->(s:Service) "
        "RETURN k.name AS name, k.description AS description, k.risk_level AS risk_level, "
        "k.script_path AS script_path, k.script_type AS script_type, "
        "k.trigger_condition AS trigger_condition, collect(DISTINCT s.name) AS applies_to "
        "ORDER BY k.name")
    applicability = {row["name"]: set(row["applies_to"]) for row in rows}
    llm = _get_llm()
    output = {"metadata": metadata("paired_context_ablation"),
              "methodology": "Same model, root, condition, alert, chain, and prompt template. "
                             "Only candidate-library breadth changes; pair order alternates. No execution.",
              "library": rows, "pairs": []}
    path = result_path("ablation_v2")
    try:
        for scenario in load_fixture()["scenarios"]:
            root = scenario["truth_root"]
            condition = scenario["observations"][root]
            candidates = [row for row in rows if root in row["applies_to"] and row["trigger_condition"] == condition]
            if not candidates:
                output["pairs"].append({"scenario_id": scenario["id"], "error": "No applicable skill"})
                continue
            base = {"alert_service": scenario["alert"]["service"],
                    "alert_error_type": scenario["alert"]["error_type"], "alert_message": scenario["alert"]["message"],
                    "root_cause_node": root, "dependency_chain": [root, scenario["alert"]["service"]],
                    "traversal_depth": scenario["depth"], "current_trigger": condition,
                    "execution_history": [], "attempt_count": 0, "max_attempts": 1}
            # Use the exact Q1 chain, in the same isolated fixture used by localization.
            from eval.controlled import ControlledGraph
            fixture = load_fixture()
            with ControlledGraph(client, fixture["edges"], scenario["observations"]) as graph:
                base["dependency_chain"] = graph.localise(scenario["alert"]).dependency_chain
            for rep in range(reps):
                pair = {"scenario_id": scenario["id"], "rep": rep, "root": root, "condition": condition}
                order = [("candidate_context", candidates), ("full_library", rows)]
                if rep % 2:
                    order.reverse()
                for treatment, skills in order:
                    usage, raw = None, None
                    try:
                        response = llm.invoke([SystemMessage(content=SYSTEM_PROMPT),
                                               HumanMessage(content=_build_prompt({**base, "candidate_skills": skills}))])
                        raw = response.content
                        usage = token_usage(response)
                        decision = parse_decision(raw)
                        chosen = decision.get("chosen_skill") if decision.get("action") == "execute" else None
                        pair[treatment] = {"n_candidates": len(skills), "raw_response": raw,
                                           "usage": usage, "decision": decision,
                                           "off_target": is_off_target(chosen, root, applicability),
                                           "outside_candidate_allowlist": bool(chosen and chosen not in {row["name"] for row in candidates})}
                    except Exception as exc:
                        pair[treatment] = {"error": f"{type(exc).__name__}: {exc}",
                                           "usage": usage, "raw_response": raw}
                output["pairs"].append(pair)
                write_json_atomic(path, output)
    finally:
        client.close()
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--reps", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.reps <= 100:
        parser.error("--reps must be 1..100")
    if not args.with_llm:
        print("Fixtures validated. Use --with-llm to measure paired decisions; no calls made.")
        load_fixture()
        return
    print(run(args.reps))


if __name__ == "__main__":
    main()
