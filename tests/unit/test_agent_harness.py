"""Harness transport proves immutable forks, safe bytes and monotonic allocation."""

import io
import json
import os
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from packages.library.agents import AgentLibrary, harness_files, pack_harness
from packages.library.common import BUILTIN_ROOT, LibraryError


def library(tmp_path):
    return AgentLibrary({"builtin": BUILTIN_ROOT, "user": tmp_path / "library"})


def changed_archive(tmp_path, text="Inspect final state before reporting completion.\n"):
    path = tmp_path / "inputs"
    path.mkdir(exist_ok=True)
    (path / "AGENTS.md").write_text(text, encoding="utf-8")
    (path / "skills.lock").write_text("skills: []\n", encoding="utf-8")
    (path / "mcp.yaml").write_text("servers: {}\n", encoding="utf-8")
    return pack_harness(path)


def tar_entries(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(content) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "../outside"
            archive.addfile(info, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
    return stream.getvalue()


def test_snapshot_export_import_fork_diff_and_reference_do_not_promote(tmp_path):
    agents = library(tmp_path)
    original = agents.export("lab-operator", 1)
    created = agents.create("fork", original)
    assert created == {"id": "agent/fork", "title": "fork", "model": None, "thinking": None,
                       "referenceVersion": 1, "currentVersion": 1}
    assert agents.export("fork", 1) == original
    new = agents.snapshot("fork", changed_archive(tmp_path))
    assert (new["version"], new["baseVersion"], new["decision"]) == (2, 1, "candidate")
    assert agents.show("fork")["referenceVersion"] == 1
    assert agents.show("fork")["currentVersion"] == 2
    assert agents.snapshot("fork")["version"] == 2
    patch = agents.diff("fork", 2, 1)
    assert any("Inspect final state" in change["patch"] for change in patch["changes"])
    restored = agents.snapshot("fork", original)
    assert (restored["version"], restored["baseVersion"]) == (3, 2)
    assert agents.export("fork", 1) == agents.export("fork", 3) == original
    assert agents.export("fork", 2) != original
    assert agents.show("fork")["referenceVersion"] == 1
    assert agents.diff("fork", 3, 1)["changes"] == []
    with pytest.raises(LibraryError) as duplicate:
        agents.create("fork", original)
    assert duplicate.value.detail["code"] == "agent_conflict"


def test_failed_reserved_version_is_never_reused_and_rejected_candidate_is_retained(tmp_path, monkeypatch):
    agents = library(tmp_path)
    agents.create("fork", agents.export("lab-operator", 1))
    imported = changed_archive(tmp_path)
    original_publish = agents._publish
    monkeypatch.setattr(agents, "_publish", lambda *args: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError):
        agents.snapshot("fork", imported)
    assert agents.show("fork")["currentVersion"] == 1
    assert json.loads((agents.user_root / ".agent-versions.json").read_bytes()) == {"fork": 2}
    monkeypatch.setattr(agents, "_publish", original_publish)
    assert agents.snapshot("fork", imported)["version"] == 3
    # A later acceptance workflow may mark a Candidate rejected; its archive remains immutable.
    record_path = agents.user_root / "agents/fork/versions/v3.json"
    record_path.write_text(json.dumps(agents.version("fork", 3) | {"decision": "rejected"}), encoding="utf-8")
    assert agents.snapshot("fork", agents.export("fork", 1))["version"] == 4
    assert agents.version("fork", 3)["decision"] == "rejected"
    assert [record["version"] for record in agents.versions("fork")] == [1, 3, 4]


def test_concurrent_identical_and_distinct_imports_serialize_without_overwrite(tmp_path):
    agents = library(tmp_path)
    agents.create("fork", agents.export("lab-operator", 1))
    imported = changed_archive(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: agents.snapshot("fork", imported), range(2)))
    assert [item["version"] for item in results] == [2, 2]
    original = agents.export("fork", 2)
    another = changed_archive(tmp_path, "A distinct safe instruction.\n")
    # Neither equals the current snapshot; both allocate independently under the same lock.
    third = changed_archive(tmp_path, "Another safe instruction.\n")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda data: agents.snapshot("fork", data), (another, third)))
    assert sorted(item["version"] for item in results) == [3, 4]
    assert agents.export("fork", 2) == original


@pytest.mark.parametrize("name,kind", [
    ("../outside", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
    ("C:/drive", tarfile.REGTYPE), ("hooks/link", tarfile.SYMTYPE),
    ("hooks/hard", tarfile.LNKTYPE), (".env", tarfile.REGTYPE),
    ("hooks/.env", tarfile.REGTYPE), ("agent.yaml", tarfile.REGTYPE),
    ("AGENTS.md.", tarfile.REGTYPE), ("hooks/CON", tarfile.REGTYPE),
])
def test_rejects_unsafe_tar_without_disclosing_content_or_overwriting_existing(tmp_path, name, kind):
    agents = library(tmp_path)
    agents.create("fork", agents.export("lab-operator", 1))
    original = agents.export("fork", 1)
    data = tar_entries([(name, b"private-canary-864209", kind)])
    with pytest.raises(LibraryError) as rejected:
        agents.snapshot("fork", data)
    assert "private-canary-864209" not in json.dumps(rejected.value.detail)
    assert agents.export("fork", 1) == original
    assert agents.show("fork")["currentVersion"] == 1
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("text", [
    "API_KEY=private-canary-864209", 'apiKey: "private-canary-864209"',
    '{"api_key":"private-canary-864209"}', "AWS_ACCESS_KEY_ID=private-canary-864209",
    "Bearer private-canary-864209", "-----BEGIN PRIVATE KEY-----\nprivate-canary-864209",
])
def test_credentials_in_any_authoring_file_fail_closed_with_generic_errors(tmp_path, text):
    with pytest.raises(LibraryError) as rejected:
        changed_archive(tmp_path, text)
    assert "private-canary-864209" not in str(rejected.value.detail)


def test_mcp_environment_requires_references_and_filesystem_hardlinks_are_rejected(tmp_path):
    changed_archive(tmp_path)
    path = tmp_path / "inputs"
    (path / "mcp.yaml").write_text(
        "servers:\n  lab:\n    command: lw\n    env:\n      TOKEN: literal-canary\n"
    )
    with pytest.raises(LibraryError):
        harness_files(path)
    (path / "mcp.yaml").write_text("servers:\n  lab:\n    command: lw\n    env:\n      TOKEN: ${LAB_TOKEN}\n")
    assert harness_files(path) == ["AGENTS.md", "mcp.yaml", "skills.lock"]
    os.link(path / "AGENTS.md", tmp_path / "linked-instructions")
    with pytest.raises(LibraryError):
        harness_files(path)


def test_dotenv_is_rejected_before_its_contents_are_opened(tmp_path, monkeypatch):
    changed_archive(tmp_path)
    path = tmp_path / "inputs"
    (path / ".env").write_text("API_KEY=private-canary-864209", encoding="utf-8")
    original = Path.read_text

    def guarded(file, *args, **kwargs):
        assert file.name != ".env", "Snapshot must not open credential files"
        return original(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    with pytest.raises(LibraryError):
        harness_files(path)


def test_mcp_credential_arguments_require_environment_references(tmp_path):
    changed_archive(tmp_path)
    path = tmp_path / "inputs"
    (path / "mcp.yaml").write_text(
        "servers:\n  tool:\n    command: tool\n    args: [--api-key, literal-canary]\n", encoding="utf-8"
    )
    with pytest.raises(LibraryError):
        harness_files(path)
    (path / "mcp.yaml").write_text(
        "servers:\n  tool:\n    command: tool\n    args: [--api-key, '${LAB_KEY}']\n", encoding="utf-8"
    )
    assert harness_files(path)


def test_archive_and_version_metadata_tampering_is_not_returned(tmp_path):
    agents = library(tmp_path)
    agents.create("fork", agents.export("lab-operator", 1))
    record = agents.user_root / "agents/fork/versions/v1.json"
    value = json.loads(record.read_bytes()) | {"private": "private-canary-864209"}
    record.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(LibraryError) as rejected:
        agents.version("fork", 1)
    assert "private-canary-864209" not in str(rejected.value.detail)
