"""The shared-event cohort must detect loss, including loss of its first record."""

import pytest

from tools.bench_newton_info import digest, match_cohort

EXPECTED = [b"accepted:request-1", b"running:request-1", b"succeeded:request-1"]


def test_native_cohort_retains_every_expected_message_with_only_outside_prefix_and_tail():
    prefix, checksum = match_cohort([b"before-cursor", *EXPECTED, b"after-cursor"], EXPECTED)
    assert prefix == 1 and checksum == digest(EXPECTED)


@pytest.mark.parametrize(
    "messages",
    [
        EXPECTED[1:],
        [EXPECTED[0], EXPECTED[2], b"later-message"],
        [EXPECTED[0], EXPECTED[1], EXPECTED[1], EXPECTED[2]],
        [EXPECTED[0], EXPECTED[2], EXPECTED[1]],
        [EXPECTED[0], *EXPECTED],
    ],
)
def test_native_cohort_detects_missing_first_middle_duplicate_and_reordered_records(messages):
    with pytest.raises(AssertionError):
        match_cohort(messages, EXPECTED)
