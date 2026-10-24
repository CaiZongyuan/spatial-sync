"""Embodied evaluation statistics and append-only, platform-owned results."""

import json
import math
import os
import re
from copy import deepcopy
from pathlib import Path

from packages.library.transactions import durable_json


def wilson_interval(successes, episodes):
    if type(successes) is not int or type(episodes) is not int or not 0 <= successes <= episodes:
        raise ValueError("Counts must be integers with 0 <= successes <= episodes")
    if episodes == 0:
        return None
    z = 1.959963984540054
    rate, squared = successes / episodes, z * z
    denominator = 1 + squared / episodes
    centre = (rate + squared / (2 * episodes)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / episodes + squared / (4 * episodes**2)) / denominator
    return [max(0, centre - radius), min(1, centre + radius)]


def evaluation_decision(successes, episodes, reference=None, *, split="train"):
    interval = wilson_interval(successes, episodes)
    if interval is None:
        raise ValueError("A complete evaluation must contain episodes")
    if split == "test":
        return "test"
    if split != "train":
        raise ValueError("Unknown evaluation split")
    if reference is None:
        return "baseline"
    if not math.isfinite(reference) or not 0 <= reference <= 1:
        raise ValueError("Reference must be a success rate")
    return "accepted" if interval[0] > reference else "rejected"


class EvaluationStore:
    """Only the gateway writes these receipts; the API exposes projections."""

    def __init__(self, state_root, *, on_change=None):
        self.root = Path(state_root) / "evaluations"
        self.records = {}
        self.rows = []
        self.on_change = on_change

    def startup(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.rows = [
            json.loads(line)
            for scoreboard in self.root.glob("scoreboards/*/*/scoreboard.jsonl")
            for line in scoreboard.read_text(encoding="utf-8").splitlines()
        ]
        for path in self.root.glob("eval-*.json"):
            record = json.loads(path.read_bytes())
            row = next((row for row in self.rows if row["evaluationId"] == record["evaluationId"]), None)
            if row is not None:
                record.update(status="completed", result=row)
                durable_json(path, record)
            elif record["status"] in ("queued", "running"):
                record.update(status="failed", error={"code": "gateway_restarted"})
                if record.get("layer") == "agent":
                    for case in record["cases"]:
                        if case["status"] in ("starting", "running", "finishing"):
                            case.update(status="invalid", error={"code": "gateway_restarted"})
                    # External v1 only owns in-process Synthetic Sources; none can
                    # survive the gateway process. Grants are deliberately ephemeral.
                    record["cleanup"] = {"complete": True, "blocked": False}
                durable_json(path, record)
            self.records[record["evaluationId"]] = record

    def save(self, record):
        durable_json(self.root / (record["evaluationId"] + ".json"), record)
        self.records = self.records | {record["evaluationId"]: deepcopy(record)}
        if self.on_change is not None:
            self.on_change(self.records[record["evaluationId"]])

    def reference(self, key):
        return next(
            (
                row
                for row in reversed(self.rows)
                if row["key"] == key and row["decision"] in ("baseline", "accepted")
            ),
            None,
        )

    def append(self, row):
        previous = next((item for item in self.rows if item["evaluationId"] == row["evaluationId"]), None)
        if previous is not None:
            if previous != row:
                raise ValueError("Evaluation already has a different scoreboard row")
            return
        match = re.fullmatch(r"suite/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)", row["suite"])
        if match is None:
            raise ValueError("Scoreboard requires an exact suite version")
        directory = self.root / "scoreboards" / match[1] / match[2]
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "scoreboard.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.rows.append(row)
