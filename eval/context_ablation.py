"""Paired candidate-context ablation; configured model calls are opt-in."""

import argparse
import json
import random
import time

from core.audit import write_json_atomic
from core.llm_usage import token_usage
from eval.research_benchmark import metadata, result_path
from eval.protocol import check_call_budget, matched_fixture, select_scenarios


def selection_fixture(identifiers: list[str] | None = None) -> dict:
    fixture = matched_fixture()
    fixture["scenarios"] = [case for case in fixture["scenarios"]
                            if case["block"] in {"legacy_positive", "selection_control"}]
    return select_scenarios(fixture, identifiers)


def risk_ranked_choice(candidates: list[dict]) -> str | None:
    rank = {"LOW": 0, "MEDIUM": 1}
    allowed = [row for row in candidates if row["risk_level"] in rank]
    return min(allowed, key=lambda row: (rank[row["risk_level"]], row["name"]))["name"] if allowed else None


def context_library(rows: list[dict], distractors: int) -> list[dict]:
    if not 0 <= distractors <= 1000:
        raise ValueError("Distractor count must be 0..1000")
    library = [dict(row) for row in rows]
    for index in range(distractors):
        library.append({"name": f"Synthetic_Unrelated_{index:04d}", "description": "Synthetic unrelated test skill",
                        "risk_level": "LOW", "trigger_condition": "SYNTHETIC_ONLY",
                        "applies_to": ["synthetic-unrelated-service"], "script_path": None,
                        "script_type": None})
    # Give both contexts the same explicit applicability/trigger fields without changing runtime prompts.
    return [{**row, "description": f"{row['description']} Applies to: {','.join(sorted(row['applies_to']))}. "
                                   f"Trigger: {row['trigger_condition']}."} for row in library]


def summarize_pairs(pairs: list[dict]) -> dict:
    from eval.metrics import distribution
    summary = {}
    for size in sorted({pair["distractors"] for pair in pairs}):
        block = [pair for pair in pairs if pair["distractors"] == size]
        treatments = {}
        for name in ("candidate_context", "full_library"):
            rows = [pair[name] for pair in block if name in pair]
            if not rows:
                continue
            treatments[name] = {
                "n_attempted": len(rows), "n_failed": sum(bool(row.get("error")) for row in rows),
                "valid_selection_rate": sum(row.get("valid_selection", False) for row in rows) / len(rows),
                "off_target_attempts": sum(row.get("off_target", False) for row in rows),
                "outside_allowlist_attempts": sum(row.get("outside_candidate_allowlist", False) for row in rows),
                "tokens": distribution((row.get("usage") or {}).get("total_tokens") for row in rows),
                "prompt_characters": distribution(row["prompt_characters"] for row in rows),
                "latency_seconds": distribution(row["latency_s"] for row in rows),
            }
        summary[str(size)] = {"n_pairs": len(block), "treatments": treatments,
                              "risk_ranked_valid_rate": sum(pair.get("risk_ranked_choice") is not None
                                                            for pair in block) / len(block)}
    return summary


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


def run(reps: int, distractors: list[int] | None = None, identifiers: list[str] | None = None,
        max_calls: int = 30, policy_only: bool = False, seed: int = 1262251354):
    from agent.nodes.reasoner import _get_llm, _build_prompt, SYSTEM_PROMPT
    from graph.graph_client import GraphClient
    from langchain_core.messages import HumanMessage, SystemMessage
    fixture = selection_fixture(identifiers)
    sizes = sorted(set(distractors if distractors is not None else [0]))
    if not 1 <= reps <= 100 or not sizes or any(not 0 <= size <= 1000 for size in sizes):
        raise ValueError("Invalid repetitions or distractor sizes")
    planned = 0 if policy_only else 2 * len(fixture["scenarios"]) * reps * len(sizes)
    check_call_budget(planned, max_calls)
    client = GraphClient()
    path = result_path("selection_policy_v3" if policy_only else "ablation_v3")
    try:
        rows = client._run(
            "MATCH (k:Skill)-[:APPLIES_TO]->(s:Service) "
            "RETURN k.name AS name, k.description AS description, k.risk_level AS risk_level, "
            "k.script_path AS script_path, k.script_type AS script_type, "
            "k.trigger_condition AS trigger_condition, collect(DISTINCT s.name) AS applies_to "
            "ORDER BY name")
        applicability = {row["name"]: set(row["applies_to"]) for row in rows}
        llm = None if policy_only else _get_llm()
        output = {"metadata": metadata("selection_policy" if policy_only else "paired_context_ablation"),
                  "methodology": "Same model, root, condition, alert, chain and prompt template. "
                                 "Applicability and trigger are explicit in both contexts. Pair order alternates; "
                                 "seeded skill order avoids always placing the valid candidate first. "
                                 "Distractors are synthetic, unrelated and never executable. No remediation. "
                                 "Risk-ranked policy tests admissibility, not optimal remediation or LLM benefit.",
                  "seed": seed, "library": rows, "fixture": fixture, "distractor_counts": sizes,
                  "call_budget": {"maximum": max_calls, "planned": planned, "attempted": 0}, "pairs": []}
        for scenario in fixture["scenarios"]:
            root = scenario["truth_root"]
            condition = scenario["observations"][root]
            candidates = [row for row in rows if root in row["applies_to"] and row["trigger_condition"] == condition]
            if not candidates:
                raise ValueError(f"No applicable skill for {scenario['id']}")
            base = {"alert_service": scenario["alert"]["service"],
                    "alert_error_type": scenario["alert"]["error_type"], "alert_message": scenario["alert"]["message"],
                    "root_cause_node": root, "dependency_chain": [root, scenario["alert"]["service"]],
                    "traversal_depth": scenario["depth"], "current_trigger": condition,
                    "execution_history": [], "attempt_count": 0, "max_attempts": 1}
            # Use the exact Q1 chain, in the same isolated fixture used by localization.
            from eval.controlled import ControlledGraph
            with ControlledGraph(client, fixture["edges"], scenario["observations"]) as graph:
                base["dependency_chain"] = graph.localise(scenario["alert"]).dependency_chain
            allowed_names = {row["name"] for row in candidates}
            for size in sizes:
                for rep in range(reps):
                    library = context_library(rows, size)
                    random.Random(f"{seed}:{scenario['id']}:{rep}").shuffle(library)
                    filtered = [row for row in library if row["name"] in allowed_names]
                    pair = {"scenario_id": scenario["id"], "rep": rep, "root": root,
                            "condition": condition, "distractors": size,
                            "n_library": len(library), "n_valid_candidates": len(filtered),
                            "risk_ranked_choice": risk_ranked_choice(filtered)}
                    order = [("candidate_context", filtered), ("full_library", library)]
                    if rep % 2:
                        order.reverse()
                    for treatment, skills in order:
                        prompt = _build_prompt({**base, "candidate_skills": skills})
                        item = {"n_candidates": len(skills), "presented_skills": [row["name"] for row in skills],
                                "prompt_characters": len(SYSTEM_PROMPT) + len(prompt),
                                "raw_response": None, "usage": None, "latency_s": None}
                        pair[treatment] = item
                        if policy_only:
                            item["inference"] = "not_requested"
                            continue
                        started = time.perf_counter()
                        try:
                            output["call_budget"]["attempted"] += 1
                            response = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
                            item.update(raw_response=response.content, usage=token_usage(response))
                            decision = parse_decision(response.content)
                            chosen = decision.get("chosen_skill") if decision["action"] == "execute" else None
                            item.update(decision=decision, valid_selection=chosen in allowed_names,
                                        off_target=is_off_target(chosen, root, applicability),
                                        outside_candidate_allowlist=bool(chosen and chosen not in allowed_names))
                        except Exception as exc:
                            item["error"] = f"{type(exc).__name__}: {exc}"
                        finally:
                            item["latency_s"] = time.perf_counter() - started
                    output["pairs"].append(pair)
                    if not policy_only:
                        output["summary"] = summarize_pairs(output["pairs"])
                    write_json_atomic(path, output)
        output["completed"] = True
        write_json_atomic(path, output)
    finally:
        client.close()
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--policy-only", action="store_true", help="Measure deterministic policy and prompt sizes, with zero model calls")
    parser.add_argument("--distractors", type=int, nargs="+", default=[0], help="Synthetic irrelevant library sizes, e.g. 0 50 200")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--max-calls", type=int, default=30)
    parser.add_argument("--seed", type=int, default=1262251354)
    args = parser.parse_args()
    if not 1 <= args.reps <= 100:
        parser.error("--reps must be 1..100")
    if args.with_llm and args.policy_only:
        parser.error("--policy-only and --with-llm are mutually exclusive")
    if not args.with_llm and not args.policy_only:
        print("Fixtures validated. Use --with-llm to measure paired decisions; no calls made.")
        selection_fixture(args.scenario)
        return
    print(run(args.reps, args.distractors, args.scenario, args.max_calls, args.policy_only, args.seed))


if __name__ == "__main__":
    main()
