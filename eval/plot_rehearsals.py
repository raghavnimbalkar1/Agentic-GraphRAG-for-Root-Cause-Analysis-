"""Plot development observations without implying a benchmark or success rate."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from core.audit import write_json_atomic


def rehearsal_rows(data: dict) -> list[dict]:
    if data.get("kind") != "development_rehearsals" or not data.get("trials"):
        raise ValueError("Expected a nonempty development-rehearsal export")
    rows = []
    seen = set()
    for trial in data["trials"]:
        incident = trial["alert_id"]
        if incident in seen:
            raise ValueError("Duplicate primary incident")
        seen.add(incident)
        duration = trial.get("handling_seconds")
        tokens = trial.get("tokens_used")
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            raise ValueError("Handling time must be a finite nonnegative measurement")
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            raise ValueError("Missing provider usage must not be plotted as zero")
        if not isinstance(trial.get("recovery_verified"), bool):
            raise ValueError("Independent recovery adjudication is required")
        rows.append({key: trial[key] for key in (
            "alert_id", "label", "handling_seconds", "tokens_used", "resolution_status",
            "recovery_verified", "overall_rehearsal_passed",
        )})
    return rows


def render(source: Path, destination: Path) -> Path:
    raw = source.read_bytes()
    data = json.loads(raw)
    rows = rehearsal_rows(data)
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="rca-rehearsal-plot-") as cache:
        os.environ.setdefault("MPLCONFIGDIR", cache)
        os.environ.setdefault("XDG_CACHE_HOME", cache)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch

        fig, (time_ax, token_ax) = plt.subplots(1, 2, figsize=(12.5, 5.4), sharey=True)
        colors = ["#147d75" if row["recovery_verified"] else "#bd3b45" for row in rows]
        positions = list(range(len(rows)))
        for ax, key, label in (
            (time_ax, "handling_seconds", "Agent handling time (seconds)"),
            (token_ax, "tokens_used", "Provider-reported total tokens"),
        ):
            values = [row[key] for row in rows]
            bars = ax.barh(positions, values, color=colors, height=0.57)
            ax.bar_label(bars, labels=[f"{v:.2f}" if key == "handling_seconds" else str(v) for v in values],
                         padding=5, fontsize=10)
            ax.set_xlabel(label, labelpad=12)
            ax.set_xlim(0, max(values) * 1.25)
            ax.spines[["top", "right", "left"]].set_visible(False)
            ax.tick_params(axis="y", length=0)
            ax.set_axisbelow(True)
            ax.grid(axis="x", color="#e7e9ec")
        time_ax.set_yticks(positions, [row["label"] for row in rows], fontsize=10)
        time_ax.invert_yaxis()
        fig.suptitle("Live Development Trials: Failures And Verified Recovery", fontsize=16, x=0.51, y=0.98)
        fig.legend(handles=[Patch(color="#bd3b45", label="Recovery not verified"),
                            Patch(color="#147d75", label="Recovery / mitigation verified")],
                   loc="upper center", bbox_to_anchor=(0.64, 0.91), ncol=2, frameon=False)
        fig.text(0.02, 0.06, "Six development incidents; code and policy changed between trials. Not a benchmark or success-rate estimate.", fontsize=10)
        fig.text(0.02, 0.02, "0.5-CPU mitigation passed, but baseline cleanup timed out. Handling excludes detection; tokens are not billing cost.", fontsize=10)
        fig.subplots_adjust(left=0.28, right=0.97, top=0.79, bottom=0.20, wspace=0.17)
        image = destination / "development_trials.png"
        fig.savefig(image, dpi=180, facecolor="white")
        plt.close(fig)
    write_json_atomic(destination / "development_trials_manifest.json", {
        "source_file": source.name, "source_sha256": hashlib.sha256(raw).hexdigest(),
        "scope": data["scope"], "rows": rows, "image": image.name,
    })
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(render(args.source, args.output))


if __name__ == "__main__":
    main()
