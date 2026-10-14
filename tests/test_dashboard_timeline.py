from dashboard.components.agent_log import build_timeline


def test_live_decision_includes_recorded_usage():
    timeline = build_timeline({"attempts": [{"decision": "execute"}], "tokens_used": 800})
    assert timeline[3]["detail"] == "execute  (800 tokens)"


def test_missing_usage_is_not_reported_as_zero():
    timeline = build_timeline({})
    assert timeline[3]["detail"] == "Historical decision not recorded"


def test_measured_zero_usage_is_preserved():
    timeline = build_timeline({"tokens_used": 0})
    assert timeline[3]["detail"] == "Historical decision not recorded  (0 tokens)"
