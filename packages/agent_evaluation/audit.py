"""Platform-owned case evidence. Adapter messages never supply state, scores or usage."""

import json
import os
import time
from copy import deepcopy

from packages.info.plans import PlanError
from packages.runtime_records.native import redacted


class CaseAudit:
    def __init__(self, session, path, *, clock=time.monotonic, on_record=lambda record: None):
        self.session, self.path, self.clock = session, path, clock
        self.started = clock()
        self.records, self.requests = [], []
        self._cutoff = None
        self._cutoff_time = None
        self.on_record = on_record
        self.healthy = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("x", encoding="utf-8", newline="\n")

    def scope(self):
        return {
            "sessionId": self.session.session_id,
            "sceneRevision": self.session.manifest.sceneRevision,
            "epoch": self.session.epoch,
        }

    def snapshot(self):
        return {
            "scope": {
                "sessionId": self.session.session_id,
                "sceneRevision": self.session.info.scene_revision,
                "epoch": self.session.info.epoch,
            },
            "state": {
                "entities": {
                    key: value.model_dump(mode="json") for key, value in self.session.info.entities.items()
                }
            },
        }

    def append(self, kind, data):
        record = {
            "seq": len(self.records) + 1,
            "atSeconds": self.clock() - self.started,
            "kind": kind,
            "data": redacted(deepcopy(data)),
        }
        try:
            self._stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            self._stream.flush()
            self.on_record(record)
        except (OSError, ValueError):
            self.healthy = False
            return None
        self.records.append(record)
        return record

    def begin(self, method, path, query, body, tool=None):
        if not self.healthy:
            raise PlanError("agent_trace_unavailable", "Trusted case evidence is unavailable", status=503)
        data = {
            "request": {"method": method, "path": path, "query": dict(query), "body": deepcopy(body)},
            "tool": tool,
            "scope": self.scope(),
            "before": self.snapshot(),
        }
        record = self.append("http.request", data)
        if record is None:
            raise PlanError("agent_trace_unavailable", "Trusted case evidence is unavailable", status=503)
        self.requests.append(record)
        return record

    def end(self, record, status, content):
        try:
            response = json.loads(content)
        except (ValueError, UnicodeError):
            response = {"error": "non_json_response"}
        self.append(
            "http.response",
            {"requestSeq": record["seq"], "status": status, "response": response, "after": self.snapshot()},
        )

    def invocation(self, update):
        observation = {"invocation": update.model_dump(mode="json"), "after": self.snapshot()}
        self.append("invocation.observation", observation)

    def seal(self, final_observation, *, observed_monotonic=None):
        self.append("case.final", final_observation)
        self._cutoff = len(self.records)
        self._cutoff_time = (
            self.clock() if observed_monotonic is None else observed_monotonic
        ) - self.started

    def derived(self):
        if not self.healthy:
            raise PlanError("agent_trace_unavailable", "Trusted case evidence is unavailable", status=503)
        records = self.records[: self._cutoff]
        if self._cutoff_time is not None:
            records = [record for record in records if record["atSeconds"] <= self._cutoff_time]
        observed = {}
        for record in records:
            if record["kind"] == "invocation.observation":
                value = record["data"]["invocation"]
                observed.setdefault(value["invocationId"], {})[value["status"]] = record["data"]
        responses = {
            record["data"]["requestSeq"]: record["data"]
            for record in records
            if record["kind"] == "http.response"
        }
        result = []
        for record in self.requests:
            if self._cutoff is not None and record["seq"] > self._cutoff:
                continue
            data, at = record["data"], record["atSeconds"]
            request = data["request"]
            reply = responses.get(record["seq"])
            if reply is None:
                raise PlanError(
                    "agent_trace_incomplete", "An admitted tool request has no outcome", status=503
                )
            response, path, method = reply["response"], request["path"], request["method"]
            # Catalog/inbox bootstrap is retained in raw evidence; target tool calls are identified by MCP.
            if data["tool"] is None and path.endswith(("/catalog", "/inbox")):
                continue
            event = {
                "kind": "read",
                "atSeconds": at,
                **data["scope"],
                "before": data["before"],
                "after": reply["after"],
                "request": request,
                "response": response,
                "statusCode": reply["status"],
            }
            if method == "POST" and path.endswith("/actions") and "invocationId" in response:
                body = request["body"]
                observations = observed.get(response["invocationId"], {})
                terminal = next(
                    (
                        observations[name]
                        for name in ("succeeded", "rejected", "failed", "cancelled")
                        if name in observations
                    ),
                    None,
                )
                observation = terminal or observations.get("running")
                latest = observation["invocation"] if observation is not None else response
                try:
                    risk = self.session.catalog.action(body["entityKey"], body["action"]).get(
                        "risk", "normal"
                    )
                except KeyError:
                    risk = "unknown"
                event.update(
                    kind="action",
                    entityKey=body["entityKey"],
                    action=body["action"],
                    params=body.get("params", {}),
                    status=latest["status"],
                    invocationId=response["invocationId"],
                    risk=risk,
                )
                event.update({key: body[key] for key in ("planId", "stepId", "planRevision") if key in body})
                if observation is not None:
                    event["after"] = observation["after"]
                if "running" in observations:
                    event["runningObservation"] = observations["running"]
            elif method == "POST" and path.endswith("/commands"):
                event.update(
                    kind="command",
                    command=request["body"].get("command"),
                    status=response.get("ack", {}).get("status"),
                )
            elif method == "POST" and (path == "/api/v1/plans" or path.endswith("/revisions")):
                event.update(kind="plan", plan=response)
            elif path.endswith("/check"):
                event["kind"] = "check"
            elif path.endswith("/wait"):
                event["kind"] = "wait"
            result.append(event)
        return result

    def close(self):
        if self._stream.closed:
            return
        try:
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except OSError:
            self.healthy = False
        finally:
            self._stream.close()
