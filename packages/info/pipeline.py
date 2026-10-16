"""One authorization, validation and dispatch path for HTTP and WebSocket actions."""

import asyncio
import time
import uuid

from pydantic import ValidationError

from packages.info.conditions import evaluate_conditions
from packages.info.invocations import TERMINAL, InvocationTable
from packages.protocol.schema import Invocation, InvocationError, InvocationUpdate, InvokeRequest


class ActionPipeline:
    def __init__(self, session, catalog, store, publish, *, approval_timeout=600):
        self.session, self.catalog, self.store, self.publish = session, catalog, store, publish
        self.table = InvocationTable()
        self._inflight = {}
        self._reserved = {}
        self._approval_timers = {}
        self._approving = {}
        self._cleanup_tasks = set()
        self.approval_timeout = approval_timeout

    def _new(self, request, actor):
        return Invocation(
            invocationId="inv-" + uuid.uuid4().hex,
            requestId=request.requestId,
            entityKey=request.entityKey,
            action=request.action,
            params=request.params,
            actor=actor,
            epoch=self.session.epoch,
            createdSimTime=self.session.sim_time,
            createdAtMs=time.time() * 1000,
            status="accepted",
        )

    def update(self, update, *, authoritative=False):
        if update.sceneRevision != self.catalog.sceneRevision or (
            not authoritative and update.epoch != self.session.epoch
        ):
            return False
        if not self.table.apply(update):
            return False
        if update.status in TERMINAL:
            timer = self._approval_timers.pop(update.invocationId, None)
            if timer:
                timer.cancel()
        self.publish(update)
        return True

    def _emit(self, invocation):
        update = InvocationUpdate(**invocation.model_dump(), sceneRevision=self.catalog.sceneRevision)
        self.update(update, authoritative=True)
        return self.table.get(invocation.invocationId)

    def _reject(self, invocation, code, message, *, details=None, status="rejected"):
        known = self.table.get(invocation.invocationId)
        if status == "rejected" and known and known.status in ("accepted", "running"):
            status = "failed"
        return self._emit(
            invocation.model_copy(
                update={
                    "status": status,
                    "error": InvocationError(code=code, message=message, details=details or {}),
                    "endedSimTime": self.session.sim_time,
                }
            )
        )

    def _check(self, invocation):
        if self.session.state not in ("RUNNING", "PAUSED") or not self.session.source:
            return "source_unavailable", "The device host is unavailable", {}
        if invocation.entityKey not in self.catalog.entities:
            return "unknown_entity", "Unknown entity", {}
        try:
            action = self.catalog.action(invocation.entityKey, invocation.action)
        except KeyError:
            return "unknown_action", "Unknown action", {}
        try:
            self.catalog.validate_params(invocation.entityKey, invocation.action, invocation.params)
        except ValidationError as exc:
            return (
                "invalid_params",
                "Invalid action parameters",
                {"errors": exc.errors(include_url=False, include_context=False)},
            )
        if not action.get("concurrent", False):
            for active in [*self.table.active_for(invocation.entityKey), *self._reserved.values()]:
                if active.invocationId == invocation.invocationId or active.entityKey != invocation.entityKey:
                    continue
                if not self.catalog.action(active.entityKey, active.action).get("concurrent", False):
                    return (
                        "busy",
                        "Another action is active on this entity",
                        {"activeInvocationId": active.invocationId},
                    )
        failed = evaluate_conditions(
            self.catalog.conditions(invocation.entityKey, invocation.action),
            invocation.entityKey,
            self.store.values(),
        )
        if failed:
            return "precondition_failed", "Action conditions are not satisfied", {"failed": failed}
        return None

    async def invoke(self, request, role, actor):
        if role != "operator":
            prior = await self.table.lookup_request(request.requestId)
            if prior and prior.error and prior.error.code == "not_permitted":
                return prior
            return self._reject(self._new(request, actor), "not_permitted", "Operator permission is required")
        # Publish the Future before yielding: concurrent duplicate request IDs share dispatch.
        pending = self._inflight.get(request.requestId)
        if pending:
            return await asyncio.shield(pending)
        existing = await self.table.lookup_request(request.requestId)
        if existing:
            return existing
        future = asyncio.get_running_loop().create_future()
        self._inflight[request.requestId] = future
        invocation = self._new(request, actor)
        try:
            error = self._check(invocation)
            if error:
                code, message, details = error
                result = self._reject(invocation, code, message, details=details)
            else:
                definition = self.catalog.action(invocation.entityKey, invocation.action)
                if (
                    definition.get("risk", "normal") == "high"
                    and self.session.options.approval.high == "require"
                ):
                    result = self._emit(invocation.model_copy(update={"status": "pending_approval"}))
                    self._approval_timers[result.invocationId] = asyncio.get_running_loop().call_later(
                        self.approval_timeout, self._approval_expired, result.invocationId
                    )
                else:
                    # Reserve concurrency while authoritative acceptance is in flight.
                    self._reserved[invocation.invocationId] = invocation
                    result = await self._dispatch(invocation)
            future.set_result(result)
            return result
        except BaseException as exc:
            future.cancel()
            if isinstance(exc, asyncio.CancelledError):
                self._reject(invocation, "source_unavailable", "Action dispatch was interrupted")
            raise
        finally:
            self._inflight.pop(request.requestId, None)
            self._reserved.pop(invocation.invocationId, None)

    async def _dispatch(self, invocation):
        request = InvokeRequest.model_validate(
            {
                key: value
                for key, value in invocation.model_dump().items()
                if key in InvokeRequest.model_fields
            }
        )
        source = self.session.source
        try:
            update = await asyncio.wait_for(source.invoke(request), 0.5)
        except Exception:
            task = asyncio.create_task(self._cancel_unconfirmed(source, invocation.invocationId))
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._cleanup_tasks.discard)
            return self._reject(invocation, "source_unavailable", "Device host did not accept the request")
        # A host event can reach the table before this direct acceptance reply.
        self.update(update)
        return self.table.get(invocation.invocationId)

    @staticmethod
    async def _cancel_unconfirmed(source, invocation_id):
        try:
            await asyncio.wait_for(source.cancel(invocation_id), 0.5)
        except Exception:
            pass  # transport failure may also prevent cancellation; keep the audited failure

    def _approval_expired(self, invocation_id):
        invocation = self.table.get(invocation_id)
        if invocation and invocation.status == "pending_approval":
            self._reject(invocation, "approval_denied", "Approval timed out")

    async def approve(self, invocation_id, decision, role, actor):
        if role != "operator" or actor.kind == "agent":
            raise PermissionError("Only a human operator can approve actions")
        if invocation_id in self._approving:
            return await asyncio.shield(self._approving[invocation_id])
        invocation = await self.table.lookup(invocation_id)
        if invocation is None:
            raise KeyError(invocation_id)
        if invocation.status != "pending_approval":
            return invocation
        timer = self._approval_timers.pop(invocation_id, None)
        if timer:
            timer.cancel()
        if decision == "deny":
            return self._reject(invocation, "approval_denied", "Approval denied")
        error = self._check(invocation)
        if error:
            code, message, details = error
            return self._reject(invocation, code, message, details=details)
        future = asyncio.get_running_loop().create_future()
        self._approving[invocation_id] = future
        self._reserved[invocation_id] = invocation
        try:
            result = await self._dispatch(invocation)
            future.set_result(result)
            return result
        finally:
            if not future.done():
                future.cancel()
            self._approving.pop(invocation_id, None)
            self._reserved.pop(invocation_id, None)

    async def cancel(self, invocation_id, role):
        if role != "operator":
            raise PermissionError("Operator permission is required")
        invocation = await self.table.lookup(invocation_id)
        if invocation is None:
            raise KeyError(invocation_id)
        if invocation.status in TERMINAL:
            return invocation
        if invocation.status == "pending_approval":
            return self._reject(invocation, "approval_denied", "Pending action cancelled")
        try:
            update = await asyncio.wait_for(self.session.source.cancel(invocation_id), 0.5)
        except Exception:
            return self._reject(invocation, "source_unavailable", "Device host unavailable", status="failed")
        # A cancellation reply coordinates control. The reliable event stream owns
        # terminal publication after any final property observations.
        if update.status not in TERMINAL:
            self.update(update)
        return self.table.get(invocation_id)

    def fail_active(self, code, message):
        pending = {**self.table.active, **self._reserved}
        for invocation in list(pending.values()):
            self._reject(invocation, code, message, status="failed")

    def close(self):
        for task in self._cleanup_tasks:
            task.cancel()
        self._cleanup_tasks.clear()
        for timer in self._approval_timers.values():
            timer.cancel()
        self._approval_timers.clear()
