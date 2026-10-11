"""Render prompt-character controls from identified raw no-model records."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory

from core.audit import write_json_atomic


def prompt_character_rows(data: dict) -> list[dict]:
    if data["metadata"]["kind"] != "selection_policy" or data["call_budget"]["attempted"] != 0:
        raise ValueError("This figure requires an identified no-model policy run")
    if not data.get("completed") or not data.get("pairs"):
        raise ValueError("Incomplete or empty control record")
    rows = []
    for size in sorted({pair["distractors"] for pair in data["pairs"]}):
        pairs = [pair for pair in data["pairs"] if pair["distractors"] == size]
        row = {"synthetic_distractors": size, "n_cases": len(pairs)}
        for treatment in ("candidate_context", "full_library"):
            if any(pair[treatment].get("inference") != "not_requested" for pair in pairs):
                raise ValueError("Unexpected model treatment in a no-model record")
            row[treatment] = statistics.mean(pair[treatment]["prompt_characters"] for pair in pairs)
        rows.append(row)
    return rows


def render(source: Path, destination: Path) -> Path:
    raw = source.read_bytes()
    data = json.loads(raw)
    rows = prompt_character_rows(data)
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="rca-plot-") as cache:
        os.environ.setdefault("MPLCONFIGDIR", cache)
        os.environ.setdefault("XDG_CACHE_HOME", cache)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4.6), layout="constrained")
        x = [row["synthetic_distractors"] for row in rows]
        ax.plot(x, [row["candidate_context"] for row in rows], marker="o", color="#177846",
                label="Graph-filtered candidates")
        ax.plot(x, [row["full_library"] for row in rows], marker="s", color="#2459a6",
                label="Full catalog plus synthetic skills")
        ax.set(xlabel="Synthetic irrelevant SOPs added", ylabel="Mean prompt characters",
               title="Candidate Context Size Control", xticks=x, ylim=(0, None))
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.2)
        ax.legend(frameon=False)
        n_cases = len({pair["scenario_id"] for pair in data["pairs"]})
        fig.supxlabel(f"No model calls; fixed valid candidate sets across {n_cases} controlled cases.", fontsize=9)
        image = destination / "prompt_characters.png"
        fig.savefig(image, dpi=180)
        plt.close(fig)
    write_json_atomic(destination / "prompt_characters_manifest.json", {
        "source_file": source.name, "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_commit": data["metadata"]["git_commit"], "rows": rows,
        "metric": "Prompt characters, not model tokens, billing or selection accuracy",
        "image": image.name,
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
