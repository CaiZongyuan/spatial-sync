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
