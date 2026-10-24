"""Immutable automatic snapshots and human override audit; Markdown is a public projection."""

import json
import os
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path

import yaml

from packages.info.plans import PlanError
from packages.library.archive import destination_parent
from packages.library.transactions import durable_json
from packages.training.notebook_contract import NotebookEntry, Override


class LabNotebooks:
    def __init__(self, state_root, library_root):
        self.path = Path(state_root) / "notebook-records.json"
        self.library_root = Path(library_root).resolve()
        self.entries = {}
        self._lock = threading.RLock()

    def startup(self):
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved["version"] != 1:
                raise ValueError("Unsupported Lab Notebook version")
            self.entries = {
                entry["entryId"]: NotebookEntry.model_validate(entry).model_dump(mode="json", by_alias=True)
                for entry in saved["entries"]
            }
        for identity in self.entries:
            self.publish(identity)

    def _commit(self, entries):
        durable_json(self.path, {"version": 1, "entries": list(entries.values())})
        self.entries = entries

    def add(self, entry):
        entry = NotebookEntry.model_validate(entry).model_dump(mode="json", by_alias=True)
        with self._lock:
            if entry["entryId"] not in self.entries:
                self._commit(self.entries | {entry["entryId"]: entry})
            # Reconciliation never re-judges a committed automatic snapshot or drops audits.
            self.publish(entry["entryId"])
            return self.document(entry["entryId"])

    def document(self, identity):
        result = deepcopy(self.entries[identity])
        for hypothesis in result["hypotheses"]:
            audits = hypothesis["overrides"]
            hypothesis["effectiveVerdict"] = (
                audits[-1]["verdict"] if audits else hypothesis["automatic"]["verdict"]
            )
        return result

    def list(self, goal, *, refuted=False):
        with self._lock:
            entries = [
                self.document(identity) for identity, entry in self.entries.items() if entry["goalId"] == goal
            ]
            entries.sort(key=lambda entry: (entry["at"], entry["evaluationId"]))
            if refuted:
                entries = [
                    entry
                    for entry in entries
                    if any(h["effectiveVerdict"] == "refuted" for h in entry["hypotheses"])
                ]
            return entries

    def override(self, goal, identity, hypothesis_id, verdict, actor, note, request_id):
        if actor.kind == "agent":
            raise PlanError(
                "override_requires_human", "A person must override a Hypothesis judgement", status=403
            )
        with self._lock:
            source = self.entries.get(identity)
            if source is None or source["goalId"] != goal:
                raise PlanError("unknown_notebook_entry", "Unknown Lab Notebook entry", status=404)
            entry = deepcopy(source)
            selected = next(
                (item for item in entry["hypotheses"] if item["definition"]["id"] == hypothesis_id), None
            )
            if selected is None:
                raise PlanError("unknown_hypothesis", "Unknown recorded Hypothesis", status=404)
            for item in entry["hypotheses"]:
                for audit in item["overrides"]:
                    if audit["requestId"] == request_id:
                        if (
                            item is not selected
                            or audit["verdict"] != verdict
                            or audit["note"] != note
                            or audit["actor"] != actor.model_dump(mode="json")
                        ):
                            raise PlanError("idempotency_conflict", "Override retry has different content")
                        self.publish(identity)
                        return self.document(identity)
            audit = Override(
                requestId=request_id, verdict=verdict, actor=actor, atMs=time.time() * 1000, note=note
            ).model_dump(mode="json")
            selected["overrides"].append(audit)
            self._commit(self.entries | {identity: entry})
            self.publish(identity)
            return self.document(identity)

    def publish(self, identity):
        with self._lock:
            self._publish(identity)

    def _publish(self, identity):
        entry = self.document(identity)
        directory = destination_parent(
            self.library_root, ("notebooks", entry["goalId"].removeprefix("goal/")), create=True
        )
        path = directory / (identity + ".md")
        if path.is_symlink() or path.is_junction():
            raise ValueError("Notebook projection cannot be a filesystem link")
        header = yaml.safe_dump(entry, allow_unicode=True, sort_keys=False)
        text = f"---\n{header}---\n\n# {entry['plan']['title']}\n\n"
        text += f"Plan: {entry['plan']['id']} r{entry['plan']['revision']} · "
        text += f"Evaluation: {entry['evaluationId']}\n\n"
        text += f"Candidate acceptance: {entry['reportedDecision'] or 'unavailable'}\n\n"
        text += f"Human deployment approval: {entry['deployment']['status']}\n\n"
        for hypothesis in entry["hypotheses"]:
            definition = hypothesis["definition"]
            text += f"## {definition['id']} · {hypothesis['effectiveVerdict']}\n\n"
            text += definition["cause"] + "\n\n" + definition["change"] + "\n\n"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
