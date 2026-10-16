import json
from pathlib import Path

from packages.info.conditions import evaluate_conditions


def test_condition_oracle_is_shared_with_browser_and_unknown_is_never_null():
    cases = json.loads(Path("tests/fixtures/conditions.json").read_text(encoding="utf-8"))
    for case in cases:
        failed = evaluate_conditions(case["conditions"], case["entityKey"], case["values"])
        assert (not failed) is case["expected"], case["name"]
        if failed:
            assert set(failed[0]) == {"condition", "actual", "message"}
