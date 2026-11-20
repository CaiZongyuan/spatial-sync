"""Revise reward weights from an exact declarative Task package."""

import io
import tempfile
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import Field

from packages.library.archive import extract_package, invalid
from packages.library.common import LibraryError, content_hash, document
from packages.training.declarative import Data, Hash
from packages.training.inputs import pack_package


class RewardWeights(Data):
    contentHash: Hash
    layerA: list[float] = Field(max_length=16)
    layerB: list[Annotated[float, Field(ge=-100, le=100)]] = Field(max_length=16)


def revise_reward_weights(tasks, identity, body):
    source = tasks.show(identity)
    if source["contentHash"] != body.contentHash:
        raise LibraryError("task_source_changed", "Task content hash differs from the selected source", 409)
    if source["task"].get("declarative") is None:
        raise LibraryError(
            "task_rewards_unsupported", "Reward editing requires a declarative Task", 422,
        )

    tasks.user_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reward-revision-", dir=tasks.user_root) as temporary:
        package = Path(temporary)
        extract_package(io.BytesIO(tasks.export(identity)), package)
        if content_hash(package) != body.contentHash:
            raise LibraryError("task_source_changed", "Task package changed during export", 409)

        task_path = package / "task.yaml"
        task = document(task_path)
        declarative = task["declarative"]
        layer_b = declarative.get("layerB")
        standard = declarative.get("rewards", [])
        expressions = layer_b.get("rewards", []) if layer_b is not None else []
        if len(standard) != len(body.layerA) or len(expressions) != len(body.layerB):
            raise invalid("Weight counts must match the selected reward lists", path="declarative.rewards")
        original_weights = [reward["weight"] for reward in standard + expressions]
        if original_weights == body.layerA + body.layerB:
            return {**source, "idempotent": True}

        updated = dict(declarative)
        if standard:
            updated["rewards"] = [
                row | {"weight": weight} for row, weight in zip(standard, body.layerA, strict=True)
            ]
        if expressions:
            updated["layerB"] = {**layer_b, "rewards": [
                row | {"weight": weight} for row, weight in zip(expressions, body.layerB, strict=True)
            ]}
        revised = yaml.safe_dump(task | {"declarative": updated}, sort_keys=False)
        task_path.write_text(revised, encoding="utf-8")
        return tasks.push(io.BytesIO(pack_package(package)))
