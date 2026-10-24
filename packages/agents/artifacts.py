"""A fixed evidence whitelist; private worker/transport directories are never browsable."""

import hashlib
import re

from packages.info.plans import PlanError

FILES = {
    "platform-trace": ("trace.jsonl", "platform", "application/x-ndjson"),
    "sdk-messages": ("worker/trace.jsonl", "sdk", "application/x-ndjson"),
    "final-state": ("final.json", "platform", "application/json"),
    "score": ("score.json", "platform", "application/json"),
    "usage": ("usage.json", "trusted-transport", "application/json"),
}


def evidence_file(root, record, case_id, kind):
    if (
        kind not in FILES
        or not re.fullmatch(r"case-[0-9]{4,8}", case_id)
        or not any(case["caseRunId"] == case_id for case in record["caseRuns"])
    ):
        raise PlanError("unknown_agent_evidence", "Unknown public Agent case evidence", status=404)
    directory = (root / record["jobId"] / "cases" / case_id).resolve()
    path = directory / FILES[kind][0]
    if (
        not path.is_file()
        or path.stat().st_nlink != 1
        or any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents))
        or not path.resolve().is_relative_to(directory)
    ):
        raise PlanError("unknown_agent_evidence", "Unknown public Agent case evidence", status=404)
    return path


def publish_case(root, record, case_id):
    result = []
    for kind, (relative, source, _) in FILES.items():
        if kind == "sdk-messages" and record.get("cleanupIncomplete"):
            continue
        try:
            path = evidence_file(root, record, case_id, kind)
        except PlanError:
            continue
        content = path.read_bytes()
        identity = case_id + "-" + kind
        result.append(
            {
                "id": identity,
                "path": f"cases/{case_id}/{relative}",
                "caseRunId": case_id,
                "kind": kind,
                "source": source,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "url": f"/api/v1/agent-runs/{record['jobId']}/artifacts/{identity}",
            }
        )
    return result


def resolve_artifact(root, record, identity):
    artifact = next((value for value in record["artifacts"] if value["id"] == identity), None)
    if artifact is None:
        raise PlanError("unknown_agent_evidence", "Unknown public Agent case evidence", status=404)
    path = evidence_file(root, record, artifact["caseRunId"], artifact["kind"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
        raise PlanError(
            "agent_evidence_changed", "The evidence prefix changed; refresh its manifest", status=409
        )
    return path, FILES[artifact["kind"]][2]
