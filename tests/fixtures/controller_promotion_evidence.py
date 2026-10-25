"""Actual immutable inputs with explicitly controlled scores and qualification, without Sources."""

from collections import deque
from copy import deepcopy

from packages.info.evaluation_identity import freeze_identity
from packages.info.evaluations import wilson_interval
from packages.policies.subjects import resolve_policy_subject
from tests.fixtures.policy_evaluation import native_suite


def controlled_qualification(policy, check=None):
    return {"qualified": True, "check": check or "controlled-observation-check",
            "evidenceMode": "controlled", "policy": policy}


def seed_controlled_train(manager, policy, job, *, successes=10, episodes=10):
    scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
    suite = native_suite(manager, scene, name="promotion-controlled", heldout=True)
    manager.evaluations.suites.refresh()
    subject = resolve_policy_subject(manager, policy["id"], suite, scene)
    return seed_controlled_subject(manager, subject, suite, scene, plan_id=job["planId"],
                                   successes=successes, episodes=episodes)


def seed_controlled_subject(manager, subject, suite, scene, *, plan_id, successes=10, episodes=10,
                            identifiers=None):
    """Real immutable Subject/identity inputs; controlled train scores, never a Source."""
    store = manager.evaluations.store
    identifiers = identifiers or {label: "eval-controlled-" + label for label in ("reference", "candidate")}
    identity = {"environment": "controlled-environment", "judge": "controlled-judge"}
    key = {"suite": suite.id, "suiteContentHash": suite.content_hash,
           "scene": scene.id, "sceneContentHash": scene.content_hash, "source": "newton", **identity}
    for label, candidate, passed, decision in (
        ("reference", {"kind": "program", "id": "robot_arm.newton", "contentHash": "1" * 64}, 0, "baseline"),
        ("candidate", subject.document()["candidateIdentity"], successes, "accepted"),
    ):
        identifier, requested = identifiers[label], candidate["id"]
        snapshot = freeze_identity(identifier, suite, scene, requested, store.root / "identity-inputs",
                                   candidate=candidate if label == "candidate" else None)
        candidate = snapshot["candidateIdentity"]
        row = {"evaluationId": identifier, "suite": suite.id, "subject": requested,
               "candidateIdentity": candidate, "key": deepcopy(key), "split": "train",
               "comparisonIdentity": deepcopy(identity), "decision": decision,
               "reference": identifiers["reference"] if label == "candidate" else None,
               "episodes": episodes, "successes": passed, "score": passed / episodes,
               "ci95": wilson_interval(passed, episodes), "at": "2026-10-25T00:00:00+00:00"}
        record = {"evaluationId": identifier, "suite": suite.id, "suiteContentHash": suite.content_hash,
                  "subject": requested, "candidateIdentity": candidate,
                  "split": "train", "status": "completed", "result": row,
                  "identitySnapshot": snapshot, "runtimeHash": manager.evaluations._runtime_hash,
                  "evidenceMode": "controlled",
                  "episodes": [{"status": "valid", "success": index < passed,
                                "cleanupComplete": True} for index in range(episodes)], "at": row["at"]}
        if label == "candidate":
            field = "policySubject" if candidate["kind"] == "policy" else "simulationSubject"
            record.update({field: subject.document(), "planId": plan_id})
        store.append(row)
        store.save(record)
    return scene


def complete_controlled_tests(service, promotion):
    value = service.get(promotion["id"])
    store = service.manager.evaluations.store
    for side, identifier in value["runs"].items():
        record = deepcopy(store.records[identifier])
        train = value["context"]["currentAccepted" if side == "candidate" else "priorReference"]
        success = side == "reference"
        row = train | {"evaluationId": identifier, "split": "test", "decision": "test",
                       "episodes": 1, "successes": int(success), "score": float(success),
                       "ci95": wilson_interval(int(success), 1)}
        record.update(status="completed", result=row, attemptedEpisodes=1,
                      episodes=[{"caseId": "test-one", "status": "valid", "success": success,
                                 "cleanupComplete": True}])
        store.append(row)
        store.save(record)
    service.manager.evaluations._queue = deque(
        entry for entry in service.manager.evaluations._queue if entry[1] not in value["runs"].values()
    )
