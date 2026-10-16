import threading

import numpy as np
import pytest

wp = pytest.importorskip("warp")
pytestmark = pytest.mark.newton

from packages.sim_runtime.transform_extractor import TransformExtractor


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda:0", marks=pytest.mark.gpu)])
@pytest.mark.parametrize("indices", [None, [5, 1, 7]])
def test_readback_owns_sample_until_callback_finishes_and_state_can_continue(device, indices):
    wp.init()
    if device.startswith("cuda") and not wp.is_cuda_available():
        pytest.skip("CUDA unavailable")
    first = np.tile([1, 2, 3, 0, 0, 0, 1], (16, 1)).astype(np.float32)
    first[:, 0] = np.arange(16) * 2 + 1
    second = first + np.array([3, 3, 3, 0, 0, 0, 0], dtype=np.float32)
    body_q = wp.array(first, dtype=wp.transform, device=device)
    callback_entered, release_callback = threading.Event(), threading.Event()

    def hold_sample(_):
        callback_entered.set()
        assert release_callback.wait(5)

    extractor = TransformExtractor(16, device=device, body_indices=indices)
    try:
        one = extractor.capture(body_q, on_complete=hold_sample)
        assert callback_entered.wait(5)
        body_q.assign(second)
        two = extractor.capture(body_q)
        body_q.assign(first * np.array([1, 1, 10, 1, 1, 1, 1], dtype=np.float32))
        # Both immutable samples still own their staging/host slots.
        assert extractor.capture(body_q) is None
        release_callback.set()
        expected = np.arange(16) if indices is None else indices
        np.testing.assert_array_equal(one.result(timeout=5), first[expected])
        np.testing.assert_array_equal(two.result(timeout=5), second[expected])
        assert extractor.dropped_captures == 1
    finally:
        release_callback.set()
        extractor.close()


@pytest.mark.parametrize("indices", [[1, 1], [-1], [16], [1.5], [True]])
def test_gather_rejects_invalid_model_indices(indices):
    with pytest.raises(ValueError, match="indices"):
        TransformExtractor(16, device="cpu", body_indices=indices)


def test_runner_targets_use_manifest_identity_not_dense_indices():
    from types import SimpleNamespace

    from packages.sim_runtime.runner import _body_targets

    nodes = [
        SimpleNamespace(kind="group"),
        SimpleNamespace(kind="body", entityKey="/z", streamId=103, debug={"newtonBodyIndex": 5}),
        SimpleNamespace(kind="body", entityKey="/a", streamId=44, debug={"newtonBodyIndex": 1}),
    ]
    assert _body_targets(SimpleNamespace(nodes=nodes)) == ([1, 5], [44, 103])
