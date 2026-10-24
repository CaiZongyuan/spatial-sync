"""SF2 acquisition worker: fixture mode, LFS rejection, bounds and JSON artifacts."""

import json
import subprocess
import sys
from pathlib import Path

from factory.fixture import NOTICE as FIXTURE_NOTICE
from factory.fixture import build_source
from factory.simready import LFS_POINTER_MARKER, REVISION

REPO_ROOT = Path(__file__).resolve().parents[2]


def job_for(tmp_path, candidate, source_root, *, expected=None, limits=None):
    job_dir = tmp_path / "work"
    job_dir.mkdir(parents=True, exist_ok=True)
    return {
        "schema": "lw.acquisition.worker-job/1",
        "acquisitionId": "acq-f" * 8 + "0",
        "provider": "simready",
        "packageId": candidate.package,
        "revision": REVISION,
        "recipe": "simready-newton-usdpreview-v1",
        "cacheRoot": str(tmp_path / "cache"),
        "stagingDir": str(tmp_path / "staging"),
        "progressFile": str(job_dir / "progress.json"),
        "resultFile": str(job_dir / "result.json"),
        "limits": limits
        or {"sourcePackageBytes": 64 * 1024 * 1024, "adaptedPackageBytes": 64 * 1024 * 1024},
        "expected": expected
        or {
            "files": candidate.expected_files,
            "bytes": candidate.expected_bytes,
            "license": "CC0-1.0",
            "localAssetId": candidate.asset_id,
            "title": candidate.title,
            "description": candidate.description,
            "role": candidate.role,
        },
        "sourceRoot": str(source_root),
    }


def run_worker(tmp_path, job):
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "factory.acquisition_worker", "--job", str(job_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )


def load_result(job):
    return json.loads(Path(job["resultFile"]).read_text(encoding="utf-8"))


def test_fixture_job_stages_package_with_license_and_source_notice(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    job = job_for(tmp_path, candidate, source)
    completed = run_worker(tmp_path, job)
    assert completed.returncode == 0, completed.stderr
    result = load_result(job)
    assert result["schema"] == "lw.acquisition.worker/1"
    assert result["status"] == "succeeded"
    assert result["error"] is None
    package = Path(result["packageDir"])
    assert package == (tmp_path / "staging" / "package").resolve()
    for required in ("asset.yaml", "LICENSE.txt", "NOTICE", "SOURCE-NOTICE", "provenance.json"):
        assert (package / required).is_file(), required
    assert "CC0-1.0 synthetic fixture" in (package / "LICENSE.txt").read_text(encoding="utf-8")
    assert (package / "SOURCE-NOTICE").read_text(encoding="utf-8") == FIXTURE_NOTICE
    generated = (package / "NOTICE").read_text(encoding="utf-8")
    assert generated != FIXTURE_NOTICE
    assert "modifications" in generated.lower()
    assert result["notices"]["sourceNotice"]["source"] == "NOTICE"
    assert result["notices"]["sourceNotice"]["file"] == "SOURCE-NOTICE"
    assert result["source"]["license"]["name"] == "CC0-1.0"
    assert result["source"]["notices"] == ["NOTICE"]
    assert result["adaptation"]["status"] == "pass"
    assert result["usage"]["sourceBytes"] == candidate.expected_bytes
    assert result["usage"]["transferBytes"] == 0
    assert result["usage"]["adaptedBytes"] == sum(
        path.stat().st_size for path in package.rglob("*") if path.is_file()
    )
    progress = json.loads(Path(job["progressFile"]).read_text(encoding="utf-8"))
    assert progress["acquisitionId"] == job["acquisitionId"]
    assert progress["phase"] == "validating"
    assert progress["totalFiles"] > 0
    assert progress["totalBytes"] == progress["completedBytes"] > 0
    assert progress["completedFiles"] == progress["totalFiles"]
    assert progress["updatedAt"]
    # The fixture cache copy is retained for reuse; no staged package is left on failure paths.
    assert (tmp_path / "cache" / candidate.package / ".metadata" / "LICENSE.txt").is_file()


def test_lfs_pointer_content_fails_with_lfs_pointer(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    pointer = f"{LFS_POINTER_MARKER}\noid sha256:{'0' * 64}\nsize 12345\n"
    (source / "simready_usd/payloads/materials.usda").write_text(pointer, encoding="utf-8")
    job = job_for(tmp_path, candidate, source)
    completed = run_worker(tmp_path, job)
    assert completed.returncode == 1
    result = load_result(job)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "lfs_pointer"
    assert result["error"]["retryable"] is False
    assert "materials.usda" in result["error"]["message"]
    assert not (tmp_path / "staging" / "package").exists()


def test_bom_mismatch_fails_and_leaves_no_staged_package(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    (source / "simready_usd/payloads/base.usda").write_text(
        (source / "simready_usd/payloads/base.usda").read_text(encoding="utf-8") + "\n# drift\n",
        encoding="utf-8",
    )
    job = job_for(tmp_path, candidate, source)
    completed = run_worker(tmp_path, job)
    assert completed.returncode == 1
    result = load_result(job)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "source_invalid"
    assert "BOM" in result["error"]["message"]
    assert not (tmp_path / "staging" / "package").exists()


def test_unsafe_bom_path_fails_as_source_invalid(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    bom_path = source / ".metadata/com.nvidia.simready.packaging.bom.json"
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    bom["items"][0]["relative_path"] = "../escaped.usda"
    bom_path.write_text(json.dumps(bom), encoding="utf-8")
    job = job_for(tmp_path, candidate, source)
    completed = run_worker(tmp_path, job)
    assert completed.returncode == 1
    result = load_result(job)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "source_invalid"
    assert "unsafe path" in result["error"]["message"]
    assert not (tmp_path / "staging" / "package").exists()


def test_source_budget_is_enforced_before_copying(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    job = job_for(
        tmp_path,
        candidate,
        source,
        limits={"sourcePackageBytes": 16, "adaptedPackageBytes": 64 * 1024 * 1024},
    )
    completed = run_worker(tmp_path, job)
    assert completed.returncode == 1
    result = load_result(job)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "budget_exceeded"
    assert not (tmp_path / "staging" / "package").exists()
    assert not (tmp_path / "cache" / candidate.package).exists()


def test_expected_fact_aliases_are_compared(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    job = job_for(tmp_path, candidate, source, expected={"sourceFiles": candidate.expected_files + 1})
    completed = run_worker(tmp_path, job)
    assert completed.returncode == 1
    result = load_result(job)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "source_invalid"
    assert "file count" in result["error"]["message"]
    assert not (tmp_path / "staging" / "package").exists()


def test_real_mode_rejects_unpinned_package_or_revision_without_network(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    job = job_for(tmp_path, candidate, source)
    job.pop("sourceRoot")
    job["packageId"] = "arbitrary_remote_package"
    job["revision"] = "0" * 40
    completed = run_worker(tmp_path, job)
    assert completed.returncode == 1
    result = load_result(job)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "source_invalid"
    assert "not pinned" in result["error"]["message"]
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "staging" / "package").exists()


def test_adapted_budget_is_enforced_and_leaves_no_staged_package(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    job = job_for(
        tmp_path,
        candidate,
        source,
        limits={"sourcePackageBytes": 64 * 1024 * 1024, "adaptedPackageBytes": 1},
    )
    completed = run_worker(tmp_path, job)
    assert completed.returncode == 1
    result = load_result(job)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "budget_exceeded"
    assert "Adapted package" in result["error"]["message"]
    assert not (tmp_path / "staging" / "package").exists()


def test_prune_preserves_the_cache_completion_marker(tmp_path):
    from factory.acquisition_worker import CACHE_MARKER, _prune_cache

    package = tmp_path / "cache" / "simready" / REVISION / "box_cardboard_a01"
    package.mkdir(parents=True)
    (package / "keep.usda").write_text("keep", encoding="utf-8")
    (package / "stale.usda").write_text("stale", encoding="utf-8")
    (package / CACHE_MARKER).write_text("{}", encoding="utf-8")
    _prune_cache(package, {"keep.usda"})
    assert (package / "keep.usda").is_file()
    assert not (package / "stale.usda").exists()
    assert (package / CACHE_MARKER).is_file()


def test_marked_cache_is_not_counted_as_source(tmp_path):
    from factory.simready import inspect_source

    candidate, source = build_source(tmp_path / "source")
    (source / ".acquisition-cache.json").write_text("{}", encoding="utf-8")
    report = inspect_source(candidate, source)
    assert report["status"] == "pass", report["problems"]
    assert report["files"] == candidate.expected_files
    assert report["bytes"] == candidate.expected_bytes
