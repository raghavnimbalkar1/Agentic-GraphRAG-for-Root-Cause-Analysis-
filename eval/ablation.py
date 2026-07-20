"""
eval/ablation.py — component ablation study.

Quantifies the contribution of two load-bearing design choices by removing each
and measuring the effect on the 21-scenario benchmark set.

  A1 — Infrastructure graph (Q1 traversal).
       full   = Q1 dependency-graph traversal to the deepest unhealthy node.
       ablated= no traversal; take the alerting service as the root (the naive
                default a topology-blind system falls back to).
       metric = root-cause accuracy, stratified by cascade depth. Deterministic;
                no LLM. (The intermediate "LLM guesses without the graph" point is
                the zero-shot baseline — ~62% — reported in the main benchmark.)

  A2 — Progressive Context Injection.
       full   = the LLM sees only the root's candidate SOP set (what Q2 returns).
       ablated= the LLM sees ALL skills in the library.
       metric = prompt tokens per decision (real LLM calls) + whether the ablated
                LLM strays to a SOP that does not apply to the root cause (the
                safety value the graph-as-allowlist adds).

Run:  python -m eval.ablation
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT_ROOT / "eval" / "results" / "ablation.json"

# Representative sample for the token/allowlist ablation (one per fault family,
# spanning depths) — keeps LLM calls modest while covering the space.
A2_SAMPLE_IDS = {"D1-oom", "D2-crash", "D3-conn", "D3-pool", "D4-oom", "D1-cpu"}


def _acc(results: list[bool]) -> float:
    return round(100 * sum(results) / len(results), 1) if results else 0.0


def main() -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    from core.config import settings
    from graph.graph_client import GraphClient
    from eval.benchmark_full import SCENARIOS
    import agent.nodes.reasoner as reasoner
    from langchain_core.messages import HumanMessage, SystemMessage

    gc = GraphClient()
    print(f"Ablation study · LLM {settings.llm_model} · {len(SCENARIOS)} scenarios\n")

    # ── A1: infrastructure-graph ablation (deterministic) ──────────────────
    full_by_depth = defaultdict(list)
    abl_by_depth = defaultdict(list)
    for sid, fault, cond, root, asvc, etype, msg in SCENARIOS:
        gc.update_service_status(root, cond, cond)
        try:
            q1 = gc.get_root_cause(asvc, etype)
        finally:
            gc.update_service_status(root, "HEALTHY", None)
        full_by_depth[q1.depth].append(q1.root_cause_node == root)
        abl_by_depth[q1.depth].append(asvc == root)   # no graph → guess the alert svc

    depths = sorted(full_by_depth)
    print("── A1: Infrastructure graph (root-cause accuracy %) ──")
    print(f"{'depth':>6} {'n':>3}  {'with graph':>11}  {'no graph':>9}")
    a1_rows = []
    for d in depths:
        f, a = _acc(full_by_depth[d]), _acc(abl_by_depth[d])
        n = len(full_by_depth[d])
        print(f"{d:>6} {n:>3}  {f:>10.0f}%  {a:>8.0f}%")
        a1_rows.append({"depth": d, "n": n, "with_graph": f, "no_graph": a})
    all_full = [x for v in full_by_depth.values() for x in v]
    all_abl = [x for v in abl_by_depth.values() for x in v]
    print(f"{'ALL':>6} {len(all_full):>3}  {_acc(all_full):>10.0f}%  {_acc(all_abl):>8.0f}%")

    # ── A2: Progressive Context Injection ablation (tokens + safety) ────────
    all_skills = gc._run(
        "MATCH (k:Skill) RETURN k.name AS name, k.description AS description, "
        "k.risk_level AS risk_level, k.script_path AS script_path, "
        "k.script_type AS script_type, k.trigger_condition AS trigger_condition")
    # name -> the service this SOP applies to (for the "strayed off-root" check)
    applies = {r["name"]: r["svc"] for r in gc._run(
        "MATCH (k:Skill)-[:APPLIES_TO]->(s:Service) "
        "RETURN k.name AS name, s.name AS svc")}

    def _state(sc, candidates):
        sid, fault, cond, root, asvc, etype, msg = sc
        return {
            "alert_service": asvc, "alert_error_type": etype, "alert_message": msg,
            "root_cause_node": root, "dependency_chain": [root, asvc],
            "traversal_depth": 2, "current_trigger": cond,
            "candidate_skills": candidates, "current_skill": candidates[0]["name"],
            "execution_history": [], "attempt_count": 0, "max_attempts": 5,
        }

    def _tokens(resp) -> int:
        um = getattr(resp, "usage_metadata", None) or {}
        if isinstance(um, dict):
            return um.get("total_tokens", 0) or (um.get("input_tokens", 0)
                                                 + um.get("output_tokens", 0))
        return getattr(um, "total_tokens", 0) or 0

    llm = reasoner._get_llm()
    print("\n── A2: Progressive Context Injection (tokens per decision) ──")
    print(f"{'scenario':10} {'candidates':>10} {'full ctx':>9} {'all-skills':>10} {'off-root?':>10}")
    a2_rows = []
    pci_tokens, abl_tokens, strayed = [], [], 0
    for sc in SCENARIOS:
        sid, fault, cond, root, asvc, etype, msg = sc
        if sid not in A2_SAMPLE_IDS:
            continue
        cands = gc.get_skills(root_node=root, error_type=cond, visited=[])
        cand_dicts = [{"name": s.name, "description": s.description,
                       "risk_level": s.risk_level, "script_path": s.script_path,
                       "script_type": s.script_type,
                       "trigger_condition": s.trigger_condition} for s in cands]
        if not cand_dicts:
            continue
        # full (PCI): candidate set only
        p_full = reasoner._build_prompt(_state(sc, cand_dicts))
        r_full = llm.invoke([SystemMessage(content=reasoner.SYSTEM_PROMPT),
                             HumanMessage(content=p_full)])
        t_full = _tokens(r_full)
        # ablated: all skills in context
        p_abl = reasoner._build_prompt(_state(sc, all_skills))
        r_abl = llm.invoke([SystemMessage(content=reasoner.SYSTEM_PROMPT),
                            HumanMessage(content=p_abl)])
        t_abl = _tokens(r_abl)
        # did the ablated LLM choose a SOP that does NOT apply to the root?
        try:
            chosen = json.loads(r_abl.content.strip().strip("`").lstrip("json").strip()
                                ).get("chosen_skill")
        except Exception:  # noqa: BLE001
            chosen = None
        off_root = bool(chosen and applies.get(chosen, root) != root)
        strayed += off_root
        pci_tokens.append(t_full); abl_tokens.append(t_abl)
        print(f"{sid:10} {len(cand_dicts):>10} {t_full:>9} {t_abl:>10} "
              f"{'YES '+str(chosen) if off_root else 'no':>10}")
        a2_rows.append({"id": sid, "n_candidates": len(cand_dicts),
                        "tokens_pci": t_full, "tokens_all_skills": t_abl,
                        "ablated_chose_off_root": off_root, "ablated_choice": chosen})

    mean_pci = round(statistics.mean(pci_tokens), 0) if pci_tokens else 0
    mean_abl = round(statistics.mean(abl_tokens), 0) if abl_tokens else 0
    print(f"\nMean tokens/decision:  PCI (candidate set) {mean_pci:.0f}  vs  "
          f"all-skills {mean_abl:.0f}   (+{mean_abl - mean_pci:.0f}, "
          f"{100*(mean_abl-mean_pci)/mean_pci:.0f}% more)" if mean_pci else "")
    print(f"Ablated LLM strayed to an off-root SOP in {strayed}/{len(a2_rows)} "
          f"sampled decisions — the graph-as-allowlist blocks exactly these.")

    out = {
        "llm": settings.llm_model, "n_scenarios": len(SCENARIOS),
        "A1_infra_graph": {
            "by_depth": a1_rows,
            "overall_with_graph": _acc(all_full),
            "overall_no_graph": _acc(all_abl),
            "note": "no-graph = alerting service as root; the LLM-guess-without-graph "
                    "point is the zero-shot baseline (~62%) in benchmark_full.json",
        },
        "A2_progressive_injection": {
            "samples": a2_rows,
            "mean_tokens_pci": mean_pci,
            "mean_tokens_all_skills": mean_abl,
            "ablated_off_root_count": strayed,
            "ablated_sample_size": len(a2_rows),
        },
    }
    RESULTS.write_text(json.dumps(out, indent=2))
    print(f"\nWritten: {RESULTS}")


if __name__ == "__main__":
    main()
