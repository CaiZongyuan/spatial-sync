"""Controller promotion consumes platform evidence; callers cannot submit a score or qualification."""

import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from packages.info.approvals import ApprovalConflict
from packages.info.evaluation_identity import comparable, comparison_identity
from packages.info.evaluations import evaluation_decision, wilson_interval
from packages.info.plans import PlanError
from packages.library.archive import destination_parent
from packages.library.common import NAME, LibraryError, document
from packages.library.robot_resources import Embodiment, RobotModel
from packages.library.transactions import durable_json
from packages.programs.simulation import (
    admitted_subject,
    parse_subject,
    scene_requires_material_authorization,
    verify_simulation_subject,
)
from packages.programs.subjects import fingerprint
from packages.protocol.schema import Actor
from packages.training.deployment_authority import read_committed_permit
from packages.training.deployment_publication import DeploymentPlan, DeploymentPublication


@dataclass(frozen=True)
class TrainEvidence:
    current_accepted: dict
    prior_reference: dict
    interval: list[float]
    decision: str = "accepted"


def _refuse(code, *, label="unverified", **facts):
    raise PlanError(code, "Promotion requires current complete accepted train evidence",
                    decision={"label": label, **facts})


def _complete_train(store, row):
    record = store.records.get(row.get("evaluationId"), {})
    candidate = row.get("candidateIdentity") or {}
    identity_key = "programId" if candidate.get("kind") == "scene-program" else "id"
    if (
        row.get("split") != "train" or row.get("layer", "embodied") != "embodied"
        or record.get("status") != "completed" or record.get("result") != row
        or record.get("split", "train") != "train"
        or not comparison_identity(row)
        or not isinstance(row.get("candidateIdentity"), dict)
        or candidate.get("kind") not in ("policy", "program", "scene-program")
        or not all(candidate.get(key) for key in (identity_key, "contentHash"))
    ):
        _refuse("incomplete_train_evidence")
    try:
        interval = wilson_interval(row["successes"], row["episodes"])
        episodes = record["episodes"]
        valid = (
            interval is not None and row.get("score") == row["successes"] / row["episodes"]
            and len(episodes) == row["episodes"]
            and all(episode.get("status") == "valid" and type(episode.get("success")) is bool
                    and episode.get("cleanupComplete") is True for episode in episodes)
            and sum(episode["success"] for episode in episodes) == row["successes"]
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        valid = False
    if not valid:
        _refuse("incomplete_train_evidence")
    return interval


def accepted_train_evidence(store, candidate, scene, *, evaluation_id=None):
    """Freeze current accepted Candidate, its prior comparator and their unchanged relation."""
    matching = [
        row for row in store.rows
        if row.get("split") == "train" and row.get("layer", "embodied") == "embodied"
        and row.get("candidateIdentity") == candidate
        and row.get("key", {}).get("scene") == scene.id
        and row["key"].get("sceneContentHash") == scene.content_hash
        and (evaluation_id is None or row.get("evaluationId") == evaluation_id)
    ]
    accepted = [row for row in matching if row.get("decision") == "accepted"]
    if not accepted:
        label = matching[-1].get("decision", "reported") if matching else "reported"
        _refuse("candidate_not_accepted", label=label)
    current = [row for row in accepted if (store.reference(row["key"]) or {}).get("evaluationId")
               == row["evaluationId"]]
    if not current:
        _refuse("train_reference_changed")
    if len(current) != 1:
        _refuse("promotion_evaluation_ambiguous", evaluations=[row["evaluationId"] for row in current])
    row = current[0]
    _complete_train(store, row)
    reference = next(
        (value for value in store.rows if value.get("evaluationId") == row.get("reference")), None
    )
    if (reference is None or reference is row or reference.get("decision") not in ("baseline", "accepted")
            or not comparable(row, reference) or store.rows.index(reference) >= store.rows.index(row)):
        _refuse("comparison_reference_unavailable")
    _complete_train(store, reference)
    decision = evaluation_decision(row["successes"], row["episodes"], reference["score"])
    interval = wilson_interval(row["successes"], row["episodes"])
    if decision != "accepted":
        _refuse("candidate_not_accepted", label=decision, ci95=interval, referenceScore=reference["score"])
    return TrainEvidence(deepcopy(row), deepcopy(reference), interval)


class ControllerPromotions:
    """Own private test admission, human decisions and one durable publication transaction."""

    def __init__(self, manager, resources, importer, *, qualification=None, after_effect=None):
        self.manager, self.resources = manager, resources
        self.root = Path(manager.settings.state_root) / "controller-promotions"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._commit_local = threading.local()
        self.qualify = qualification or manager.policy_evidence.qualification
        self.publication = DeploymentPublication(
            manager.library_assets, manager.library, resources, importer, self.root,
            validate_context=self._publication_context, commit_context=self._commit_context,
            after_effect=after_effect,
        )

    def _path(self, identifier):
        if not isinstance(identifier, str) or not NAME.fullmatch(identifier):
            raise PlanError("unknown_promotion", "Unknown Controller promotion", status=404)
        return destination_parent(self.root, ()) / f"{identifier}.json"

    def get(self, identifier):
        path = self._path(identifier)
        if not path.is_file() or path.is_symlink() or path.is_junction() or path.stat().st_nlink != 1:
            raise PlanError("unknown_promotion", "Unknown Controller promotion", status=404)
        return document(path)

    def _save(self, value):
        durable_json(self._path(value["id"]), value)
        return value

    @staticmethod
    def public(value):
        return {key: deepcopy(value[key]) for key in (
            "id", "status", "subject", "scene", "requestId", "approvalId", "error", "deployment"
        ) if key in value}

    def _controller(self, scene, target):
        matches = [
            value for value in self.resources._documents("embodiments", Embodiment).values()
            if value["kind"] == "sim" and value["controller"] and any(
                binding == {"scene": scene.id, "entity": target, "source": "newton"}
                for binding in value["bindings"]
            )
        ]
        if len(matches) != 1:
            raise PlanError("promotion_embodiment_unavailable", "Select one fixed simulated Embodiment")
        embodiment = matches[0]
        controller = self.resources.controller(embodiment["controller"])
        controller.pop("versions")  # A prepared immutable version does not change the deployed current.
        return embodiment, controller

    def _materials(self, scene, subject):
        permit = self.authorize_scene(scene)
        materials = deepcopy(permit["materialIdentities"]) if permit else {}
        materials[subject.target.entityKey] = subject.document()["candidateIdentity"]
        return materials

    def _model(self, subject, embodiment, row):
        candidate = subject.document()["candidateIdentity"]
        policy = candidate["kind"] == "policy"
        facts = {
            "title": candidate["id"],
            "family": "rl-policy" if policy else "scripted", "source": candidate["id"],
            "license": None, "parameters": None, "embodiments": [embodiment["id"]],
            "upstreamEmbodiments": [], "actionSpace": "joint_position_delta" if policy else "robot-arm/v1",
            "compute": {"inference": "local CPU"}, "runnable": True,
            "capability": {"program": subject.binding["program"], "sources": ["newton"],
                           "requirements": ["Exact committed human deployment proof"],
                           "notes": "Measured train evidence; learned contact quality is not implied"},
            "quality": {"status": "evaluated", "evaluationId": row["evaluationId"]},
            "sources": [{"title": "Platform promotion contract",
                         "url": "https://github.com/CaiZongyuan/spatial-sync/issues/36",
                         "retrievedOn": row["at"][:10]}],
        }
        identifier = f"{candidate['kind']}-{fingerprint({'candidate': candidate, 'model': facts})[:24]}"
        return RobotModel.model_validate({"id": identifier, **facts}).model_dump(mode="json")

    def _prepare(self, body, actor):
        with self._lock, self.manager.evaluations.store.reference_guard():
            inputs = body.model_dump(mode="json")
            for path in self.root.glob("promotion-*.json"):
                previous = self.get(path.stem)
                if previous["requestId"] == body.requestId:
                    if previous["inputs"] != inputs:
                        raise PlanError("idempotency_conflict", "Request ID belongs to another promotion")
                    return previous
            scene = self.manager.library.resolve(body.scene, launch=True, source="newton")
            store = self.manager.evaluations.store
            rows = [row for row in store.rows if row.get("split") == "train"
                    and row.get("candidateIdentity", {}).get("id") == body.subject
                    and row.get("key", {}).get("scene") == scene.id
                    and (body.evaluationId is None or row["evaluationId"] == body.evaluationId)]
            current = [row for row in rows if row.get("decision") == "accepted"
                       and (store.reference(row["key"]) or {}).get("evaluationId") == row["evaluationId"]]
            if len(current) != 1:
                _refuse("candidate_not_accepted" if not rows else "train_reference_changed")
            train = accepted_train_evidence(store, current[0]["candidateIdentity"], scene,
                                            evaluation_id=current[0]["evaluationId"])
            template = store.records[train.current_accepted["evaluationId"]]
            subject = admitted_subject(template)
            if subject is None:
                _refuse("promotion_subject_unavailable")
            suite = self.manager.evaluations.suites.resolve(template["suite"])
            verify_simulation_subject(self.manager, subject, suite, scene)
            if (subject.document()["candidateIdentity"] != train.current_accepted["candidateIdentity"]
                    or subject.candidateIdentity.id != body.subject):
                _refuse("promotion_subject_changed")
            qualification = (self.qualify(body.subject, body.observationCheck)
                             if subject.candidateIdentity.kind == "policy" else {"gate": "exact-code-plan"})
            if subject.candidateIdentity.kind == "policy" and qualification.get("qualified") is not True:
                _refuse("observation_evidence_required")
            count = sum(case["split"] == "test" for case in suite.metadata["episodes"])
            if not count:
                _refuse("promotion_test_unavailable")
            plan_id = body.planId or template.get("planId")
            if plan_id is None and subject.candidateIdentity.kind == "policy":
                plan_id = self.manager.jobs.get(subject.candidateIdentity.jobId)["planId"]
            if plan_id is None:
                _refuse("promotion_plan_required")
            funding = self.manager.notebooks.prepare_evaluation(
                SimpleNamespace(planId=plan_id, planRevision=body.planRevision), suite, scene
            )
            funding["allocatedEpisodes"] = count
            embodiment, controller = self._controller(scene, subject.target.entityKey)
            model = self._model(subject, embodiment, train.current_accepted)
            candidate = subject.document()["candidateIdentity"]
            fields = {"embodiment": embodiment["id"], "robotModel": model["id"],
                      "policy": body.subject if candidate["kind"] == "policy" else None,
                      "program": subject.binding["program"],
                      "programArtifact": {key: candidate[key] for key in ("id", "contentHash")}
                      if candidate["kind"] == "program" else None,
                      "action": {"space": model["actionSpace"], "hz": None},
                      "observation": candidate.get("observationSha256"), "task": candidate.get("task"),
                      "evaluationId": train.current_accepted["evaluationId"]}
            identifier = "promotion-" + uuid.uuid4().hex
            context = {"baseScene": {"id": scene.id, "contentHash": scene.content_hash},
                       "subject": subject.document(), "controller": controller, "versionFields": fields,
                       "libraryRoot": str(self.manager.library_assets.roots["user"]),
                       "materialIdentities": self._materials(scene, subject), "robotModel": model,
                       "embodiment": embodiment, "currentAccepted": train.current_accepted,
                       "priorReference": train.prior_reference, "qualification": qualification,
                       "funding": funding, "suite": {"id": suite.id, "contentHash": suite.content_hash}}
            value = {"schema": "lw.controller-promotion/1", "id": identifier, "status": "testing",
                     "requestId": body.requestId, "inputs": inputs, "subject": body.subject,
                     "scene": scene.id, "requestedBy": actor.model_dump(mode="json"),
                     "context": context, "contextHash": fingerprint(context), "runs": {}}
            value["evaluationContextHash"] = value["contextHash"]
            value["evaluationRecords"] = []
            for side, row in (("reference", train.prior_reference), ("candidate", train.current_accepted)):
                source = store.records[row["evaluationId"]]
                evaluation_id = "eval-" + fingerprint({"promotion": identifier, "side": side})[:32]
                record = {key: deepcopy(source[key]) for key in (
                    "suite", "suiteContentHash", "subject", "runtimeHash", "identitySnapshot",
                    "candidateIdentity", "policySubject", "simulationSubject", "trainingSuccess"
                ) if key in source}
                record.update(evaluationId=evaluation_id, split="test", purpose="controller-promotion",
                              status="queued", episodes=[], attemptedEpisodes=0, at=row["at"],
                              allocatedEpisodes=count, controllerPromotion={"id": identifier, "side": side,
                              "contextHash": value["evaluationContextHash"]})
                value["runs"][side] = evaluation_id
                value["evaluationRecords"].append(record)
            return self._save(value)

    async def request(self, body, actor):
        import asyncio

        value = await asyncio.to_thread(self._prepare, body, actor)
        if value["status"] == "testing":
            try:
                await self.manager.evaluations.admit_controller_tests(
                    value["evaluationRecords"], value["context"]["funding"]
                )
            except (PlanError, LibraryError) as error:
                await asyncio.to_thread(self._block_admission, value["id"], error.detail["code"])
                raise
        return self.public(value)

    def _block_admission(self, identifier, code):
        with self._lock:
            value = self.get(identifier)
            value.update(status="blocked", error={"code": code})
            self._save(value)

    def _validate(self, value):
        context = value["context"]
        if fingerprint(context) != value["contextHash"]:
            _refuse("promotion_context_changed")
        scene = self.manager.library.resolve(context["baseScene"]["id"], launch=True, source="newton")
        subject = parse_subject(context["subject"])
        suite = self.manager.evaluations.suites.resolve(context["suite"]["id"])
        current = accepted_train_evidence(self.manager.evaluations.store,
            subject.document()["candidateIdentity"], scene,
            evaluation_id=context["currentAccepted"]["evaluationId"])
        if (current.current_accepted != context["currentAccepted"]
                or current.prior_reference != context["priorReference"]
                or suite.content_hash != context["suite"]["contentHash"]
                or scene.content_hash != context["baseScene"]["contentHash"]):
            _refuse("promotion_context_changed")
        verify_simulation_subject(self.manager, subject, suite, scene)
        if subject.candidateIdentity.kind == "policy":
            if self.qualify(value["subject"], context["qualification"]["check"]) != context["qualification"]:
                _refuse("promotion_qualification_changed")
        funding = context["funding"]
        fresh = self.manager.notebooks.prepare_evaluation(
            SimpleNamespace(planId=funding["planId"], planRevision=funding["planRevision"]), suite, scene
        )
        fresh["allocatedEpisodes"] = funding["allocatedEpisodes"]
        embodiment, controller = self._controller(scene, subject.target.entityKey)
        if (fresh != funding or embodiment != context["embodiment"] or controller != context["controller"]
                or self._materials(scene, subject) != context["materialIdentities"]):
            _refuse("promotion_context_changed")
        if "testEvidence" in context and self._test_evidence(value) != context["testEvidence"]:
            _refuse("promotion_test_changed")
        return scene, subject

    def require_evaluation(self, record):
        with self._lock:
            marker = record.get("controllerPromotion") or {}
            value = self.get(marker.get("id"))
            side = marker.get("side")
            if (value["status"] != "testing" or record.get("split") != "test"
                    or value["runs"].get(side) != record["evaluationId"]
                    or marker.get("contextHash") != value["evaluationContextHash"]):
                _refuse("promotion_evaluation_stale")
            expected = next(row for row in value["evaluationRecords"]
                            if row["evaluationId"] == record["evaluationId"])
            if any(record.get(key) != expected.get(key) for key in (
                "suite", "suiteContentHash", "subject", "candidateIdentity", "identitySnapshot",
                "policySubject", "simulationSubject", "controllerPromotion"
            )):
                _refuse("promotion_evaluation_changed")
            self._validate(value)

    def _test_evidence(self, value):
        store = self.manager.evaluations.store
        suite = self.manager.evaluations.suites.resolve(value["context"]["suite"]["id"])
        expected = [case["id"] for case in suite.metadata["episodes"] if case["split"] == "test"]
        evidence = {}
        for side, identifier in value["runs"].items():
            record = store.records.get(identifier, {})
            row = record.get("result", {})
            template = value["context"]["priorReference" if side == "reference" else "currentAccepted"]
            episodes = record.get("episodes", [])
            if (record.get("status") != "completed" or record.get("split") != "test"
                    or row not in store.rows or row.get("split") != "test"
                    or row.get("decision") != "test" or row.get("key") != template["key"]
                    or row.get("candidateIdentity") != template["candidateIdentity"]
                    or row.get("comparisonIdentity") != template["comparisonIdentity"]
                    or [episode.get("caseId") for episode in episodes] != expected
                    or any(episode.get("status") != "valid" or episode.get("cleanupComplete") is not True
                           or type(episode.get("success")) is not bool for episode in episodes)
                    or row.get("episodes") != len(expected)
                    or row.get("successes") != sum(episode["success"] for episode in episodes)
                    or row.get("score") != row["successes"] / len(expected)):
                _refuse("promotion_test_incomplete")
            evidence[side] = {"recordHash": fingerprint(record), "result": deepcopy(row)}
        return evidence

    def refresh(self, identifier):
        with self._lock:
            value = self.get(identifier)
            if value["status"] == "testing":
                store = self.manager.evaluations.store
                runs = [store.records.get(identity, {}) for identity in value["runs"].values()]
                if not all(run.get("status") in ("completed", "failed", "cancelled") for run in runs):
                    return value
                try:
                    self._validate(value)
                    value["context"]["testEvidence"] = self._test_evidence(value)
                    value["contextHash"] = fingerprint(value["context"])
                except (PlanError, LibraryError) as error:
                    value.update(status="inconclusive", error={"code": error.detail["code"]})
                else:
                    existing = next((approval for approval in self.manager.approvals.records.values()
                                     if approval.details.get("controllerPromotionId") == identifier
                                     and approval.details.get("contextHash") == value["contextHash"]), None)
                    approval = existing or self.manager.approvals.submit(
                        subject="policy", subject_id=identifier,
                        requester=Actor.model_validate(value["requestedBy"]),
                        title="Review Controller promotion",
                        details={"controllerPromotionId": identifier, "contextHash": value["contextHash"],
                                 "reviewUrl": f"/api/v1/controller-promotions/{identifier}/review"},
                    )
                    value.update(status="awaiting_human", approvalId=approval.approvalId)
                self._save(value)
            if value["status"] in ("approved", "publishing"):
                return self._publish(value)
            approval = self.manager.approvals.records.get(value.get("approvalId"))
            if (value["status"] == "awaiting_human" and approval is not None
                    and approval.status == "approved" and approval.decisions
                    and approval.decisions[-1].actor.kind == "user"
                    and approval.details.get("contextHash") == value["contextHash"]):
                value["status"] = "approved"
                self._save(value)
                return self._publish(value)
            return value

    def review(self, identifier):
        value = self.refresh(identifier)
        result = self.public(value)
        evidence = value["context"].get("testEvidence")
        if evidence:
            result["train"] = {key: deepcopy(value["context"][field])
                               for key, field in (("candidate", "currentAccepted"),
                                                  ("reference", "priorReference"))}
            result["test"] = {side: item["result"] for side, item in evidence.items()}
            result["candidateBelowReference"] = evidence["candidate"]["result"]["score"] < (
                evidence["reference"]["result"]["score"])
            result["qualification"] = deepcopy(value["context"]["qualification"])
        return result

    def decide(self, approval, decision, actor, note):
        with self._lock:
            value = self.get(approval.subjectId)
            current = self.manager.approvals.records[approval.approvalId]
            expected = {"approve": "approved", "deny": "denied", "revoke": "revoked"}[decision]
            if actor.kind != "user" or value.get("approvalId") != approval.approvalId:
                raise ApprovalConflict("Exact human deployment authority is required")
            if value["status"] == "published":
                if decision == "approve" and current.status == "approved":
                    return self.public(value)
                raise ApprovalConflict("A committed deployment requires a new transaction")
            if current.status not in ("pending", expected) and not (
                decision == "revoke" and current.status in ("approved", "denied")
            ):
                raise ApprovalConflict("This deployment approval has already been processed")
            if (decision == "approve" and current.status == "approved"
                    and value["status"] in ("approved", "publishing")):
                return self.public(self._publish(value))
            if decision == "approve":
                self._validate(value)
            if current.status != expected:
                self.manager.approvals.decide(approval.approvalId, decision, actor, note)
            value["status"] = expected
            self._save(value)
            return self.public(self._publish(value) if decision == "approve" else value)

    def _publication_context(self, record):
        value = self.get(record["id"])
        approval = self.manager.approvals.records.get(value.get("approvalId"))
        if (value["status"] not in ("approved", "publishing") or approval is None
                or approval.status != "approved" or not approval.decisions
                or approval.decisions[-1].actor.kind != "user"
                or approval.details.get("contextHash") != value["contextHash"]
                or record["intent"]["contextHash"] != value["contextHash"]):
            _refuse("promotion_approval_changed")
        if not getattr(self._commit_local, "active", False):
            self._validate(value)
            return
        # The expensive immutable material/qualification checks already ran before
        # this short guard. Only mutable admission facts can advance under it.
        context = value["context"]
        subject = parse_subject(context["subject"])
        scene = SimpleNamespace(id=context["baseScene"]["id"],
                                content_hash=context["baseScene"]["contentHash"])
        proof = accepted_train_evidence(self.manager.evaluations.store,
            subject.document()["candidateIdentity"], scene,
            evaluation_id=context["currentAccepted"]["evaluationId"])
        funding = context["funding"]
        if (proof.current_accepted != context["currentAccepted"]
                or proof.prior_reference != context["priorReference"]):
            _refuse("promotion_context_changed")
        plans = [(funding["planId"], funding["planRevision"], funding["planApprovalId"])]
        if subject.candidateIdentity.kind == "program":
            code = subject.codeApproval
            plans.append((code.planId, code.planRevision, code.approvalId))
        for identifier, revision, approval_id in plans:
            plan = self.manager.training_plans.get(identifier)
            current = self.manager.training_plans.document(plan)
            if (current["status"] != "approved" or current["revision"] != revision
                    or plan.approvalId != approval_id):
                _refuse("promotion_context_changed")

    @contextmanager
    def _commit_context(self, record):
        with self._lock:
            self._publication_context(record)
            with self.manager.training_plans._lock, self.manager.evaluations.store.reference_guard():
                self._commit_local.active = True
                try:
                    self._publication_context(record)
                    yield
                finally:
                    self._commit_local.active = False

    def _publish(self, value):
        try:
            if not (self.publication._directory(value["id"]) / "record.json").exists():
                scene, subject = self._validate(value)
                model = value["context"]["robotModel"]
                root = self.manager.library_assets.roots["user"]
                path = destination_parent(root, ("robot-models",), create=True) / f"{model['id']}.yaml"
                with self.manager.library_assets._lock:
                    if path.exists() and document(path) != model:
                        _refuse("promotion_model_changed")
                    if not path.exists():
                        durable_json(path, model)
                context = value["context"]
                self.publication.prepare(DeploymentPlan(value["id"], scene, subject, context["controller"],
                                                        context["versionFields"], value["contextHash"]))
            value["status"] = "publishing"
            self._save(value)
            result = self.publication.publish(value["id"])
            value.update(status="published" if result["phase"] == "committed" else "blocked")
            if result["phase"] == "committed":
                value["deployment"] = {"scene": result["scene"], "controller": result["controller"]}
            else:
                value["error"] = result.get("error", {"code": "publication_incomplete"})
            return self._save(value)
        except (PlanError, LibraryError) as error:
            value.update(status="blocked", error={"code": error.detail["code"]})
            return self._save(value)

    def authorize_scene(self, scene):
        """Pure read under Session registration locks; never recover or publish from here."""
        if ("newton" not in scene.metadata["sources"]
                or not scene_requires_material_authorization(scene, "newton")):
            return None
        lineage = scene.metadata.get("derivedFrom") or {}
        permit = read_committed_permit(self.manager.settings.state_root, lineage.get("promotion"))
        if permit["scene"] != {"id": scene.id, "contentHash": scene.content_hash}:
            _refuse("deployment_authorization_required")
        return permit

    def recover(self):
        for path in self.root.glob("promotion-*.json"):
            value = self.get(path.stem)
            if (value["status"] == "testing" and any(identity not in self.manager.evaluations.store.records
                                                       for identity in value["runs"].values())):
                value.update(status="inconclusive", error={"code": "promotion_admission_interrupted"})
                self._save(value)
            else:
                self.refresh(path.stem)
