"""Map failures and public-call/reset observation keep the trainer authoritative."""

import copy
import json
from types import SimpleNamespace

import pytest

from apps.node.view_bridge import TrainingViewBridge, validate_mapping
from apps.node.view_relay import latest


def fixture_mapping():
    inputs = {
        "job": {"taskContentHash": "a" * 64, "sceneContentHash": "b" * 64, "assets": []},
        "view": {"bodyTable": [{"entityKey": "/robot"}, {"entityKey": "/tube"}]},
    }
    mapping = {
        "schema": "lw.training-view-map/1", **inputs["job"], "solver": "fixture-only",
        "environments": [
            {
                "envIndex": environment, "world": environment,
                "cloneTransform": [5 + environment, 2, 0, 0, 0, 0, 1],
                "bodies": [
                    {"entityKey": key, "bodyIndex": 2 * environment + index,
                     "bodyLabel": f"clone{environment}/{index}", "localTransform": [0, 0, 0, 0, 0, 0, 1]}
                    for index, key in enumerate(("/robot", "/tube"))
                ],
            }
            for environment in range(4)
        ],
    }
    labels = [f"clone{i}/{j}" for i in range(4) for j in range(2)]
    return inputs, mapping, labels, [i for i in range(4) for _ in (0, 1)]


@pytest.mark.parametrize("mutation", ["task", "asset", "closure", "label", "world", "transform", "duplicate"])
def test_mapping_rejects_wrong_pins_incomplete_lab_and_assumed_clone_membership(mutation):
    inputs, mapping, labels, worlds = fixture_mapping()
    assert validate_mapping(mapping, inputs, labels, worlds, 1024) == [0, 1, 2, 3]
    mapping = copy.deepcopy(mapping)
    first = mapping["environments"][0]
    if mutation == "task":
        mapping["taskContentHash"] = "c" * 64
    elif mutation == "asset":
        mapping["assets"] = [{"id": "asset/other@1", "contentHash": "d" * 64}]
    elif mutation == "closure":
        first["bodies"].pop()
    elif mutation == "label":
        first["bodies"][0]["bodyLabel"] = "guessed-prefix/0"
    elif mutation == "world":
        first["world"] = 3
    elif mutation == "transform":
        first["cloneTransform"][-1] = 2
    else:
        mapping["environments"][1]["envIndex"] = 0
    with pytest.raises(ValueError):
        validate_mapping(mapping, inputs, labels, worlds, 1024)


def test_public_step_once_preserves_identity_exception_and_reset_epoch_between_samples():
    calls, copied, samples = [], [], []

    class Mask:
        device = "producer-device"

        def __init__(self, values):
            self.values = values

        def __or__(self, other):
            return Mask([a or b for a, b in zip(self.values, other.values, strict=True)])

        def index_select(self, axis, selected):
            copied.append((axis, selected))
            return Mask([self.values[index] for index in selected])

        def to(self, device):
            assert device == "cpu" and len(self.values) == 4
            return self

        def tolist(self):
            return self.values

    count = 0
    result = ({}, object(), Mask([False, True, False, False] + [True] * 1020), Mask([False] * 1024), {})

    class Env:
        def step(self, action):
            nonlocal count
            calls.append(action)
            count += 3
            return result

        def reset(self):
            calls.append("reset")
            return result

    env = Env()
    env.sim = SimpleNamespace(get_physics_step_count=lambda: count)
    bridge = TrainingViewBridge(None, {})
    bridge.env, bridge.selected = env, [0, 1, 2, 3]
    bridge.epochs, bridge.physics_count, bridge.sim_time, bridge.dt = [0] * 4, 0, 0, 1 / 60
    bridge.torch = SimpleNamespace(long=int, tensor=lambda value, **kw: value)
    bridge._sample = lambda: samples.append((bridge.epochs.copy(), bridge.sim_time))
    for name in ("step", "reset"):
        bridge.originals[name] = (False, getattr(env, name))
        setattr(env, name, bridge._observe(name, getattr(env, name)))
    assert env.step("action") is result and calls == ["action"]
    assert bridge.epochs == [0, 1, 0, 0] and bridge.sim_time == 0.05
    assert copied == [(0, [0, 1, 2, 3])]
    assert env.reset() is result and calls == ["action", "reset"]
    assert bridge.epochs == [1, 2, 1, 1] and bridge.sim_time == 0.05
    error = RuntimeError("original physics error")

    def fail():
        raise error

    with pytest.raises(RuntimeError) as caught:
        bridge._observe("step", fail)()
    assert caught.value is error and len(samples) == 2
    bridge.__exit__()
    assert "step" not in vars(env) and "reset" not in vars(env)


def test_recovered_ipc_cannot_claim_live_ownership(tmp_path):
    (tmp_path / "latest.json").write_text(json.dumps({"hello": "old owned run"}))
    capability = SimpleNamespace(is_set=lambda: False)
    owned = SimpleNamespace(view_active=capability, view_directory=tmp_path)
    assert latest(owned) is None


def test_view_teardown_cannot_mask_the_original_training_exception():
    bridge = TrainingViewBridge(None, {})
    error = RuntimeError("real trainer failure")

    def unavailable():
        raise OSError("owned view IPC deletion unavailable")

    bridge.writer = SimpleNamespace(close=unavailable)
    with pytest.raises(RuntimeError) as caught:
        try:
            raise error
        finally:
            bridge.__exit__()
    assert caught.value is error
