"""Owned metadata worker. All catalog mutations cross the scoped HTTP seam.

The configured runtime consumes one JSON request on stdin and emits one JSON
receipt on stdout. A capture import is never used as an execution substitute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

SKILLS_VERSION = "lw.catalog-maintenance/1"
_CANCEL = threading.Event()


def maintenance_skill():
    path = Path(__file__).resolve().parents[2] / "skills" / "lab-catalog-maintain" / "SKILL.md"
    return path.read_text(encoding="utf-8")


def profile_from_environment():
    value = os.environ.get("LW_CATALOG_AGENT_PROFILE")
    if not value:
        return None
    return normalize_profile(json.loads(value))


def normalize_profile(profile):
    profile = dict(profile) if isinstance(profile, dict) else profile
    if (
        not isinstance(profile, dict)
        or not isinstance(profile.get("name"), str)
        or not profile["name"].strip()
        or not isinstance(profile.get("model"), str)
        or not profile["model"].strip()
        or not isinstance(profile.get("command"), list)
        or not 1 <= len(profile["command"]) <= 64
        or any(not isinstance(part, str) or not part or len(part) > 4096 for part in profile["command"])
    ):
        raise ValueError("Agent profile requires name, model and an argv command")
    for key, default, maximum in (("timeoutSeconds", 120, 1800), ("maxOutputBytes", 1048576, 4194304)):
        selected = profile.get(key, default)
        if isinstance(selected, bool) or not isinstance(selected, int) or not 1 <= selected <= maximum:
            raise ValueError(f"Agent profile {key} is invalid")
        profile[key] = selected
    return profile


def public_profile(profile):
    if profile is None:
        return {"configured": False, "profile": None, "model": None, "fingerprint": None}
    encoded = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    return {
        "configured": True,
        "profile": profile["name"],
        "model": profile["model"],
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
    }


def execute_agent(profile, request, *, root, cancel=_CANCEL):
    """Run the selected argv, bounding time and captured bytes through its handle."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    budget = request.get("remaining", {})
    timeout = min(profile["timeoutSeconds"], budget.get("seconds", profile["timeoutSeconds"]))
    with (
        tempfile.TemporaryFile() as inputs,
        tempfile.TemporaryFile() as output,
        tempfile.TemporaryFile() as errors,
    ):
        inputs.write(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        inputs.seek(0)
        env = os.environ.copy()
        # The runtime receives its declared context in stdin, not the worker's
        # authorization credential or a database path.
        env.pop("LW_CATALOG_RUN_TOKEN", None)
        env.pop("LW_CATALOG_AGENT_PROFILE", None)
        process = subprocess.Popen(
            profile["command"],
            stdin=inputs,
            stdout=output,
            stderr=errors,
            cwd=root,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            while process.poll() is None:
                if cancel.is_set():
                    raise InterruptedError("Maintenance was cancelled")
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Agent batch exceeded its runtime limit")
                if (
                    os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size
                    > profile["maxOutputBytes"]
                ):
                    raise ValueError("Agent receipt exceeded its byte limit")
                cancel.wait(0.02)
            if (
                os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size
                > profile["maxOutputBytes"]
            ):
                raise ValueError("Agent receipt exceeded its byte limit")
            if process.returncode:
                raise RuntimeError(f"Agent process exited with code {process.returncode}")
            output.seek(0)
            original = output.read(profile["maxOutputBytes"] + 1).decode("utf-8")
            receipt = json.loads(original)
            if not isinstance(receipt, dict) or not isinstance(receipt.get("results"), list):
                raise ValueError("Agent receipt requires structured results")
            receipt["execution"] = {
                "profile": profile["name"],
                "model": profile["model"],
                "seconds": time.monotonic() - started,
                "originalSha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                "original": original,
            }
            return receipt
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


class Worker:
    def __init__(self, url, run_id, cache_root, control_file=None):
        self.run_id = run_id
        self.cache_root = Path(cache_root)
        self.client = httpx.Client(
            base_url=url.rstrip("/") + "/api/v1/library/catalog/maintenance",
            headers={"Authorization": "Bearer " + os.environ["LW_CATALOG_RUN_TOKEN"]},
            timeout=60,
            follow_redirects=False,
            trust_env=False,
        )
        self.base = "/runs/" + run_id
        if control_file:

            def watch():
                while not _CANCEL.is_set():
                    if Path(control_file).exists():
                        _CANCEL.set()
                        return
                    _CANCEL.wait(0.1)

            threading.Thread(target=watch, daemon=True, name="catalog-worker-cancel").start()

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def read_source(self, record):
        from packages.catalog_metadata.hub import HFHubReader

        previous_usage = record.get("sourceUsage", {})
        budgets = dict(record["budgets"])
        budgets["seconds"] = max(0, budgets["seconds"] - record["usage"].get("seconds", 0))
        for key in ("requests", "networkBytes"):
            budgets[key] = max(0, budgets[key] - previous_usage.get(key, 0))
        budgets["previewBytes"] = max(
            0,
            budgets["previewBytes"]
            - (previous_usage.get("previewBytes", 0) - previous_usage.get("embeddedPreviewBytes", 0)),
        )
        updates = []
        saw_manifest = False

        def cumulative(usage):
            result = dict(usage)
            for key in ("requests", "networkBytes", "previewBytes", "embeddedPreviewBytes", "cacheBytes"):
                result[key] = previous_usage.get(key, 0) + usage.get(key, 0)
            return result

        def flush():
            if updates:
                self.request("POST", self.base + "/extracted", json={"entries": list(updates)})
                updates.clear()

        def progress(event):
            nonlocal record, saw_manifest
            usage = event.get("usage", event)
            self.request("POST", self.base + "/checkpoint", json={"usage": cumulative(usage)})
            if "inventory" in event:
                saw_manifest = True
                inventory = event["inventory"]
                identities = sorted(entry["upstreamId"] for entry in inventory["entries"])
                identity_digest = hashlib.sha256(
                    json.dumps(identities, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if record.get("inventoryId"):
                    if identity_digest != record.get("inventoryIdentityDigest"):
                        raise ValueError("The resumed manifest changed its fixed identity set")
                else:
                    for offset in range(0, len(inventory["entries"]), 24):
                        self.request(
                            "POST",
                            self.base + "/inventory",
                            json={
                                "offset": offset,
                                "entries": inventory["entries"][offset : offset + 24],
                            },
                        )
                    record = self.request(
                        "POST",
                        self.base + "/inventory/complete",
                        json={
                            **{key: value for key, value in inventory.items() if key != "entries"},
                            "usage": cumulative(usage),
                            "extractionComplete": False,
                        },
                    )
                    self.request("POST", self.base + "/publish", json={})
            if "entry" in event:
                updates.append(
                    {
                        **event["entry"],
                        "extractionStatus": event.get(
                            "extractionStatus", event["entry"].get("extractionStatus", "extracted")
                        ),
                    }
                )
                if len(updates) >= 24:
                    flush()

        self.request("POST", self.base + "/phase", json={"phase": "enumerating"})
        reader = HFHubReader(cache_root=self.cache_root, cancel=_CANCEL, budgets=budgets, progress=progress)
        try:
            pinned = record.get("sourceRevision") or reader.pin(
                record["collection"], record.get("requestedRevision")
            )
            self.request(
                "POST",
                self.base + "/checkpoint",
                json={
                    "usage": cumulative(reader.usage),
                    "sourceRevision": pinned,
                },
            )
            inventory = reader.enumerate(record["collection"], pinned)
            flush()
            if not saw_manifest:
                for offset in range(0, len(inventory["entries"]), 24):
                    self.request(
                        "POST",
                        self.base + "/inventory",
                        json={
                            "offset": offset,
                            "entries": inventory["entries"][offset : offset + 24],
                        },
                    )
            record = self.request(
                "POST",
                self.base + "/inventory/complete",
                json={
                    **{key: value for key, value in inventory.items() if key != "entries"},
                    "usage": cumulative(inventory["usage"]),
                    "refresh": saw_manifest,
                    "extractionComplete": True,
                },
            )
            return record, False
        except Exception as exc:
            from packages.catalog_metadata import MetadataError

            if isinstance(exc, MetadataError) and record.get("inventoryId") and not _CANCEL.is_set():
                flush()
                usage = cumulative(exc.details.get("usage", reader.usage))
                record = self.request(
                    "POST",
                    self.base + "/inventory/complete",
                    json={
                        "revision": record["sourceRevision"],
                        "complete": True,
                        "refresh": True,
                        "usage": usage,
                        "extractionComplete": False,
                        "sourceError": {"code": exc.code, "message": str(exc)[:500]},
                    },
                )
                return record, True
            details = getattr(exc, "details", None)
            if isinstance(details, dict) and "usage" in details:
                details["usage"] = cumulative(details["usage"])
            raise
        finally:
            reader.close()

    def run(self):
        record = self.request("GET", self.base)
        profile = profile_from_environment()
        if public_profile(profile) != record["runtime"]:
            raise ValueError("Configured Agent profile changed; resume with its recorded profile")
        if record.get("inventoryId") is None or not record.get("extractionComplete", True):
            record, stopped = self.read_source(record)
            if stopped:
                self.request("POST", self.base + "/finish", json={})
                return
        self.request("POST", self.base + "/phase", json={"phase": "organizing"})
        if profile is not None:
            while not _CANCEL.is_set():
                batch = self.request("POST", self.base + "/claim", json={"limit": 24})
                if not batch.get("entries"):
                    break
                execution_started = False
                try:
                    skill = maintenance_skill()
                    if (
                        batch["versions"].get("skillsDigest")
                        != hashlib.sha256(skill.encode("utf-8")).hexdigest()
                    ):
                        raise ValueError("Maintenance skill changed; start a new run with the new version")
                    self.request("POST", self.base + "/batches/" + batch["id"] + "/execution", json={})
                    execution_started = True
                    receipt = execute_agent(
                        profile,
                        {
                            "schema": SKILLS_VERSION,
                            "runId": self.run_id,
                            "batchId": batch["id"],
                            "inputKey": batch["inputKey"],
                            "versions": batch["versions"],
                            "entries": batch["entries"],
                            "remaining": batch["remaining"],
                            "skill": skill,
                            "instructions": (
                                "Organize bilingual names, aliases, summaries, categories and tags "
                                "from the supplied source evidence. Preserve source facts and unknowns. "
                                "Return results with upstreamId, inputDigest and fields; report actual "
                                "usage and nativeSessionId. Never invent license, "
                                "dimensions, collision or verification facts."
                            ),
                        },
                        root=self.cache_root.parent / "maintenance" / self.run_id / "runtime",
                        cancel=_CANCEL,
                    )
                    state = self.request(
                        "POST",
                        self.base + "/batches/" + batch["id"] + "/submit",
                        json={
                            "inputKey": batch["inputKey"],
                            **receipt,
                        },
                    )
                    if state.get("budgetExhausted"):
                        break
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    self.request(
                        "POST",
                        self.base + "/batches/" + batch["id"] + "/error",
                        json={
                            "code": type(exc).__name__,
                            "message": str(exc)[:500],
                            "executionStarted": execution_started,
                        },
                    )
        if not _CANCEL.is_set():
            self.request("POST", self.base + "/finish", json={})

    def close(self):
        self.client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--control-file")
    args = parser.parse_args(argv)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _CANCEL.set())
    worker = Worker(args.url, args.run, args.cache_root, args.control_file)
    try:
        worker.run()
    except Exception as exc:
        try:
            worker.request(
                "POST",
                worker.base + "/error",
                json={
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc)[:500],
                    "usage": getattr(exc, "details", {}).get("usage"),
                },
            )
        except httpx.HTTPError:
            pass
        return 1
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
