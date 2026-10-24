import json
import shutil

import pytest

from packages.info.evaluations import EvaluationStore, evaluation_decision, wilson_interval
from packages.library.common import BUILTIN_ROOT, LibraryError
from packages.library.suites import SuiteLibrary


@pytest.mark.parametrize(
    ("successes", "expected"),
    [
        (0, [0, 0.2775327998628892]),
        (5, [0.236593090512564, 0.7634069094874361]),
        (10, [0.7224672001371107, 1]),
    ],
)
def test_wilson_independent_known_ten_episode_examples(successes, expected):
    assert wilson_interval(successes, 10) == pytest.approx(expected)


def test_decision_uses_strict_lower_bound_and_no_reference_is_baseline():
    lower = wilson_interval(10, 10)[0]
    assert evaluation_decision(10, 10, lower) == "rejected"
    assert evaluation_decision(10, 10, lower - 0.01) == "accepted"
    assert evaluation_decision(0, 10) == "baseline"
    assert evaluation_decision(0, 10, split="test") == "test"
    assert wilson_interval(0, 0) is None
    with pytest.raises(ValueError):
        evaluation_decision(0, 0)
    for successes, count in ((True, 1), (-1, 2), (3, 2), (0, 1.5)):
        with pytest.raises(ValueError):
            wilson_interval(successes, count)


def test_public_suite_has_no_private_predicates_seeds_or_test_cases(tmp_path):
    library = SuiteLibrary({"builtin": BUILTIN_ROOT, "user": tmp_path})
    library.refresh()
    suite = library.resolve("suite/lab-demo-pick-place@1")
    public = json.dumps(suite.public())
    assert suite.public()["coverage"] == {"from": ["rack.A1"], "to": ["centrifuge.slot1"]}
    assert len(suite.public()["episodes"]) == 2
    for private in ("held-out-example", "4101", "4102", "9101", "private", "success", "when", "seeds"):
        assert private not in public
    with pytest.raises(LibraryError):
        library.resolve("suite/lab-demo-pick-place")


def test_store_is_append_only_and_recovers_commit_before_receipt(tmp_path):
    store = EvaluationStore(tmp_path)
    store.startup()
    record = {"evaluationId": "eval-a", "status": "running"}
    row = {
        "evaluationId": "eval-a",
        "suite": "suite/example@1",
        "key": {"source": "synthetic"},
        "score": 0.5,
        "decision": "baseline",
        "at": "2026-01-01T00:00:00Z",
    }
    store.save(record)
    store.append(row)
    scoreboard = next(store.root.rglob("scoreboard.jsonl"))
    original = scoreboard.read_bytes()
    store.append(row)
    assert scoreboard.read_bytes() == original
    with pytest.raises(ValueError):
        store.append(row | {"score": 1})
    restarted = EvaluationStore(tmp_path)
    restarted.startup()
    assert restarted.records["eval-a"]["status"] == "completed"
    assert restarted.reference({"source": "newton"}) is None
    assert restarted.reference({"source": "synthetic"}) is None
    assert restarted.rows == [row]  # Legacy score/decision remain intact, with unknown comparability.
    identity = {"environment": "observed-environment", "judge": "observed-judge"}
    known = row | {"evaluationId": "eval-b", "key": row["key"] | identity, "comparisonIdentity": identity}
    restarted.append(known)
    assert restarted.reference(known["key"]) == known
    assert restarted.rows[0] == row


def test_private_parser_diagnostics_and_changed_package_do_not_return_private_values(tmp_path):
    package = tmp_path / "suites/lab-demo-pick-place/1"
    shutil.copytree(BUILTIN_ROOT / "suites/lab-demo-pick-place/1", package)
    library = SuiteLibrary({"user": tmp_path})
    library.refresh()
    original = library.resolve("suite/lab-demo-pick-place@1")
    private = package / "private/seeds.yaml"
    private.write_text("train-one: private-canary-864209\n", encoding="utf-8")
    with pytest.raises(LibraryError) as changed:
        original.verify()
    assert changed.value.detail["code"] == "suite_changed"
    with pytest.raises(LibraryError) as malformed:
        library.refresh()
    assert malformed.value.detail == {"code": "invalid_suite", "message": "Invalid embodied suite package"}
    assert "private-canary" not in str(malformed.value.detail)
