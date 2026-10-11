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
    positive = truth["truth_root"] is not None
    expected = truth.get("truth_outcome", "root")
    correct = not failed and prediction.get("root") == truth["truth_root"]
    outcome = prediction.get("outcome", "root" if prediction.get("root") is not None else "review")
    return {**prediction, "root_correct": correct if positive else None,
            "decision_correct": correct and outcome == expected,
            "potential_blast_f1": (0.0 if failed else blast_f1(prediction.get("potential_blast"),
                                   truth["truth_potential_blast"])) if positive else None}


def summarize(trials: list[dict]) -> dict:
    systems = sorted({trial["system"] for trial in trials})
    output = {}
    for system in systems:
        rows = [trial for trial in trials if trial["system"] == system]
        positive = [row for row in rows if row["truth_root"] is not None]
        negative = [row for row in rows if row["truth_root"] is None]
        output[system] = {
            "n_attempted": len(rows), "n_failed": sum(bool(row.get("error")) for row in rows),
            "n_positive": len(positive), "n_negative": len(negative),
            "root_accuracy": sum(row["root_correct"] for row in positive) / len(positive) if positive else None,
            "negative_control_accuracy": sum(row["decision_correct"] for row in negative) / len(negative) if negative else None,
            "decision_accuracy": sum(row.get("decision_correct", row.get("root_correct", False)) for row in rows) / len(rows),
            "potential_blast_f1": distribution(row["potential_blast_f1"] for row in rows),
            "localisation_seconds": distribution(row.get("latency_s") for row in rows),
            "tokens": distribution(row.get("tokens") for row in rows),
        }
    return output
