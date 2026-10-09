"""Raw-trial metrics with explicit missing values and failure denominators."""

import math
import statistics


def blast_f1(predicted, truth) -> float:
    p, t = set(predicted or []), set(truth or [])
    return 2 * len(p & t) / (len(p) + len(t)) if p or t else 1.0


def distribution(values) -> dict:
    measured = [float(value) for value in values
                if value is not None and math.isfinite(float(value))]
    return {"n": len(measured), "mean": statistics.mean(measured) if measured else None,
            "std": statistics.stdev(measured) if len(measured) > 1 else (0.0 if measured else None)}


def score_prediction(prediction: dict, truth: dict) -> dict:
    failed = bool(prediction.get("error"))
    return {**prediction, "root_correct": not failed and prediction.get("root") == truth["truth_root"],
            "potential_blast_f1": 0.0 if failed else blast_f1(prediction.get("potential_blast"),
                                                            truth["truth_potential_blast"])}


def summarize(trials: list[dict]) -> dict:
    systems = sorted({trial["system"] for trial in trials})
    output = {}
    for system in systems:
        rows = [trial for trial in trials if trial["system"] == system]
        output[system] = {
            "n_attempted": len(rows), "n_failed": sum(bool(row.get("error")) for row in rows),
            "root_accuracy": sum(row["root_correct"] for row in rows) / len(rows),
            "potential_blast_f1": distribution(row["potential_blast_f1"] for row in rows),
            "localisation_seconds": distribution(row.get("latency_s") for row in rows),
            "tokens": distribution(row.get("tokens") for row in rows),
        }
    return output
