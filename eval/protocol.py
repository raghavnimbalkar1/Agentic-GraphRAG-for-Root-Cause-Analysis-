"""Matched localization controls and an explicit per-run model-call budget."""

from copy import deepcopy

from eval.controlled import load_fixture


def matched_fixture() -> dict:
    fixture = deepcopy(load_fixture())
    fixture.update(schema_version=3, kind="matched_localisation")
    names = sorted({name for edge in fixture["edges"] for name in edge})
    for scenario in fixture["scenarios"]:
        scenario.update(block="legacy_positive", truth_outcome="root")
    blast = ["cartservice", "checkoutservice", "frontend", "loadgenerator"]
    for specificity in ("generic", "specific"):
        for depth, service in ((1, "cartservice"), (2, "checkoutservice"),
                               (3, "frontend"), (4, "loadgenerator")):
            fixture["scenarios"].append({
                "id": f"matched-{specificity}-d{depth}", "block": "matched_depth",
                "alert_specificity": specificity,
                "alert": {"service": service, "error_type": "DEP_ERROR",
                          "message": "Dependency operation failed." if specificity == "generic"
                          else "Dependency operation failed; Redis rejected a write."},
                "observations": {"redis-cart": "OOM_KILLED"},
                "truth_root": "redis-cart", "truth_outcome": "root",
                "truth_potential_blast": blast, "depth": depth,
            })
    fixture["scenarios"].append({
        "id": "cpu-multiple-sops", "block": "selection_control", "depth": 1,
        "alert": {"service": "frontend", "error_type": "DEP_ERROR",
                  "message": "Dependency operation failed."},
        "observations": {"adservice": "HIGH_CPU"}, "truth_root": "adservice",
        "truth_outcome": "root", "truth_potential_blast": ["frontend", "loadgenerator"],
    })
    controls = [
        ("negative-healthy", {}, {}, "none", "frontend"),
        ("negative-unreachable", {"adservice": "HIGH_CPU"}, {}, "none", "cartservice"),
        ("negative-unknown", {"redis-cart": "UNKNOWN"}, {}, "review", "frontend"),
        ("negative-stale", {"redis-cart": "OOM_KILLED"}, {"redis-cart": 120}, "review", "frontend"),
        ("negative-ambiguous", {"redis-cart": "OOM_KILLED", "adservice": "HIGH_CPU"},
         {}, "review", "frontend"),
    ]
    for identifier, observations, ages, outcome, service in controls:
        fixture["scenarios"].append({
            "id": identifier, "block": "negative", "depth": None,
            "alert": {"service": service, "error_type": "DEP_ERROR",
                      "message": "Dependency operation failed."},
            "observations": observations, "observation_age_seconds": ages,
            "truth_root": None, "truth_outcome": outcome, "truth_potential_blast": [],
        })
    for scenario in fixture["scenarios"]:
        scenario["observations"] = {name: scenario["observations"].get(name, "HEALTHY") for name in names}
        ages = scenario.get("observation_age_seconds", {})
        scenario["observation_age_seconds"] = {name: ages.get(name, 0) for name in names}
    fixture["provenance"] = (
        "Controlled complete snapshots; omitted legacy statuses expanded explicitly to HEALTHY. "
        "Matched blocks hold Redis observations, error type and message constant within specificity. "
        "Truth is a reviewed dependency-leaf attribution policy, not proof of causal discovery."
    )
    return fixture


def select_scenarios(fixture: dict, identifiers: list[str] | None) -> dict:
    if not identifiers:
        return fixture
    selected = set(identifiers)
    known = {case["id"] for case in fixture["scenarios"]}
    if selected - known:
        raise ValueError("Unknown scenarios: " + ", ".join(sorted(selected - known)))
    return {**fixture, "scenarios": [case for case in fixture["scenarios"] if case["id"] in selected]}


def check_call_budget(planned: int, maximum: int) -> None:
    if maximum < 0 or planned > maximum:
        raise ValueError(f"Run needs {planned} model calls; --max-calls permits {maximum}. "
                         "Select fewer scenarios/repetitions or explicitly raise the budget.")
