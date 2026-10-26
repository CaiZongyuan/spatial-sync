"""One HTTP client shared by the command line and the generic MCP tools."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tarfile
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import httpx

TERMINAL = {"succeeded", "rejected", "failed", "cancelled"}
INVOCATION_STATES = TERMINAL | {"accepted", "running", "pending_approval"}
ACQUISITION_FINAL = frozenset(("succeeded", "failed", "cancelled"))


def plan_fields(plan_id, step_id, revision):
    return {name: value for name, value in {"planId": plan_id, "stepId": step_id,
                                           "planRevision": revision}.items() if value is not None}


def condition_hint(invocation):
    error = invocation.get("error") or {}
    failed = error.get("details", {}).get("failed", [])
    return "\n".join(dict.fromkeys(item["message"] for item in failed if item.get("message")))


class GatewayError(Exception):
    def __init__(self, message, *, detail=None, status_code=None):
        super().__init__(message)
        self.detail, self.status_code = detail, status_code

    def document(self):
        return {"error": {"message": str(self), "statusCode": self.status_code, "detail": self.detail}}


class GatewayClient:
    def __init__(self, url=None, token=None, session=None, *, http=None):
        self.url = (url or os.getenv("LW_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.token = token if token is not None else os.getenv("LW_TOKEN")
        self._session_id = session or os.getenv("LW_SESSION") or None
        self._session_explicit = self._session_id is not None
        self._invocation_targets = {}
        try:
            self.http = http or httpx.AsyncClient(base_url=self.url, timeout=30, trust_env=False)
        except httpx.InvalidURL as exc:
            raise GatewayError("Invalid LW_URL") from exc
        self._owns_http = http is None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self._owns_http:
            await self.http.aclose()

    async def request(self, method, path, *, actor="cli:lw", timeout=30, response_headers=None,
                      response_bytes=False, **kwargs):
        headers = kwargs.pop("headers", {}) | {"X-Actor": actor}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            response = await self.http.request(method, path, headers=headers, timeout=timeout, **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # Exception URLs/messages can contain credentials. Keep diagnostics local and generic.
            raise GatewayError(f"Gateway connection failed ({type(exc).__name__})") from exc
        if response_headers is not None:
            response_headers.update(response.headers)
        if response_bytes and not response.is_error:
            return response.content
        try:
            document = response.json()
        except ValueError as exc:
            raise GatewayError("Gateway returned invalid JSON", status_code=response.status_code) from exc
        if response.is_error and not (
            isinstance(document, dict)
            and document.get("invocationId")
            and document.get("status") in INVOCATION_STATES
        ):
            detail = document.get("detail", document) if isinstance(document, dict) else document
            raise GatewayError("Gateway request failed", detail=detail, status_code=response.status_code)
        return document

    async def route(self, key=None):
        if key is not None and key.startswith("/Real/"):
            return "/api/v1/live"
        if not self._session_explicit:
            health = await self.request("GET", "/api/v1/health")
            active = [
                item for item in health["sessions"] if item["state"] in ("RUNNING", "PAUSED", "LOADING")
                and not item.get("evaluationId")
            ]
            if len(active) != 1:
                raise GatewayError(f"Set LW_SESSION: expected one active session, found {len(active)}")
            self._session_id = active[0]["sessionId"]
        return "/api/v1/sessions/" + quote(self._session_id, safe="")

    async def status(self):
        return await self.request("GET", await self.route())

    async def vault_names(self):
        return (await self.request("GET", "/api/v1/vault"))["names"]

    async def models(self, query=None):
        return (await self.request("GET", "/api/v1/models", params={"q": query} if query else {}))["models"]

    async def robots_list(self):
        return (await self.request("GET", "/api/v1/embodiments"))["embodiments"]

    async def robot_show(self, id):
        return await self.request("GET", "/api/v1/embodiments/" + quote(id, safe=""))

    async def robot_models_list(self):
        return (await self.request("GET", "/api/v1/robot-models"))["models"]

    async def robot_model_show(self, id):
        return await self.request("GET", "/api/v1/robot-models/" + quote(id, safe=""))

    async def controller_show(self, id):
        return await self.request("GET", "/api/v1/controllers/" + quote(id, safe=""))

    async def add_model(self, ref, default_thinking="default", credential_name=None):
        provider, separator, model_id = ref.partition("/")
        if not separator or not provider or not model_id:
            raise GatewayError("Use a model reference provider/model_id")
        return await self.request("POST", "/api/v1/models", json={
            "provider": provider, "modelId": model_id, "defaultThinking": default_thinking,
            "credentialName": credential_name,
        })

    async def default_model(self, ref):
        return await self.request("PUT", "/api/v1/models/default", json={"ref": ref})

    async def catalog(self):
        return await self.request("GET", await self.route() + "/catalog")

    async def catalog_if_active(self):
        if self.token and self.token.startswith("lw-case-"):
            return await self.catalog()
        health = await self.request("GET", "/api/v1/health")
        if any(session["state"] in ("RUNNING", "PAUSED") and not session.get("evaluationId")
               for session in health["sessions"]):
            return await self.catalog()
        if health.get("live", {}).get("enabled"):
            return await self.request("GET", "/api/v1/live/catalog")
        return None

    async def scene_search(self, query=None, launchable=False):
        return await self.library_search(query=query, launchable=launchable)

    async def asset_search(self, query=None, kind=None, gaps=False):
        return await self.library_search(query=query, kind=kind, gaps=gaps, resource="assets")

    async def library_search(self, query=None, launchable=False, resource="scenes", kind=None, gaps=False):
        if kind is not None or gaps:
            resource = "assets"
        if resource not in ("scenes", "assets"):
            raise ValueError("resource must be scenes or assets")
        if resource == "assets" and launchable:
            raise ValueError("launchable is only supported for scenes")
        params = {"q": query} if query is not None else {}
        if launchable:
            params["launchable"] = True
        if kind is not None:
            params["kind"] = kind
        if gaps:
            params["gaps"] = True
        return (await self.request("GET", "/api/v1/library/" + resource, params=params))[resource]

    async def catalog_search(self, query=None, scope="all", kind=None, provider=None, usage=None,
                             license=None, installed=False, notInstalled=False,
                             minSourceBytes=None, maxSourceBytes=None, minDimensionM=None,
                             maxDimensionM=None, approximate=False, sort="relevance", limit=10,
                             cursor=None, category=None, tag=None, release=None, page=None):
        if installed and notInstalled:
            raise GatewayError(
                "installed and notInstalled are mutually exclusive",
                status_code=400,
                detail={"code": "invalid_filter"},
            )
        installed_filter = True if installed else False if notInstalled else None
        params = {name: value for name, value in {
            "q": query,
            "scope": scope if scope != "all" else None,
            "kind": kind,
            "provider": provider,
            "usage": usage,
            "licenseAdmitted": None if license is None else license == "admitted",
            "installed": installed_filter,
            "minSourceBytes": minSourceBytes,
            "maxSourceBytes": maxSourceBytes,
            "minDimensionM": minDimensionM,
            "maxDimensionM": maxDimensionM,
            "approximate": True if approximate else None,
            "sort": sort if sort != "relevance" else None,
            "limit": limit if limit != 10 else None,
            "cursor": cursor,
            "category": category, "tag": tag, "release": release, "page": page,
        }.items() if value is not None}
        return await self.request("GET", "/api/v1/library/search", params=params)

    @staticmethod
    def _catalog_path(id):
        value = id.removeprefix("catalog:")
        provider, separator, rest = value.partition("/")
        package, revision_separator, revision = rest.partition("@")
        if not separator or not revision_separator or not provider or not package or not revision:
            raise ValueError("Select a catalog entry provider/package@revision")
        return "/api/v1/library/catalog/entries/" + "/".join(
            quote(part, safe="") for part in (provider, package, revision)
        )

    async def catalog_show(self, id, view="summary", documentDigest=None, release=None):
        params = {"view": view}
        if documentDigest is not None:
            params["documentDigest"] = documentDigest
        if release is not None:
            params["release"] = release
        return await self.request("GET", self._catalog_path(id), params=params)

    async def catalog_estimate(self, id, release=None):
        return await self.request("GET", self._catalog_path(id) + "/estimate",
                                  params={"release": release} if release else None)

    @staticmethod
    def _maintenance_path(id=None):
        return "/api/v1/library/catalog/maintenance" + ("/runs/" + quote(id, safe="") if id else "")

    async def catalog_collections(self):
        return await self.request("GET", self._maintenance_path() + "/collections")

    async def catalog_collection_put(self, profile, *, actor="cli:lw"):
        return await self.request(
            "POST", self._maintenance_path() + "/collections", json=profile, actor=actor
        )

    async def catalog_collection_set(self, id, document, *, actor="cli:lw"):
        return await self.request(
            "PATCH",
            self._maintenance_path() + "/collections/" + quote(id, safe=""),
            json=document,
            actor=actor,
        )

    async def catalog_discover(self, limit=20, *, actor="cli:lw"):
        return await self.request(
            "GET", self._maintenance_path() + "/discover", params={"limit": limit}, actor=actor
        )

    async def catalog_maintain(
        self, collection, revision=None, budgets=None, wait=False, requestId=None, *, actor="cli:lw"
    ):
        result = await self.request(
            "POST",
            self._maintenance_path() + "/runs",
            actor=actor,
            json={
                "collectionId": collection,
                "revision": revision,
                "budgets": budgets,
                "requestId": requestId,
            },
        )
        deadline = time.monotonic() + 120
        while wait and result["status"] in ("queued", "running") and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            result = await self.catalog_maintenance_status(result["id"])
        return result

    async def catalog_maintenance_status(self, id=None):
        return await self.request("GET", self._maintenance_path(id))

    async def catalog_maintenance_cancel(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._maintenance_path(id) + "/cancel", actor=actor)

    async def catalog_maintenance_resume(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._maintenance_path(id) + "/resume", actor=actor)

    async def catalog_maintenance_retry(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._maintenance_path(id) + "/retry", actor=actor)

    async def catalog_batch_claim(self, id, limit=24, *, actor="cli:lw"):
        return await self.request(
            "POST", self._maintenance_path(id) + "/claim", json={"limit": limit}, actor=actor
        )

    async def catalog_batch_submit(self, id, batch, document, *, actor="cli:lw"):
        return await self.request(
            "POST",
            self._maintenance_path(id) + "/batches/" + quote(batch, safe="") + "/submit",
            json=document,
            actor=actor,
        )

    async def catalog_batch_error(self, id, batch, document, *, actor="cli:lw"):
        return await self.request(
            "POST",
            self._maintenance_path(id) + "/batches/" + quote(batch, safe="") + "/error",
            json=document,
            actor=actor,
        )

    async def catalog_evidence(self, digest, offset=0):
        if offset < 0:
            raise ValueError("Evidence offset must be nonnegative")
        content = await self.request(
            "GET",
            self._maintenance_path() + "/evidence/" + quote(digest, safe=""),
            params={"view": "raw"},
            response_bytes=True,
        )
        chunk = content[offset : offset + 32768]
        try:
            text = chunk.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text, encoding = base64.b64encode(chunk).decode("ascii"), "base64"
        return {
            "sha256": digest,
            "bytes": len(content),
            "offset": offset,
            "encoding": encoding,
            "content": text,
            "nextOffset": offset + len(chunk) if offset + len(chunk) < len(content) else None,
        }

    async def catalog_correction_put(self, document, *, actor="cli:lw"):
        return await self.request(
            "POST", self._maintenance_path() + "/corrections", json=document, actor=actor
        )

    async def catalog_correction_retract(self, id, baseVersion=None, *, actor="cli:lw"):
        return await self.request(
            "POST",
            self._maintenance_path() + "/corrections/" + quote(id, safe="") + "/retract",
            json={"baseVersion": baseVersion},
            actor=actor,
        )

    async def catalog_organization_select(self, document, *, actor="cli:lw"):
        return await self.request(
            "POST", self._maintenance_path() + "/organization/select", json=document, actor=actor
        )

    async def catalog_metadata_publish(self, document=None, *, actor="cli:lw"):
        return await self.request(
            "POST", self._maintenance_path() + "/publish", json=document or {}, actor=actor
        )

    async def catalog_metadata_export(self, *, actor="cli:lw"):
        return await self.request("GET", self._maintenance_path() + "/export", actor=actor)

    async def catalog_metadata_restore(self, document, *, actor="cli:lw"):
        return await self.request("POST", self._maintenance_path() + "/restore", json=document, actor=actor)

    async def catalog_sync(self, wait=False, provider=None, *, actor="cli:lw"):
        value = await self.request("POST", "/api/v1/library/catalog/sync", actor=actor,
                                   params={"provider": provider} if provider else None)
        if wait:
            deadline = time.monotonic() + 120
            while value["status"] in ("running", "cancelling") and time.monotonic() < deadline:
                await asyncio.sleep(.2)
                value = await self.catalog_sync_status(value["id"])
        return value

    async def catalog_sync_status(self, id=None):
        path = "/api/v1/library/catalog/sync"
        return await self.request("GET", path + ("/" + quote(id, safe="") if id else ""))

    async def catalog_sync_cancel(self, id, *, actor="cli:lw"):
        return await self.request(
            "POST", "/api/v1/library/catalog/sync/" + quote(id, safe="") + "/cancel", actor=actor
        )

    async def catalog_sync_retry(self, id, *, actor="cli:lw"):
        return await self.request(
            "POST", "/api/v1/library/catalog/sync/" + quote(id, safe="") + "/retry", actor=actor
        )

    @staticmethod
    def _acquisition_path(id):
        return "/api/v1/library/acquisitions/" + quote(id, safe="")

    async def acquisition_start(self, ref, wait=False, *, actor="cli:lw", timeout=120):
        """Create one acquisition from a catalog ID; ``wait`` polls the bounded record."""
        document = await self.request(
            "POST", "/api/v1/library/acquisitions", json={"ref": ref}, actor=actor
        )
        if not wait:
            return document
        deadline = time.monotonic() + timeout
        while document.get("status") not in ACQUISITION_FINAL and time.monotonic() < deadline:
            await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            document = await self.acquisition_show(document["id"])
        return document

    async def acquisition_list(self, status=None):
        params = {"status": status} if status is not None else {}
        return await self.request("GET", "/api/v1/library/acquisitions", params=params)

    async def acquisition_show(self, id):
        return await self.request("GET", self._acquisition_path(id))

    async def acquisition_cancel(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._acquisition_path(id) + "/cancel", actor=actor)

    async def acquisition_retry(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._acquisition_path(id) + "/retry", actor=actor)

    @staticmethod
    def _authoring_path(submission_id=None):
        path = "/api/v1/library/authoring-reports"
        return path if submission_id is None else path + "/" + quote(submission_id, safe="")

    async def authoring_submit(self, document, *, actor="cli:lw"):
        """Submit one external report; a duplicate submissionId returns its first receipt."""
        return await self.request(
            "POST", self._authoring_path(), json=document, actor=actor
        )

    async def authoring_list(self, scene=None, asset=None, draft=None, limit=20):
        params = {
            name: value
            for name, value in {
                "scene": scene, "asset": asset, "draft": draft,
                "limit": limit if limit != 20 else None,
            }.items()
            if value is not None
        }
        return await self.request("GET", self._authoring_path(), params=params)

    async def authoring_show(self, id):
        return await self.request("GET", self._authoring_path(id))

    async def library_describe(self, id, view="summary", section=None, offset=0, documentDigest=None):
        params = {"view": view, "offset": offset}
        if section is not None:
            params["section"] = section
        if documentDigest is not None:
            params["documentDigest"] = documentDigest
        return await self.request("GET", self._library_path(id), params=params)

    @staticmethod
    def _library_path(id):
        name, separator, rev = id.partition("@")
        if "/" in name:
            path = "/api/v1/library/assets/" + "/".join(quote(part, safe="") for part in name.split("/"))
        else:
            path = "/api/v1/library/scenes/" + quote(name, safe="")
        if separator:
            path += "/" + quote(rev, safe="")
        return path

    async def library_validate(self, id, *, actor="cli:lw"):
        if "@" not in id:
            raise ValueError("Validation requires an exact asset or scene version with @rev")
        return await self.request("POST", self._library_path(id) + "/validate", actor=actor, timeout=240)

    async def library_remove(self, id, *, actor="cli:lw"):
        if not re.fullmatch(
            r"(?:(?:robot|device|labware|environment)/)?[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*", id,
        ):
            raise ValueError("Deletion requires an exact asset kind/name@rev or scene name@rev")
        return await self.request("DELETE", self._library_path(id), actor=actor)

    async def library_import(self, directory, *, actor="cli:lw"):
        return await self._upload_directory(directory, "/api/v1/library/imports", actor)

    async def task_push(self, directory, *, actor="cli:lw"):
        return await self._upload_directory(directory, "/api/v1/tasks", actor)

    async def suite_push(self, directory, *, actor="cli:lw"):
        return await self._upload_directory(directory, "/api/v1/suites", actor)

    async def program_inspect(self, directory, *, actor="cli:lw"):
        return await self._upload_directory(directory, "/api/v1/programs/inspect", actor)

    async def program_push(self, directory, *, actor="cli:lw"):
        return await self._upload_directory(directory, "/api/v1/programs", actor)

    async def program_list(self):
        return (await self.request("GET", "/api/v1/programs"))["programs"]

    @staticmethod
    def _program_path(id):
        match = re.fullmatch(r"program/robot_arm\.script\.([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)", id)
        if not match:
            raise ValueError("Select an exact program/robot_arm.script.name@revision")
        return f"/api/v1/programs/{match[1]}/{match[2]}"

    async def program_show(self, id):
        return await self.request("GET", self._program_path(id))

    async def program_promote(self, id, scene, evaluation=None, plan=None, revision=None,
                              request_id=None, *, actor="cli:lw"):
        self._program_path(id)
        return await self.controller_promotion_request(
            id, scene, evaluation, plan=plan, revision=revision, request_id=request_id, actor=actor
        )

    async def program_source(self, id, filename):
        content = await self.request(
            "GET", self._program_path(id) + "/files/" + quote(filename, safe="/"), response_bytes=True
        )
        return {"id": id, "path": filename, "content": content.decode("utf-8")}

    async def program_export(self, id, output):
        content = await self.request("GET", self._program_path(id) + "/archive", response_bytes=True)
        try:
            with Path(output).open("xb") as stream:
                stream.write(content)
        except OSError as exc:
            raise ValueError("Program output must be a new writable file") from exc
        return {"id": id, "output": output}

    async def tasks_list(self):
        return (await self.request("GET", "/api/v1/tasks"))["tasks"]

    async def task_show(self, id):
        match = re.fullmatch(r"task/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)", id)
        if not match:
            raise ValueError("Select an exact task/name@revision")
        return await self.request("GET", f"/api/v1/tasks/{match[1]}/{match[2]}")

    async def _upload_directory(self, directory, route, actor):
        # The gateway independently validates paths, schema and upload/expansion limits.
        try:
            root = Path(directory).expanduser().resolve()
            if not root.is_dir():
                raise ValueError("Import directory does not exist on the client's filesystem")
            with tempfile.TemporaryFile() as output:
                with tarfile.open(fileobj=output, mode="w", dereference=True) as archive:
                    for path in sorted(root.rglob("*")):
                        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
                            raise ValueError("Library directory cannot contain filesystem links")
                        if path.is_file():
                            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
                        elif not path.is_dir():
                            raise ValueError("Library directory can only contain regular files")
                output.seek(0)

                # httpx's async transport requires an async iterator for streamed bodies.
                async def chunks():
                    while chunk := output.read(1024 * 1024):
                        yield chunk

                return await self.request(
                    "POST",
                    route,
                    content=chunks(),
                    headers={"Content-Type": "application/x-tar"},
                    actor=actor,
                    timeout=240,
                )
        except OSError as exc:
            raise ValueError("Library directory could not be read") from exc

    async def scene_launch(self, sceneVersion, source=None, seed=None, *, actor="cli:lw"):
        body = {"sceneVersion": sceneVersion}
        if source is not None:
            body["source"] = source
        if seed is not None:
            body["seed"] = seed
        result = await self.request("POST", "/api/v1/sessions", json=body, actor=actor, timeout=190)
        if "sessionId" in result:
            self._session_id, self._session_explicit = result["sessionId"], False
        return result

    @staticmethod
    def _suite_path(suite):
        match = re.fullmatch(r"suite/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)", suite)
        if not match:
            raise ValueError("Select an exact suite/name@revision")
        return "/api/v1/suites/" + match[1] + "/" + match[2]

    async def suites_list(self):
        return (await self.request("GET", "/api/v1/suites"))["suites"]

    @staticmethod
    def _agent_path(id):
        name = id.removeprefix("agent/")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError("Select an Agent name or agent/name")
        return "/api/v1/agents/" + name

    async def agents_list(self):
        return (await self.request("GET", "/api/v1/agents"))["agents"]

    async def agent_show(self, id):
        return await self.request("GET", self._agent_path(id))

    async def agent_harness(self, id, version):
        return await self.request("GET", self._agent_path(id) + f"/harness/{version}")

    async def agent_rsi(self, id):
        return await self.request("GET", self._agent_path(id) + "/rsi")

    async def agent_tool_trace(self, id, goal):
        return await self.request(
            "GET", self._agent_path(id) + "/tool-trace", params={"goal": self._goal(goal)}
        )

    async def agent_baseline(self, id, evaluationId, requestId=None, *, actor="cli:lw"):
        return await self.request(
            "POST",
            self._agent_path(id) + "/baseline",
            actor=actor,
            json={"evaluationId": evaluationId, "requestId": requestId or uuid.uuid4().hex},
        )

    async def agent_candidate(self, id, document, *, actor="cli:lw"):
        return await self.request("POST", self._agent_path(id) + "/candidates", actor=actor, json=document)

    async def agent_optimizer_grant(self, id, document, *, actor="cli:lw"):
        return await self.request(
            "POST", self._agent_path(id) + "/optimizer-grant", actor=actor, json=document
        )

    async def agent_round_start(self, id, revision, requestId=None, *, actor="cli:lw"):
        return await self.request(
            "POST",
            "/api/v1/plans/" + quote(id, safe="") + "/agent-runs",
            actor=actor,
            json={"revision": revision, "requestId": requestId or uuid.uuid4().hex},
        )

    async def agent_round_settle(self, id, revision, *, actor="cli:lw"):
        return await self.request(
            "POST",
            "/api/v1/plans/" + quote(id, safe="") + "/settle",
            actor=actor,
            json={"revision": revision},
        )

    async def agent_contamination(self, id, document, *, actor="cli:lw"):
        return await self.request(
            "POST", "/api/v1/plans/" + quote(id, safe="") + "/contamination", actor=actor, json=document
        )

    async def agent_contamination_capture(
        self, id, records, format="claude-code-stream-json", *, actor="cli:lw"
    ):
        return await self.request(
            "POST", "/api/v1/plans/" + quote(id, safe="") + "/contamination/runtime-records/import",
            actor=actor, json={"format": format, "records": records},
        )

    async def agent_promotion_request(self, id, revision, requestId=None, *, actor="cli:lw"):
        return await self.request(
            "POST",
            "/api/v1/plans/" + quote(id, safe="") + "/promotions",
            actor=actor,
            json={"revision": revision, "requestId": requestId or uuid.uuid4().hex},
        )

    @staticmethod
    def _promotion_path(id):
        return "/api/v1/agent-promotions/" + quote(id, safe="")

    async def agent_promotion_show(self, id):
        return await self.request("GET", self._promotion_path(id))

    async def agent_promotion_review(self, id):
        return await self.request("GET", self._promotion_path(id) + "/review")

    async def agent_promotion_grant(self, id, wallSeconds=900, *, actor="cli:lw"):
        return await self.request(
            "POST",
            self._promotion_path(id) + "/controller-grant",
            actor=actor,
            json={"wallSeconds": wallSeconds},
        )

    async def agent_promotion_next(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._promotion_path(id) + "/next", actor=actor)

    async def agent_promotion_bind(self, id, caseRunId, nativeSessionId, *, actor="cli:lw"):
        return await self.request(
            "POST",
            self._promotion_path(id) + "/bind-native",
            actor=actor,
            json={"caseRunId": caseRunId, "nativeSessionId": nativeSessionId},
        )

    async def agent_promotion_complete(
        self, id, caseRunId, nativeSessionId, runtimeRecordId=None, *, actor="cli:lw"
    ):
        body = {
            "caseRunId": caseRunId,
            "nativeSessionId": nativeSessionId,
            "runtimeRecordId": runtimeRecordId,
        }
        return await self.request("POST", self._promotion_path(id) + "/complete", actor=actor, json=body)

    async def agent_promotion_capture(self, id, records, format="claude-code-stream-json", *, actor="cli:lw"):
        return await self.request(
            "POST",
            self._promotion_path(id) + "/runtime-records/import",
            actor=actor,
            json={"format": format, "records": records},
        )

    async def agent_promotion_cancel(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._promotion_path(id) + "/cancel", actor=actor)

    async def agent_promotion_cleanup(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._promotion_path(id) + "/cleanup", actor=actor)

    async def agent_run_create(
        self,
        id,
        suite,
        harnessVersion,
        runtime,
        model,
        thinking,
        runtimeVersion=None,
        purpose="train",
        caseId=None,
        wallSeconds=900,
        requestId=None,
        *,
        actor="cli:lw",
    ):
        payload = {
            "requestId": requestId or uuid.uuid4().hex,
            "suite": suite,
            "harnessVersion": harnessVersion,
            "purpose": purpose,
            "wallSeconds": wallSeconds,
            "runtime": {"name": runtime, "version": runtimeVersion, "model": model, "thinking": thinking},
        }
        if caseId is not None:
            payload["caseId"] = caseId
        return await self.request("POST", self._agent_path(id) + "/runs", json=payload, actor=actor)

    async def agent_run_list(self, agent=None):
        params = {"agent": agent} if agent is not None else {}
        return (await self.request("GET", "/api/v1/agent-runs", params=params))["runs"]

    async def agent_run_show(self, id):
        return await self.request("GET", "/api/v1/agent-runs/" + quote(id, safe=""))

    async def agent_run_next(self, id, *, actor="cli:lw"):
        return await self.request("POST", "/api/v1/agent-runs/" + quote(id, safe="") + "/next", actor=actor)

    async def agent_run_complete(self, id, caseRunId, nativeSessionId, runtimeRecordId=None,
                                 *, actor="cli:lw"):
        payload = {"caseRunId": caseRunId, "nativeSessionId": nativeSessionId}
        if runtimeRecordId is not None:
            payload["runtimeRecordId"] = runtimeRecordId
        return await self.request("POST", "/api/v1/agent-runs/" + quote(id, safe="") + "/complete",
                                  json=payload, actor=actor)

    async def agent_run_cancel(self, id, *, actor="cli:lw"):
        return await self.request("POST", "/api/v1/agent-runs/" + quote(id, safe="") + "/cancel", actor=actor)

    async def agent_versions(self, id):
        return (await self.request("GET", self._agent_path(id) + "/versions"))["versions"]

    async def agent_snapshot(self, id, *, actor="cli:lw"):
        return await self.request("POST", self._agent_path(id) + "/snapshots", actor=actor)

    async def harness_diff(self, id, before, after):
        result = await self.request("GET", self._agent_path(id) + f"/versions/{after}",
                                    params={"against": before})
        return result["diff"]

    async def agent_export(self, id, version, output):
        data = await self.request("GET", self._agent_path(id) + f"/versions/{version}/snapshot",
                                  response_bytes=True)
        try:
            with Path(output).open("xb") as stream:
                stream.write(data)
        except OSError as exc:
            raise ValueError("Snapshot output must be a new writable file") from exc
        return {"agent": "agent/" + id.removeprefix("agent/"), "version": version, "output": output}

    @staticmethod
    def _snapshot_bytes(archive):
        try:
            path = Path(archive)
            if (path.suffix != ".tar" or path.is_symlink() or path.is_junction()
                or path.stat().st_size > 3 * 1024 * 1024):
                raise ValueError("Select a portable Harness .tar archive of at most 3 MiB")
            return path.read_bytes()
        except OSError as exc:
            raise ValueError("Cannot read Harness tar archive") from exc

    async def agent_import(self, id, archive, *, actor="cli:lw"):
        return await self.request("POST", self._agent_path(id) + "/snapshots",
                                  content=self._snapshot_bytes(archive),
                                  headers={"Content-Type": "application/x-tar"}, actor=actor)

    async def agent_create(self, id, archive, title=None, *, actor="cli:lw"):
        self._agent_path(id)
        params = {"id": id, **({"title": title} if title else {})}
        return await self.request("POST", "/api/v1/agents", params=params,
                                  content=self._snapshot_bytes(archive),
                                  headers={"Content-Type": "application/x-tar"}, actor=actor)

    async def suite_show(self, suite):
        return await self.request("GET", self._suite_path(suite))

    async def score_list(self, suite, subject=None):
        return (await self.request("GET", self._suite_path(suite) + "/scoreboard",
                                   params={"subject": subject} if subject else {}))["rows"]

    async def _import_runtime(self, format, *, content=None, records=None, sourceLabel=None,
                              agentId=None, harnessVersion=None, planId=None, planRevision=None,
                              goalId=None, clearAssociations=False, actor="cli:lw"):
        if (planId is None) != (planRevision is None):
            raise ValueError("Associate an exact Plan with both --plan and --plan-revision")
        if harnessVersion is not None and agentId is None:
            raise ValueError("A Harness association requires --agent")
        associations = {key: value for key, value in {
            "agentId": agentId, "harnessVersion": harnessVersion, "planId": planId,
            "planRevision": planRevision, "goalId": goalId,
        }.items() if value is not None}
        if clearAssociations and associations:
            raise ValueError("Choose explicit associations or --clear-associations")
        body = {"format": format, **({"content": content} if content is not None else {"records": records})}
        if sourceLabel is not None:
            body["sourceLabel"] = sourceLabel
        if associations or clearAssociations:
            body["associations"] = associations
        return await self.request("POST", "/api/v1/runtime-sessions/import", json=body, actor=actor)

    async def runtime_import_file(self, file, format="claude-code-stream-json", **options):
        try:
            content = Path(file).read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError("Cannot read the explicitly selected UTF-8 runtime capture") from exc
        if options.get("sourceLabel") is None:
            options["sourceLabel"] = Path(file).name
        return await self._import_runtime(format, content=content, **options)

    async def runtime_import_records(self, records, format="claude-code-stream-json", **options):
        return await self._import_runtime(format, records=records, **options)

    async def runtime_list(self, q=None, runtime=None, agent=None, plan=None, goal=None):
        params = {key: value for key, value in {"q": q, "runtime": runtime, "agent": agent,
                  "plan": plan, "goal": goal}.items() if value is not None}
        return (await self.request("GET", "/api/v1/runtime-sessions", params=params))["sessions"]

    async def runtime_show(self, id):
        return await self.request("GET", "/api/v1/runtime-sessions/" + quote(id, safe=""))

    async def demo_add(self, recordingId, task=None, *, actor="cli:lw"):
        body = {"recordingId": recordingId}
        if task is not None:
            body["task"] = task
        return await self.request("POST", "/api/v1/demos", json=body, actor=actor)

    async def demo_list(self, task=None):
        return (await self.request("GET", "/api/v1/demos", params={"task": task} if task else {}))["demos"]

    async def evaluation_run(self, subject, suite, planId=None, planRevision=None, *, actor="cli:lw"):
        payload = {"subject": subject, "suite": suite, "split": "train"}
        if planId is not None:
            payload["planId"] = planId
        if planRevision is not None:
            payload["planRevision"] = planRevision
        return await self.request("POST", "/api/v1/evaluations", json=payload, actor=actor)

    @staticmethod
    def _goal(value):
        if re.fullmatch(r"task/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*", value):
            return "goal/" + value.split("/", 1)[1].rsplit("@", 1)[0]
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            return "goal/" + value
        if not re.fullmatch(r"goal/[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise GatewayError("Select goal/name, a Task version or its default goal name")
        return value

    async def notebook_show(self, goal, refuted=False):
        return await self.request("GET", "/api/v1/notebooks/" + quote(self._goal(goal), safe=""),
                                  params={"refuted": str(refuted).lower()})

    async def loop_show(self, goal):
        return await self.request("GET", "/api/v1/loops/" + quote(self._goal(goal), safe=""))

    async def notebook_override(self, goal, entry, hypothesisId, verdict, note, requestId=None,
                                *, actor="cli:lw"):
        return await self.request("PATCH", "/api/v1/notebooks/" + quote(self._goal(goal), safe="") + "/" +
                                  quote(entry, safe=""), actor=actor,
                                  json={"hypothesisId": hypothesisId, "verdict": verdict, "note": note,
                                        "requestId": requestId or uuid.uuid4().hex})

    async def evaluation_show(self, id):
        return await self.request("GET", "/api/v1/evaluations/" + quote(id, safe=""))

    async def evaluations_list(self):
        return (await self.request("GET", "/api/v1/evaluations"))["evaluations"]

    async def evaluation_cancel(self, id, *, actor="cli:lw"):
        return await self.request("POST", "/api/v1/evaluations/" + quote(id, safe="") + "/cancel",
                                  actor=actor)

    async def session_request_show(self, id):
        return await self.request("GET", "/api/v1/session-requests/" + quote(id, safe=""))

    async def session_request_cancel(self, id, *, actor="cli:lw"):
        return await self.request("DELETE", "/api/v1/session-requests/" + quote(id, safe=""), actor=actor)

    async def alerts_list(self, active=False, state=None, severity=None):
        params = {"active": "true"} if active else {}
        if state is not None:
            params["state"] = state
        if severity is not None:
            params["severity"] = severity
        if self._session_explicit:
            params["sessionId"] = self._session_id
        return (await self.request("GET", "/api/v1/alerts", params=params))["alerts"]

    async def history_query(self, key, property, since, until=None, every=None, agg="mean", recording=None):
        params = {name: value for name, value in {
            "key": key, "property": property, "since": since, "until": until,
            "every": every, "agg": agg, "recording": recording,
        }.items() if value is not None}
        if recording is None and not key.startswith("/Real/") and self._session_explicit:
            params["sessionId"] = self._session_id
        # History's public HTTP body is a point array. Capture only this call's
        # headers to expose its cap and range through both CLI and MCP.
        headers = {}
        points = await self.request("GET", "/api/v1/history", params=params, response_headers=headers)
        every_ms = headers["x-history-every-ms"]
        return {"points": points, "recordingId": headers["x-history-recording"],
                "limit": int(headers["x-history-limit"]),
                "truncated": headers["x-history-truncated"] == "true",
                "sinceMs": float(headers["x-history-since-ms"]),
                "untilMs": float(headers["x-history-until-ms"]),
                "everyMs": None if every_ms == "raw" else float(every_ms),
                "agg": headers["x-history-agg"], "message": headers.get("x-history-explanation")}

    async def inbox_poll(self, after=None, kinds=None):
        params = {name: value for name, value in {"after": after, "kinds": kinds}.items()
                  if value is not None}
        return await self.request("GET", "/api/v1/inbox", params=params)

    async def alert_ack(self, alertId, note="", *, actor="cli:lw"):
        return await self.request(
            "POST", "/api/v1/alerts/" + quote(alertId, safe="") + "/ack", json={"note": note}, actor=actor
        )

    async def list_entities(self, type=None, query=None):
        params = {key: value for key, value in {"type": type, "q": query}.items() if value is not None}
        return (await self.request("GET", await self.route(query) + "/entities", params=params))["entities"]

    async def describe_entity(self, key):
        return await self.request("GET", await self.route(key) + "/entity", params={"key": key})

    async def get_state(self, keys):
        groups = {}
        for key in keys:
            groups.setdefault(key.startswith("/Real/"), []).append(key)
        if not groups:
            return await self.request("GET", await self.route() + "/state")
        entities = {}
        for selected in groups.values():
            document = await self.request("GET", await self.route(selected[0]) + "/state",
                                          params={"keys": ",".join(selected)})
            entities.update(document["entities"])
        return {"entities": entities}

    async def invoke_action(self, key, action, params=None, *, wait=True, timeout=120, actor="cli:lw",
                            planId=None, stepId=None, planRevision=None):
        route = await self.route(key)
        result = await self.request(
            "POST",
            route + "/actions",
            actor=actor,
            timeout=timeout + 10,
            json={
                "requestId": uuid.uuid4().hex,
                "entityKey": key,
                "action": action,
                "params": params or {},
                "wait": wait,
                "timeoutSeconds": timeout,
                **plan_fields(planId, stepId, planRevision),
            },
        )
        self._invocation_targets[result["invocationId"]] = route
        return result

    async def invocation_route(self, invocation_id):
        if self.token and self.token.startswith("lw-case-"):
            return await self.route()
        if invocation_id in self._invocation_targets:
            return self._invocation_targets[invocation_id]
        path = "/api/v1/live/invocations/" + quote(invocation_id, safe="")
        try:
            await self.request("GET", path)
        except GatewayError as error:
            if error.status_code != 404 and not (
                error.status_code == 503 and isinstance(error.detail, dict)
                and error.detail.get("code") == "live_unavailable"
            ):
                raise
            return await self.route()
        self._invocation_targets[invocation_id] = "/api/v1/live"
        return "/api/v1/live"

    async def get_invocation(self, invocation_id, *, wait=False, timeout=120):
        return await self.request(
            "GET",
            await self.invocation_route(invocation_id) + "/invocations/" + quote(invocation_id, safe=""),
            params={"wait": wait, "timeoutSeconds": timeout},
            timeout=timeout + 10,
        )

    async def cancel_invocation(self, invocation_id, *, actor="cli:lw"):
        return await self.request(
            "POST",
            await self.invocation_route(invocation_id) + "/invocations/" + quote(invocation_id, safe="")
            + "/cancel",
            actor=actor,
        )

    async def describe_approval(self, invocation_id, *, actor="cli:lw", human_review=False):
        if invocation_id.startswith("approval-"):
            value = await self.request(
                "GET", "/api/v1/approvals/" + quote(invocation_id, safe=""), actor=actor
            )
            promotion = value.get("details", {}).get("controllerPromotionId")
            if human_review and promotion:
                value["humanReview"] = await self.request(
                    "GET", "/api/v1/controller-promotions/" + quote(promotion, safe="") + "/review",
                    actor=actor,
                )
            return value
        return await self.get_invocation(invocation_id)

    async def approve_invocation(self, invocation_id, *, deny=False, note="", actor="cli:lw"):
        path = (
            "/api/v1/approvals/" + quote(invocation_id, safe="") + "/decision"
            if invocation_id.startswith("approval-")
            else await self.invocation_route(invocation_id) + "/invocations/" + quote(invocation_id, safe="")
            + "/approve"
        )
        return await self.request(
            "POST",
            path,
            actor=actor,
            json={"decision": "deny" if deny else "approve", "note": note},
        )

    async def wait_until(self, key, prop, op, value, *, timeout=120, actor="cli:lw",
                         planId=None, stepId=None, planRevision=None, by=None):
        return await self.request(
            "GET",
            await self.route(key) + "/wait",
            actor=actor,
            timeout=timeout + 10,
            params={
                "key": key,
                "property": prop,
                "op": op,
                "value": json.dumps(value, ensure_ascii=False, allow_nan=False),
                "timeoutSeconds": timeout,
                **({"by": by} if by is not None else {}),
                **plan_fields(planId, stepId, planRevision),
            },
        )

    async def check_condition(self, key, prop, op, value, *, actor="cli:lw",
                              planId=None, stepId=None, planRevision=None, by=None):
        return await self.request("POST", await self.route(key) + "/check", actor=actor, json={
            "key": key, "property": prop, "op": op, "value": value,
            **({"by": by} if by is not None else {}),
            **plan_fields(planId, stepId, planRevision),
        })

    async def plan_create(self, document, *, actor="cli:lw"):
        session_id = (await self.route()).rsplit("/", 1)[-1]
        return await self.request("POST", "/api/v1/plans", actor=actor,
                                  json={**document, "sessionId": session_id})

    async def plan_revise(self, id, document, *, actor="cli:lw"):
        if document.get("kind") == "training":
            return await self.request("POST", "/api/v1/plans/" + quote(id, safe="") + "/revisions",
                                      actor=actor, json=document)
        session_id = (await self.route()).rsplit("/", 1)[-1]
        return await self.request("POST", "/api/v1/plans/" + quote(id, safe="") + "/revisions", actor=actor,
                                  json={**document, "sessionId": session_id})

    async def plan_submit(self, document, *, actor="cli:lw"):
        if document.get("kind") != "training":
            raise ValueError("plan submit requires kind: training")
        return await self.request("POST", "/api/v1/plans", actor=actor, json=document)

    async def job_start(self, planId, seed=None, planRevision=None, requestId=None, *, actor="cli:lw"):
        return await self.request("POST", "/api/v1/jobs", actor=actor, json={
            "planId": planId, "seed": seed, "planRevision": planRevision,
            "requestId": requestId or uuid.uuid4().hex,
        })

    async def job_list(self, plan=None, status=None, kind=None):
        values = {key: value for key, value in {"plan": plan, "status": status, "kind": kind}.items()
                  if value is not None}
        return (await self.request("GET", "/api/v1/jobs", params=values))["jobs"]

    async def job_show(self, id):
        return await self.request("GET", "/api/v1/jobs/" + quote(id, safe=""))

    async def job_cancel(self, id, *, actor="cli:lw"):
        return await self.request("POST", "/api/v1/jobs/" + quote(id, safe="") + "/cancel", actor=actor)

    async def job_logs(self, id, after=0, follow=False):
        return await self.request("GET", "/api/v1/jobs/" + quote(id, safe="") + "/logs",
                                  params={"after": after, "timeoutSeconds": 10 if follow else 0}, timeout=15)

    async def tensorboard(self, *, actor="cli:lw"):
        return await self.request("POST", "/api/v1/tensorboard", actor=actor, timeout=40)

    async def node_list(self):
        return (await self.request("GET", "/api/v1/nodes"))["nodes"]

    async def policy_create(self, checkpoint, *, actor="cli:lw"):
        return await self.request("POST", "/api/v1/policies", actor=actor, json={"checkpoint": checkpoint})

    async def policy_list(self):
        return (await self.request("GET", "/api/v1/policies"))["policies"]

    async def policy_show(self, id):
        return await self.request("GET", self._policy_path(id))

    async def policy_promote(self, id, scene, evaluation=None, observation_check=None,
                             plan=None, revision=None, request_id=None, *, actor="cli:lw"):
        self._policy_path(id)
        return await self.controller_promotion_request(
            id, scene, evaluation, observation_check, plan, revision, request_id, actor=actor
        )

    async def controller_promotion_request(self, subject, scene, evaluation=None, observation_check=None,
                                          plan=None, revision=None, request_id=None, *, actor="cli:lw"):
        body = {key: value for key, value in {
            "subject": subject, "scene": scene, "evaluationId": evaluation,
            "observationCheck": observation_check, "planId": plan, "planRevision": revision,
            "requestId": request_id,
        }.items() if value is not None}
        return await self.request("POST", "/api/v1/controller-promotions", actor=actor, json=body)

    async def controller_promotion_show(self, id):
        return await self.request("GET", "/api/v1/controller-promotions/" + quote(id, safe=""))

    @staticmethod
    def _policy_path(id):
        match = re.fullmatch(r"policy/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)", id)
        if not match:
            raise ValueError("Select an exact policy/name@revision")
        return f"/api/v1/policies/{match[1]}/{match[2]}"

    async def policy_check_observations(self, id, artifact, *, actor="cli:lw"):
        return await self.request("POST", self._policy_path(id) + "/observation-checks",
                                  actor=actor, json={"artifact": artifact})

    async def policy_observation_checks(self, id):
        return (await self.request("GET", self._policy_path(id) + "/observation-checks"))["checks"]

    async def policy_observation_check_show(self, id, check):
        path = self._policy_path(id) + "/observation-checks/" + quote(check, safe="")
        return await self.request("GET", path)

    async def policy_observation_check_verify_source(self, id, check, artifact, *, actor="cli:lw"):
        path = self._policy_path(id) + "/observation-checks/" + quote(check, safe="") + "/verify-source"
        return await self.request("POST", path, actor=actor, json={"artifact": artifact})

    async def cost_query(self, since="7d", by=None, view="summary", agent=None, model=None,
                         node=None, goal=None, plan=None, start=None, end=None):
        projection = "breakdown" if by else view
        params = {key: value for key, value in {"range": since, "by": by, "agent": agent, "model": model,
                                                "node": node, "goal": goal, "plan": plan,
                                                "start": start, "end": end}.items() if value is not None}
        return await self.request("GET", f"/api/v1/cost/{projection}",
                                  params={} if projection == "anomalies" else params)

    async def plan_show(self, id, revision=None):
        return await self.request("GET", "/api/v1/plans/" + quote(id, safe=""),
                                  params={"revision": revision} if revision is not None else {})

    async def plan_list(self, status=None):
        params = {"status": status} if status is not None else {}
        if self.token and self.token.startswith("lw-case-") and self._session_explicit:
            params["sessionId"] = self._session_id
        return (await self.request("GET", "/api/v1/plans",
                                   params=params))["plans"]

    async def session_control(self, action, *, count=1, actor="cli:lw"):
        return await self.request(
            "POST",
            await self.route() + "/commands",
            actor=actor,
            json={
                "requestId": uuid.uuid4().hex,
                "command": action,
                "args": {"count": count} if action == "step" else {},
            },
        )

    async def recordings(self):
        return await self.request("GET", "/api/v1/recordings")

    async def watch(self, key=None) -> AsyncIterator[dict]:
        path = await self.route(key) + "/events"
        initial = await self.request("GET", path, params={"after": 0, "timeoutSeconds": 0})
        cursor = initial["lastSeq"]
        while True:
            result = await self.request(
                "GET", path, params={"after": cursor, "timeoutSeconds": 30}, timeout=40
            )
            if result["gap"]:
                raise GatewayError("Event history gap; restart watch", detail={"after": cursor})
            cursor = result["lastSeq"]
            for item in result["events"]:
                message = item["message"]
                if message["type"] == "entity.observations":
                    changes = [change for change in message["changes"] if key is None or change["key"] == key]
                    if changes:
                        yield message | {"changes": changes}
                elif message["type"] in ("entity.event", "invocation.update"):
                    if key is None or message["entityKey"] == key:
                        yield message


def exit_code(document):
    if isinstance(document, dict):
        if document.get("schema") == "lw.validation/1" and any(
            check["status"] == "fail" for check in document["checks"]
        ):
            return 3
        if document.get("timedOut"):
            return 4
        if document.get("satisfied") is False:
            return 2
        status = document.get("status")
        if status == "rejected" or document.get("ack", {}).get("status") == "rejected":
            return 2
        if status in ("failed", "cancelled"):
            return 3
    return 0
