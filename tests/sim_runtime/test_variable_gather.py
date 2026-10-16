"""One model-indexed filter must survive periodic target changes and full captures."""

import numpy as np
import pytest

wp = pytest.importorskip("warp")
pytestmark = pytest.mark.newton

from packages.sim_runtime.transform_extractor import TransformExtractor


def test_cpu_variable_targets_read_one_body_and_full_capture_is_one_fresh_sample():
    wp.init()
    initial = np.array(
        [[1, 0, 1, 0, 0, 0, 1], [10, 0, 1, 0, 0, 0, 1], [20, 0, 1, 0, 0, 0, 1], [30, 0, 1, 0, 0, 0, 1]],
        dtype=np.float32,
    )
    state = wp.array(initial, dtype=wp.transform, device="cpu")
    extractor = TransformExtractor(4, device="cpu")
    try:
        np.testing.assert_array_equal(extractor.capture(state, block=True).result(5), initial)
        narrow = initial.copy()
        narrow[3, 0] = 300
        state.assign(narrow)
        one = extractor.capture(state, body_indices=[3], block=True).result(5)
        np.testing.assert_array_equal(one, [[300, 0, 1, 0, 0, 0, 1]])
        assert extractor.last_metrics["readbackBodies"] == 1
        assert extractor.last_metrics["readbackBytes"] == 28
        # A different actual body occupies dense slot zero. Its invalid sample must
        # retain body1's value, never body3's previously returned dense-slot value.
        narrow[1, 0] = np.nan
        state.assign(narrow)
        retained = extractor.capture(state, body_indices=[1], block=True).result(5)
        np.testing.assert_array_equal(retained, [[10, 0, 1, 0, 0, 0, 1]])
        assert extractor.filter.consecutive_invalid.tolist() == [0, 1, 0, 0]
        current = np.array(
            [
                [101, 0, 2, 0, 0, 0, 1],
                [110, 0, 2, 0, 0, 0, 1],
                [120, 0, 2, 0, 0, 0, 1],
                [130, 0, 2, 0, 0, 0, 1],
            ],
            dtype=np.float32,
        )
        state.assign(current)
        full = extractor.capture(state, block=True).result(5)
        np.testing.assert_array_equal(full, current)
        assert extractor.last_metrics["readbackBodies"] == 4
    finally:
        extractor.close()
