import pytest

from packages.agent_rsi.notebooks import remaining_gap


@pytest.mark.parametrize(
    "reference,candidate,comparable,expected,reason",
    [
        (40, 73 + 1 / 3, True, 5 / 9, None),
        (40, 40, True, 0, None),
        (80, 40, True, -2, None),
        (100, 100, True, None, "reference_at_ceiling"),
        (40, 90, False, None, "not_comparable"),
    ],
)
def test_agent_remaining_gap_uses_the_known_100_point_ceiling(
    reference, candidate, comparable, expected, reason
):
    value, basis = remaining_gap(reference, candidate, comparable=comparable)
    assert value == pytest.approx(expected) if expected is not None else value is None
    assert basis == reason
