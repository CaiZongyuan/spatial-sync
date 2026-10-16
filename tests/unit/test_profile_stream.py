import pytest

from tools.profile_stream import corpus, verify


def test_frozen_corpus_oracle_rejects_wrong_ids_positions_and_nonfinite_output():
    rows = corpus(35)[0]
    assert rows[0] == (0, 0, 0, 0.55, 0, 0, 0, 1)
    verify(rows, rows)
    for component, invalid in ((0, 100), (1, 0.1), (4, float("nan"))):
        changed = [list(row) for row in rows]
        changed[0][component] = invalid
        with pytest.raises(AssertionError):
            verify(changed, rows)
