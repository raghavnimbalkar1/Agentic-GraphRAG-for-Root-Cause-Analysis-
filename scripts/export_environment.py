"""Generate a credential-free installed-version snapshot and pip constraints."""

import json
from importlib.metadata import distributions
from pathlib import Path
import platform
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    packages = {}
    for package in distributions():
        name = package.metadata.get("Name", "")
        if re.fullmatch(r"[A-Za-z0-9_.-]+", name) and name.lower() != "agentic-graphrag-rca":
            packages[name.lower().replace("_", "-")] = package.version
    packages = dict(sorted(packages.items()))
    snapshot = {"python": platform.python_version(), "system": platform.system(),
                "machine": platform.machine(), "packages": packages,
                "scope": "Installed local environment; not a cross-platform lock or live validation"}
    (ROOT / "docs" / "runtime_snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    (ROOT / "constraints-tested.txt").write_text(
        "# Installed local version snapshot. Constraints do not install optional packages.\n"
        + "\n".join(f"{name}=={version}" for name, version in packages.items()) + "\n")
    print(f"Recorded {len(packages)} package versions without credentials or local install URLs.")


if __name__ == "__main__":
    main()
