"""Hold one browser fixture's graded first episode before its normal close."""

import asyncio
import uuid
from copy import deepcopy

from fastapi import HTTPException


class EvaluationAlertBarriers:
    HOLD_SECONDS = 30

    def __init__(self, service):
        self.service = service
        self.original_episode = service._episode
        self.barriers = {}
        service._episode = self.episode

    def register(self, suite):
        token = uuid.uuid4().hex
        self.barriers[token] = {
            "suite": suite.id,
            "suiteContentHash": suite.content_hash,
            "sceneVersion": suite.metadata["scene"],
            "sceneContentHash": suite.metadata["sceneContentHash"],
            "caseId": next(case["id"] for case in suite.metadata["episodes"] if case["split"] == "train"),
            "evaluationId": None,
            "state": "pending",
            "witness": None,
            "release": asyncio.Event(),
        }
        return {"token": token, **self.snapshot(token)}

    def _get(self, token):
        if token not in self.barriers:
            raise HTTPException(404, "Unknown evaluation alert barrier")
        return self.barriers[token]

    def snapshot(self, token):
        return deepcopy({key: value for key, value in self._get(token).items() if key != "release"})

    def bind(self, token, evaluation_id):
        barrier = self._get(token)
        record = self.service.store.records.get(evaluation_id)
        if not record or record["status"] != "queued":
            raise HTTPException(409, "Bind the queued fixture evaluation before closing its manual session")
        suite = self.service.suites.resolve(record["suite"])
        if (
            record["suite"] != barrier["suite"]
            or record["suiteContentHash"] != barrier["suiteContentHash"]
            or suite.content_hash != barrier["suiteContentHash"]
            or suite.metadata["scene"] != barrier["sceneVersion"]
            or suite.metadata["sceneContentHash"] != barrier["sceneContentHash"]
            or barrier["evaluationId"] not in (None, evaluation_id)
            or any(
                other is not barrier and other["evaluationId"] == evaluation_id
                for other in self.barriers.values()
            )
        ):
            raise HTTPException(409, "Evaluation alert barrier ownership mismatch")
        barrier["evaluationId"] = evaluation_id
        return self.snapshot(token)

    def release(self, token):
        barrier = self._get(token)
        barrier["release"].set()
        if barrier["state"] in ("pending", "held"):
            barrier["state"] = "released"
        return self.snapshot(token)

    async def episode(self, session, suite, case, *, attempt=None):
        # Grade the unmodified real action exactly once before extending lifetime.
        outcome = await self.original_episode(session, suite, case, attempt=attempt)
        barrier = next(
            (item for item in self.barriers.values() if item["evaluationId"] == session.evaluation_id), None
        )
        if barrier is None or case["id"] != barrier["caseId"]:
            return outcome
        if (
            suite.id != barrier["suite"]
            or suite.content_hash != barrier["suiteContentHash"]
            or session.scene_version.id != barrier["sceneVersion"]
            or session.scene_version.content_hash != barrier["sceneContentHash"]
            or barrier["witness"] is not None
        ):
            raise RuntimeError("Evaluation alert barrier witness mismatch")
        barrier["witness"] = {
            "evaluationId": session.evaluation_id,
            "sessionId": session.session_id,
            "epoch": session.epoch,
            "outcome": deepcopy(outcome),
        }
        if not barrier["release"].is_set():
            barrier["state"] = "held"
            try:
                # Expiry fails the real evaluation; it never substitutes for release.
                async with asyncio.timeout(self.HOLD_SECONDS):
                    await barrier["release"].wait()
            except asyncio.CancelledError:
                barrier["state"] = "cancelled"
                raise
            except TimeoutError:
                barrier["state"] = "failed"
                raise
        return outcome
