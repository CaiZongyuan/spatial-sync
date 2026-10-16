from packages.info.diff import PropertyDiff


def test_rate_limit_sends_last_pending_value_and_deadband_compares_last_sent_value():
    diff = PropertyDiff({"rpm": {"deadband": 10, "maxRateHz": 2}})
    assert diff.update({"rpm": 0}, 0) == {"rpm": 0}
    assert diff.update({"rpm": 20}, 0.1) == {}
    assert diff.update({"rpm": 30}, 0.2) == {}
    assert diff.update({}, 0.5) == {"rpm": 30}
    assert diff.update({"rpm": 35}, 1) == {}
    assert diff.update({"rpm": 41}, 1.1) == {"rpm": 41}
