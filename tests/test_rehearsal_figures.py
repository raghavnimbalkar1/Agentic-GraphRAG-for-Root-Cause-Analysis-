from copy import deepcopy
import json
from pathlib import Path

import pytest

from eval.plot_rehearsals import rehearsal_rows


SOURCE = Path(__file__).parents[1] / "eval/results/verified/20261014/development_rehearsals.json"


def test_rehearsal_figure_preserves_failures_and_cleanup_distinction():
    rows = rehearsal_rows(json.loads(SOURCE.read_text()))
    assert len(rows) == 6
    assert rows[0]["resolution_status"] == "RESOLVED"
    assert rows[0]["recovery_verified"] is False
    assert rows[-1]["recovery_verified"] is True
    assert rows[-1]["overall_rehearsal_passed"] is False


@pytest.mark.parametrize("field,value", [("tokens_used", None), ("handling_seconds", float("nan")),
                                        ("handling_seconds", -1), ("recovery_verified", None)])
def test_rehearsal_figure_rejects_missing_or_invalid_measurements(field, value):
    data = json.loads(SOURCE.read_text())
    data["trials"][0][field] = value
    with pytest.raises(ValueError):
        rehearsal_rows(data)


def test_rehearsal_figure_rejects_duplicate_incidents():
    data = json.loads(SOURCE.read_text())
    data["trials"].append(deepcopy(data["trials"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        rehearsal_rows(data)
