import pytest

from packages.training.reward_state import compile_expression


def test_reward_expression_has_bounded_data_operators_and_typed_results():
    dot = {"op": "dot", "args": [{"state": "base_position_world"}, {"state": "target_position_world"}]}
    assert compile_expression(dot).shape == "scalar"
    with pytest.raises(ValueError, match=r"reward.args\[1\].*unknown"):
        compile_expression({"op": "add", "args": [1, {"op": "import", "args": []}]})
    with pytest.raises(ValueError, match="expects vector3 operands"):
        compile_expression({"op": "dot", "args": [1, 2]})
    with pytest.raises(ValueError, match="expects boolean"):
        compile_expression(dot, expected="boolean")
    with pytest.raises(ValueError, match="depth.*12"):
        expression = 1
        for _ in range(12):
            expression = {"op": "abs", "args": [expression]}
        compile_expression(expression)
    with pytest.raises(ValueError, match="128 nodes"):
        expression = 1
        for _ in range(7):
            expression = {"op": "add", "args": [expression, expression]}
        compile_expression(expression)


def test_reward_numeric_faults_use_the_gpu_free_evaluator():
    import numpy as np

    from packages.training.reward_state import evaluate

    template = np.zeros(2, dtype=np.float32)

    def no_fields(*_):
        raise AssertionError("Constants should not read reward state")

    infinity = compile_expression({"op": "div", "args": [1, 0]})
    indeterminate = compile_expression({"op": "div", "args": [0, 0]})
    with np.errstate(divide="ignore", invalid="ignore"):
        np.testing.assert_array_equal(evaluate(infinity, no_fields, template, arrays=np), [10000.0, 10000.0])
        np.testing.assert_array_equal(evaluate(indeterminate, no_fields, template, arrays=np), [0.0, 0.0])
        for expression in (infinity, indeterminate):
            with pytest.raises(ValueError, match=r"^reward: div produced a non-finite value$"):
                evaluate(expression, no_fields, template, arrays=np, reject_nonfinite=True)


def test_scene_reference_validation_reports_the_nested_reference_node():
    from packages.training.reward_state import validate_scene_references

    reference = {"state": "prop_position_world", "key": "/World/Lab/Missing"}
    compiled = compile_expression({"op": "add", "args": [1, {"op": "component", "args": [reference, 0]}]})
    with pytest.raises(ValueError, match=r"^reward\.args\[1\]\.args\[0\]: .* /World/Lab/Missing$"):
        validate_scene_references(compiled, {"/World/Lab/Ball"})


def test_layer_b_does_not_require_targets_for_non_target_state():
    from packages.training.declarative import LayerA

    task = {
        "baseBody": "/World/Lab/Robot/root",
        "episodeSeconds": 3.0,
        "rewards": [],
        "layerB": {
            "state": "lw.reward-state/1",
            "rewards": [
                {"weight": 1.0, "expression": {"state": "episode_time"}},
            ],
        },
    }
    assert LayerA.model_validate(task).targetSites is None
    task["layerB"]["rewards"][0]["expression"] = {
        "op": "norm",
        "args": [{"state": "target_position_world"}],
    }
    with pytest.raises(ValueError, match="Target state requires targetSites or curriculum"):
        LayerA.model_validate(task)
