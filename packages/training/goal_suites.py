"""Publish one sealed embodied Suite with the first durable human approval."""

import hashlib
import itertools
import json
import math
import os
import secrets
import tempfile
from pathlib import Path

import yaml

from packages.info.plans import PlanError
from packages.info.schema_subset import json_equal
from packages.library.archive import destination_parent
from packages.library.common import LibraryError, content_hash, document
from packages.library.robot_asset import scene_robot_asset
from packages.library.structural import USDGraph
from packages.library.suites import load_suite
from packages.library.transactions import sync_directory
from packages.policies.observations import ObservationSpec
from packages.protocol.schema import MAX_SESSION_SEED
from packages.sim_runtime.trial import BUCKETS
from packages.training.declarative import trajectory_completion
from packages.training.schema import TrialPerturbation

PREFIX = "suite/goal-bound-"


def suite_identity(goal, implements):
    key = json.dumps([goal, implements], separators=(",", ":"))
    return PREFIX + hashlib.sha256(key.encode()).hexdigest()[:32] + "@1"


class GoalSuites:
    def __init__(self, manager):
        self.manager = manager

    def approved(self):
        for approval in tuple(self.manager.approvals.records.values()):
            suite = approval.details.get("goalSuite")
            if suite and approval.details.get("goalSuiteOrigin") == approval.approvalId and any(
                event.decision == "approve" and event.actor.kind == "user" for event in approval.decisions
            ):
                yield approval, suite

    def matching(self, identity):
        return next(
            ((approval, suite) for approval, suite in self.approved() if suite["id"] == identity), None
        )

    def frozen_for(self, goal, implements):
        return self.matching(suite_identity(goal, implements))

    def _package(self, approval_id):
        root = self.manager.library_assets.roots["user"]
        return destination_parent(root, (".goal-suites", approval_id))

    def load(self, approval, review):
        suite = load_suite(self._package(approval.approvalId), review["id"], "user")
        if suite.content_hash != review["contentHash"]:
            raise LibraryError("goal_suite_changed", "Frozen Goal Suite bytes differ", 409)
        return suite

    def prepare(self, plan, task_record):
        task = task_record["task"]
        if not task.get("declarative") or plan.program is not None:
            if any(row["goalId"] == plan.goalId and row["implements"] == task["implements"]
                   for _, row in self.approved()):
                raise PlanError("goal_suite_changed", "Use the Goal's declarative Task protocol")
            return None
        if not 30 <= plan.evaluation.episodes <= 5000:
            raise PlanError("invalid_goal_suite", "Select 30–5000 Goal Suite episodes per split", status=422)
        try:
            review = self._protocol(plan, task_record)
        except ValueError as exc:
            raise PlanError("invalid_goal_suite", str(exc), status=422) from exc
        frozen = self.matching(review["id"])
        if frozen:
            _, existing = frozen
            if {key: value for key, value in existing.items() if key != "contentHash"} != review:
                raise PlanError("goal_suite_changed", "Start a new Goal for changed evaluation inputs")
            return existing
        if review["id"] in self.manager.evaluations.suites.versions:
            raise PlanError("goal_suite_conflict", "Goal Suite identity already belongs to a published Suite")
        return review

    def _protocol(self, plan, task_record):
        from apps.node.usd_mjcf import derive_usd_mjcf

        task = task_record["task"]
        scene = self.manager.library.resolve(task["scene"], launch=True, source="newton")
        if task_record["sceneContentHash"] != scene.content_hash:
            raise ValueError("Task Scene bytes changed")
        payload = scene.runtime_payload()
        graph = USDGraph(payload["package"], payload["assets"], payload["expectedAssets"])
        stage = graph.compose(scene.metadata["stage"])
        asset = scene_robot_asset(scene, task["robot"], stage, graph) if stage and not graph.errors else None
        if asset is None or content_hash(Path(asset["path"])) != asset["contentHash"]:
            raise ValueError("Goal Suite requires the robot's exact calibrated USD Asset")
        asset_pin = {name: asset[name] for name in ("id", "contentHash")}
        if calibration := asset["files"].get("joint-calibration.json"):
            with open(calibration, "rb") as stream:
                asset_pin["calibrationSha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        config = plan.evaluation.perturbation or TrialPerturbation()
        package = self.manager.tasks._versions()[plan.task] / "package"
        spec = ObservationSpec.model_validate(document(package / task["observation"]))
        derived = derive_usd_mjcf(
            stage, prim_path=task["robot"], include_ground_plane=False, include_static_colliders=False
        )
        channels = dict(zip(spec.action.jointKeys, spec.action.units, strict=True))
        trial = None
        for joint in derived.joints:
            unit = "rad" if joint.kind == "hinge" else "m"
            amplitude = config.jointPositionRadians if unit == "rad" else config.jointPositionMeters
            lower, upper = joint.default_position - amplitude, joint.default_position + amplitude
            if (
                channels.get(joint.prim_path) == unit and joint.actuated
                and (not joint.limited or joint.lower <= lower <= upper <= joint.upper)
                and (2 * amplitude / BUCKETS) > max(abs(lower), abs(upper)) * 2**-23
            ):
                trial = {"jointKey": joint.prim_path, "unit": unit, "amplitude": amplitude}
                break
        if trial is None:
            raise ValueError("Goal Suite needs a controlled joint with room for distinct perturbations")
        base_params = task["episode"]["params"]
        choices = task["evaluation"]["params"]
        overrides = plan.evaluation.params or choices
        for key, values in overrides.items():
            permitted = choices.get(key, [base_params[key]] if key in base_params else [])
            if not values or any(not any(json_equal(value, item) for item in permitted) for value in values):
                raise ValueError("Goal Suite parameters must use the pinned Task coverage")
        if math.prod(map(len, overrides.values())) > plan.evaluation.episodes:
            raise ValueError("Goal Suite episode count must cover the requested parameter combinations")
        completion = trajectory_completion(task)
        return {
            "id": suite_identity(plan.goalId, task["implements"]),
            "goalId": plan.goalId, "implements": task["implements"],
            "scene": scene.id, "sceneContentHash": scene.content_hash,
            "robot": task["robot"], "asset": asset_pin, "success": task["success"],
            "episodes": plan.evaluation.episodes, "threshold": plan.evaluation.threshold,
            "baseParameters": base_params, "parameterCoverage": overrides,
            "perturbation": trial,
            "timeoutSeconds": task["declarative"]["episodeSeconds"],
            **({
                "referenceMotionRef": task["referenceMotionRef"],
                "trajectoryCompletion": completion.model_dump(mode="json"),
            } if completion else {}),
        }

    def seal(self, approval, review, task_record):
        frozen = self.matching(review["id"])
        if frozen:
            self.load(*frozen)
            return frozen[1], frozen[0].approvalId
        destination = self._package(approval.approvalId)
        if destination.exists():
            # A fully prepared package may survive a crash before the approval append.
            suite = load_suite(destination, review["id"], "user")
            if suite.metadata.get("goalSuite") != review:
                raise PlanError("goal_suite_changed", "Prepared Goal Suite protocol differs")
            return review | {"contentHash": suite.content_hash}, approval.approvalId
        root = destination_parent(self.manager.library_assets.roots["user"], (".goal-suites",), create=True)
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
            package = Path(temporary) / "package"
            (package / "private").mkdir(parents=True)
            case_count = review["episodes"]
            coverage = review["parameterCoverage"]
            keys = sorted(coverage)
            combinations = [
                review["baseParameters"] | dict(zip(keys, values, strict=True))
                for values in itertools.product(*(coverage[key] for key in keys))
            ]
            cases = [
                {"id": f"{split}-{index + 1}", "split": split,
                 "params": combinations[index % len(combinations)]}
                for split in ("train", "test") for index in range(case_count)
            ]
            seeds = {}
            excluded = set(task_record["task"]["evaluation"]["seeds"])
            used_buckets = set()
            for case in cases:
                while True:
                    seed = secrets.randbelow(MAX_SESSION_SEED + 1)
                    bucket = seed % BUCKETS
                    if seed not in excluded and bucket not in used_buckets:
                        break
                used_buckets.add(bucket)
                seeds[case["id"]] = seed
            metadata = {"id": review["id"].split("@")[0], "title": review["goalId"],
                        "layer": "embodied", "source": "newton",
                        "scene": review["scene"], "sceneContentHash": review["sceneContentHash"],
                        "episodes": cases, "goalSuite": review}
            task = {field: review[field]
                    for field in ("scene", "robot", "implements", "success", "timeoutSeconds")}
            task.update(name="goal-evaluation", milestones=[{"id": "success", "when": review["success"]}])
            for path, data in (("suite.yaml", metadata), ("task.yaml", task), ("private/seeds.yaml", seeds)):
                with (package / path).open("w", encoding="utf-8") as stream:
                    yaml.safe_dump(data, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
            suite = load_suite(package, review["id"], "user")
            sync_directory(package / "private")
            sync_directory(package)
            os.rename(package, destination)
            sync_directory(root)
            sync_directory(root.parent)
        return review | {"contentHash": suite.content_hash}, approval.approvalId
