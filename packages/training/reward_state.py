"""Bounded data expressions over the immutable lw.reward-state/1 field layout."""

from dataclasses import dataclass
from types import MappingProxyType

SCHEMA = "lw.reward-state/1"
LIMIT = 10000.0
STATE = MappingProxyType(
    {
        "base_position_world": ("vector3", "m"),
        "base_orientation_world": ("quaternion", "xyzw"),
        "base_linear_velocity_world": ("vector3", "m/s"),
        "base_angular_velocity_world": ("vector3", "rad/s"),
        "base_linear_velocity_body": ("vector3", "m/s"),
        "base_angular_velocity_body": ("vector3", "rad/s"),
        "target_position_world": ("vector3", "m"),
        "prop_position_world": ("vector3", "m"),
        "prop_orientation_world": ("quaternion", "xyzw"),
        "command": ("vector3", "m/s,m/s,rad/s"),
        "episode_time": ("scalar", "s"),
    }
)
KEYED = frozenset(("prop_position_world", "prop_orientation_world"))
ARITY = MappingProxyType(
    {
        "add": 2,
        "sub": 2,
        "mul": 2,
        "div": 2,
        "neg": 1,
        "abs": 1,
        "norm": 1,
        "dot": 2,
        "component": 2,
        "clamp": 3,
        "gaussian": 1,
        "lt": 2,
        "le": 2,
        "gt": 2,
        "ge": 2,
        "eq": 2,
        "and": 2,
        "or": 2,
        "not": 1,
        "where": 3,
    }
)
NUMERIC = frozenset(("scalar", "vector3"))


@dataclass(frozen=True)
class Expression:
    path: str
    op: str
    shape: str
    value: object = None
    args: tuple = ()


def compile_expression(raw, expected="scalar", path="reward"):
    count = 0

    def visit(node, location, depth):
        nonlocal count
        count += 1

        def fail(message):
            raise ValueError(f"{location}: {message}")

        if depth > 12:
            fail("expression exceeds depth 12")
        if count > 128:
            fail("expression exceeds 128 nodes")
        if type(node) is bool:
            return Expression(location, "constant", "boolean", node)
        if type(node) in (float, int):
            if not -LIMIT <= node <= LIMIT:
                fail("constant must be finite and within the amplitude limit")
            return Expression(location, "constant", "scalar", float(node))
        if not isinstance(node, dict):
            fail("expected a number, boolean, state reference or operator")
        if "state" in node:
            name = node["state"]
            if not isinstance(name, str) or name not in STATE:
                fail("unknown reward state field")
            fields = {"state", "key"} if name in KEYED else {"state"}
            key = node.get("key")
            if (
                set(node) != fields
                or name in KEYED
                and (not isinstance(key, str) or not key.startswith("/World/Lab/") or len(key) > 256)
            ):
                fail("invalid state reference fields")
            return Expression(location, "state", STATE[name][0], (name, key))
        op = node.get("op")
        if not isinstance(op, str) or op not in ARITY:
            fail("unknown reward operator")
        values = node.get("args")
        if set(node) != {"op", "args"} or not isinstance(values, list) or len(values) != ARITY[op]:
            fail(f"{op} expects {ARITY[op]} ordered operands")
        args = tuple(visit(value, f"{location}.args[{i}]", depth + 1) for i, value in enumerate(values))
        shapes = tuple(arg.shape for arg in args)
        shape = shapes[0]
        if op in ("and", "or", "not"):
            if any(arg != "boolean" for arg in shapes):
                fail(f"{op} expects boolean operands")
        elif op in ("lt", "le", "gt", "ge", "eq"):
            if shapes != ("scalar", "scalar"):
                fail(f"{op} expects scalar operands")
            shape = "boolean"
        elif op == "where":
            if shape != "boolean" or shapes[1] != shapes[2]:
                fail("where expects a boolean condition and matching branches")
            shape = shapes[1]
        elif op in ("dot", "norm"):
            if any(arg != "vector3" for arg in shapes):
                fail(f"{op} expects vector3 operands")
            shape = "scalar"
        elif op == "component":
            offset = args[1].value
            if (
                shape not in ("vector3", "quaternion")
                or args[1].op != "constant"
                or shapes[1] != "scalar"
                or (offset not in range(3 if shape == "vector3" else 4))
            ):
                fail("component expects a vector and a fixed in-range index")
            shape = "scalar"
        elif op == "clamp":
            if shape not in NUMERIC or shapes[1:] != ("scalar", "scalar"):
                fail("clamp expects a number and two scalar bounds")
        elif any(arg not in NUMERIC for arg in shapes):
            fail(f"{op} expects scalar or vector3 operands")
        elif "vector3" in shapes:
            shape = "vector3"
        return Expression(location, op, shape, args=args)

    result = visit(raw, path, 1)
    if result.shape != expected:
        raise ValueError(f"{path}: expression expects {expected} result, got {result.shape}")
    return result


def state_references(expression):
    if expression.op == "state":
        yield expression
    for argument in expression.args:
        yield from state_references(argument)


def validate_scene_references(expression, props):
    for reference in state_references(expression):
        name, key = reference.value
        if key is not None and key not in props:
            raise ValueError(f"{reference.path}: {name} unknown Scene rigid body {key}")


def evaluate(expression, state, template, *, reject_nonfinite=False, arrays=None):
    import operator

    if arrays is None:
        import torch

        arrays = torch

    unary = {
        "neg": operator.neg,
        "abs": arrays.abs,
        "norm": lambda value: arrays.sqrt((value * value).sum(-1)),
        "gaussian": lambda value: arrays.exp(-(value * value)),
        "not": arrays.logical_not,
    }
    binary = {
        "add": operator.add,
        "sub": operator.sub,
        "mul": operator.mul,
        "div": operator.truediv,
        "dot": lambda left, right: (left * right).sum(-1),
        "lt": operator.lt,
        "le": operator.le,
        "gt": operator.gt,
        "ge": operator.ge,
        "eq": operator.eq,
        "and": arrays.logical_and,
        "or": arrays.logical_or,
    }

    def visit(node):
        if node.op == "constant":
            return arrays.full_like(
                template, node.value, dtype=arrays.bool if node.shape == "boolean" else None
            )
        if node.op == "state":
            value = state(*node.value)
        elif node.op == "component":
            value = visit(node.args[0])[..., int(node.args[1].value)]
        else:
            values = [visit(child) for child in node.args]
            if node.shape in ("vector3", "quaternion"):
                values = [
                    value[..., None] if child.shape in ("scalar", "boolean") else value
                    for child, value in zip(node.args, values, strict=True)
                ]
            if node.op == "clamp":
                value = arrays.minimum(arrays.maximum(values[0], values[1]), values[2])
            elif node.op == "where":
                value = arrays.where(*values)
            elif node.op in unary:
                value = unary[node.op](*values)
            else:
                value = binary[node.op](*values)
        if node.shape != "boolean":
            if reject_nonfinite and not arrays.isfinite(value).all().item():
                raise ValueError(f"{node.path}: {node.op} produced a non-finite value")
            clean = arrays.nan_to_num(value, nan=0.0, posinf=LIMIT, neginf=-LIMIT)
            value = arrays.clip(clean, -LIMIT, LIMIT)
        return value

    return visit(expression)
