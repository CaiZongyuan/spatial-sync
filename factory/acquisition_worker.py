"""Subprocess worker for one SF2 acquisition job.

The gateway never imports this module. It runs as a tool process::

    python -m factory.acquisition_worker --job <job.json>

The job is server-created. It never contains a user URL; the only source is the
pinned NVIDIA SimReady revision, or a server-configured ``sourceRoot`` fixture
directory for GPU-free tests. Job shape (``lw.acquisition.worker-job/1``)::

    {
      "schema": "lw.acquisition.worker-job/1",
      "acquisitionId": "acq-...",
      "provider": "simready",
      "packageId": "box_cardboard_a01",
      "revision": "524747d9...",
      "recipe": "simready-newton-usdpreview-v1",
      "cacheRoot": "<absolute>/cache/simready/<revision>",
      "stagingDir": "<absolute>/<acquisition id>",
      "progressFile": "<absolute>/progress.json",
      "resultFile": "<absolute>/result.json",
      "limits": {"sourcePackageBytes": 536870912, "adaptedPackageBytes": 536870912},
      "expected": {
        "files": 36, "bytes": 21530693, "license": "CC-BY-4.0",
        "licenseSha256": "...", "localAssetId": "environment/cardboard-box",
        "title": "Cardboard box", "description": "...",
        "role": "dressing and static collision prop"
      },
      "sourceRoot": "<absolute fixture directory>"  # optional fixture mode
    }

Expected facts may also arrive as ``expectedFiles``/``expectedBytes``/
``expectedLicense``, or as ``sourceFiles``/``sourceBytes``/``license`` inside
``expected`` (the runner's shape). Missing facts fall back to the pinned support
record for real packages and to the copied fixture for fixture jobs. The package
is staged at ``<stagingDir>/package``; ``stagedPackage`` and ``packageDir`` in
the result point at it.

Progress is written atomically as
``{"phase", "completedBytes", "completedFiles", "totalBytes", "totalFiles",
"message", "updatedAt"}``. The result file (``lw.acquisition.worker/1``) carries
``status`` (``succeeded``/``failed``), the source inspection and adaptation
reports, measured usage, retained notices and an error with one of the design
codes: ``source_invalid``, ``adaptation_gap``, ``budget_exceeded``,
``source_unavailable``, ``lfs_pointer``, ``interrupted`` or ``internal_error``.
Failures exit non-zero and leave no staged package; the raw cache is kept for
reuse.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import simready  # noqa: E402
from factory.simready import (  # noqa: E402
    AdaptationError,
    PinnedSource,
    SourceError,
    adapt,
    download_package,
    fetch_tree,
    inspect_source,
    lfs_pointer_files,
)
from packages.acquisition.cache import CACHE_MARKER, mark_complete  # noqa: E402

WORKER_SCHEMA = "lw.acquisition.worker/1"
JOB_SCHEMA = "lw.acquisition.worker-job/1"
PROGRESS_INTERVAL_SECONDS = 0.5
DEFAULT_SOURCE_BYTES = 512 * 1024 * 1024
DEFAULT_ADAPTED_BYTES = 512 * 1024 * 1024
SUPPORTED_PROVIDERS = {"simready"}
FIXTURE_PROVIDERS = {"simready", "fixture"}
RETRYABLE = {
    "adaptation_gap": False,
    "budget_exceeded": False,
    "import_invalid": False,
    "internal_error": True,
    "interrupted": True,
    "lfs_pointer": False,
    "source_invalid": False,
    "source_unavailable": True,
}


class WorkerError(Exception):
    """A job failure with one of the frozen acquisition error codes."""

    def __init__(self, code, message, *, retryable=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = RETRYABLE[code] if retryable is None else retryable


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


class Progress:
    """Throttled, atomic progress writer; progress failures never fail the job."""

    def __init__(self, path, acquisition_id):
        self.path = Path(path) if path else None
        self.acquisition_id = acquisition_id
        self.phase = None
        self.last_write = 0.0

    def write(
        self,
        phase,
        completed_bytes,
        completed_files,
        total_bytes,
        total_files,
        *,
        message=None,
        force=False,
    ):
        if self.path is None:
            return
        changed = phase != self.phase
        now = time.monotonic()
        if not force and not changed and now - self.last_write < PROGRESS_INTERVAL_SECONDS:
            return
        try:
            _atomic_json(
                self.path,
                {
                    "acquisitionId": self.acquisition_id,
                    "phase": phase,
                    "completedBytes": int(completed_bytes),
                    "completedFiles": int(completed_files),
                    "totalBytes": int(total_bytes),
                    "totalFiles": int(total_files),
                    "message": message,
                    "updatedAt": _now(),
                },
            )
        except OSError:
            return
        self.phase = phase
        self.last_write = now


def _required(job, name):
    value = job.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise WorkerError("internal_error", f"Acquisition job is missing {name}")
    return value


def _absolute(job, name):
    path = Path(str(_required(job, name)))
    if not path.is_absolute():
        raise WorkerError("internal_error", f"Acquisition job {name} must be an absolute path")
    return path


def _expected(job, key, *aliases):
    names = (key, *aliases)
    for container in (job.get("expected"), job.get("expectedSource")):
        if isinstance(container, dict):
            for name in names:
                if name in container:
                    return container[name]
    for name in names:
        if name in job:
            return job[name]
    return None


def _optional_int(value, name):
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkerError("internal_error", f"Expected {name} must be an integer") from exc
    if number < 0:
        raise WorkerError("internal_error", f"Expected {name} must not be negative")
    return number


def _fixture_files(source_root):
    """List regular fixture files, rejecting symlinks and special entries."""
    files = []
    for directory, dirnames, filenames in os.walk(source_root, followlinks=False):
        base = Path(directory)
        for name in list(dirnames) + list(filenames):
            path = base / name
            if path.is_symlink():
                relative = path.relative_to(source_root).as_posix()
                raise WorkerError("source_invalid", f"Fixture source contains a symlink: {relative}")
        for name in filenames:
            path = base / name
            if path.is_file():
                files.append(path)
    return sorted(files)


def _tree_totals(root):
    files = [
        path
        for path in Path(root).rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.relative_to(root).as_posix() != CACHE_MARKER
    ]
    return len(files), sum(path.stat().st_size for path in files)


def _remove_tree(path):
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def _prune_cache(package_dir, keep):
    """Drop cache entries the pinned tree no longer declares (and .part leftovers)."""
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        return
    for path in sorted(package_dir.rglob("*"), reverse=True):
        relative = path.relative_to(package_dir).as_posix()
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.is_file() and relative not in keep and relative != CACHE_MARKER:
            path.unlink(missing_ok=True)
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _copy_fixture(source_root, destination, *, limit, progress):
    source_root = Path(source_root)
    if not source_root.is_dir():
        raise WorkerError("source_unavailable", f"Fixture source is missing: {source_root}")
    files = _fixture_files(source_root)
    if not files:
        raise WorkerError("source_invalid", "Fixture source contains no files")
    total_bytes = sum(path.stat().st_size for path in files)
    total_files = len(files)
    if total_bytes > limit:
        raise WorkerError(
            "budget_exceeded",
            f"Fixture source is {total_bytes} bytes, above the {limit} byte source limit",
        )
    _remove_tree(destination)
    destination.mkdir(parents=True)
    progress.write(
        "downloading",
        0,
        0,
        total_bytes,
        total_files,
        message="Copying fixture source",
        force=True,
    )
    completed_bytes = 0
    for completed_files, path in enumerate(files, start=1):
        relative = path.relative_to(source_root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        completed_bytes += target.stat().st_size
        progress.write("downloading", completed_bytes, completed_files, total_bytes, total_files)
    progress.write(
        "downloading", total_bytes, total_files, total_bytes, total_files,
        message="Fixture source copied", force=True,
    )
    return {"transferBytes": 0, "cacheReusedBytes": 0}


def _download(package, cache_root, *, limit, progress):
    """Download the pinned tree with bounded, progress-reporting transfer."""
    try:
        entries = [
            entry
            for entry in fetch_tree(package)
            if entry["type"] == "file" and not entry["path"].endswith("/")
        ]
    except SourceError as exc:
        raise WorkerError("source_unavailable", str(exc)) from exc
    if not entries:
        raise WorkerError("source_invalid", f"Pinned source tree has no files for {package}")
    total_bytes = sum(int(entry.get("size") or 0) for entry in entries)
    total_files = len(entries)
    if total_bytes > limit:
        raise WorkerError(
            "budget_exceeded",
            f"Pinned source declares {total_bytes} bytes, above the {limit} byte source limit",
        )
    package_dir = Path(cache_root) / package
    _prune_cache(package_dir, {entry["path"].removeprefix(package + "/") for entry in entries})
    reused = 0
    for entry in entries:
        destination = package_dir / entry["path"].removeprefix(package + "/")
        if destination.is_file() and destination.stat().st_size == entry.get("size", -1):
            reused += destination.stat().st_size
    state = {"bytes": 0, "files": 0}

    def report(completed_bytes, completed_files, declared_bytes, declared_files):
        state["bytes"] = completed_bytes
        state["files"] = completed_files
        progress.write("downloading", completed_bytes, completed_files, declared_bytes, declared_files)

    try:
        download_package(package, cache_root, progress=report, max_bytes=limit)
    except SourceError as exc:
        raise WorkerError("source_unavailable", str(exc)) from exc
    progress.write(
        "downloading", state["bytes"], state["files"], total_bytes, total_files,
        message="Source downloaded" if state["bytes"] < total_bytes else "Pinned source verified",
        force=True,
    )
    return {
        "transferBytes": max(0, total_bytes - reused),
        "cacheReusedBytes": reused,
    }


def _candidate(job, package, expected_files, expected_bytes, cache_dir):
    pinned = simready.SOURCE_PACKAGE_BY_NAME.get(package)
    if expected_files is None or expected_bytes is None:
        actual_files, actual_bytes = _tree_totals(cache_dir)
        expected_files = actual_files if expected_files is None else expected_files
        expected_bytes = actual_bytes if expected_bytes is None else expected_bytes
    asset_id = _expected(job, "localAssetId") or (
        pinned.asset_id if pinned else f"environment/{package.replace('_', '-')}"
    )
    title = _expected(job, "title") or (pinned.title if pinned else package.replace("_", " ").title())
    description = _expected(job, "description") or (
        pinned.description if pinned else f"SimReady source package {package}."
    )
    role = _expected(job, "role") or (
        pinned.role if pinned else "dressing and static collision prop"
    )
    return PinnedSource(
        package=package,
        asset_id=str(asset_id),
        title=str(title),
        description=str(description),
        expected_files=int(expected_files),
        expected_bytes=int(expected_bytes),
        role=str(role),
    )


def _expected_license(value):
    """Accept either a plain license name or the catalog license document."""
    if isinstance(value, dict):
        evidence = value.get("evidence") if isinstance(value.get("evidence"), dict) else {}
        return value.get("name"), evidence.get("sha256") or value.get("sha256")
    return value, None


def _inspect(candidate, cache_dir, *, expected_license, expected_license_sha, source_limit):
    expected_license, document_sha = _expected_license(expected_license)
    expected_license_sha = expected_license_sha or document_sha
    pointers = lfs_pointer_files(cache_dir)
    if pointers:
        raise WorkerError(
            "lfs_pointer",
            "Source package still contains Git LFS pointer content: " + ", ".join(pointers[:5]),
        )
    try:
        report = inspect_source(candidate, cache_dir)
    except SourceError as exc:
        raise WorkerError("source_invalid", str(exc)) from exc
    if report["status"] != "pass":
        raise WorkerError("source_invalid", "; ".join(report["problems"]))
    if expected_license is not None and report["license"]["name"] != expected_license:
        raise WorkerError(
            "source_invalid",
            f"Source license {report['license']['name']!r} differs from catalog {expected_license!r}",
        )
    if expected_license_sha is not None and report["license"]["sha256"] != expected_license_sha:
        raise WorkerError("source_invalid", "Source license text differs from the catalog digest")
    if report["bytes"] > source_limit:
        raise WorkerError(
            "budget_exceeded",
            f"Source package is {report['bytes']} bytes, above the {source_limit} byte limit",
        )
    return report


def _adapt(candidate, cache_dir, package_dir, *, adapted_limit, progress, source_report):
    progress.write(
        "preparing", 0, 0, source_report["bytes"], source_report["files"],
        message="Adapting the pinned source package", force=True,
    )
    _remove_tree(package_dir)
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = adapt(candidate, cache_dir, package_dir)
    except AdaptationError as exc:
        raise WorkerError("adaptation_gap", str(exc)) from exc
    except SourceError as exc:
        raise WorkerError("source_invalid", str(exc)) from exc
    if report.get("status") != "pass":
        code = "source_invalid" if report.get("status") == "fail" else "adaptation_gap"
        message = "; ".join(report.get("gaps") or ["Adaptation did not produce a usable package"])
        raise WorkerError(code, message)
    output = report.get("output") or {}
    if not output.get("bytes") or not output.get("files"):
        raise WorkerError("adaptation_gap", "Adaptation produced no measured package")
    if output["bytes"] > adapted_limit:
        raise WorkerError(
            "budget_exceeded",
            f"Adapted package is {output['bytes']} bytes, above the {adapted_limit} byte limit",
        )
    progress.write(
        "validating", output["bytes"], output["files"], output["bytes"], output["files"],
        message="Validating the staged package", force=True,
    )
    for required in ("asset.yaml", "LICENSE.txt", "provenance.json"):
        if not (package_dir / required).is_file():
            raise WorkerError("adaptation_gap", f"Staged package is missing {required}")
    notice = report.get("sourceNotice")
    if source_report.get("notices"):
        if not notice or not (package_dir / "SOURCE-NOTICE").is_file():
            raise WorkerError("adaptation_gap", "Source NOTICE was not retained as SOURCE-NOTICE")
        if simready.sha256_file(package_dir / "SOURCE-NOTICE") != notice["sha256"]:
            raise WorkerError("adaptation_gap", "Retained SOURCE-NOTICE does not match the source notice")
    progress.write(
        "validating", output["bytes"], output["files"], output["bytes"], output["files"],
        message="Staged package is ready", force=True,
    )
    return report


def _usage(source_report, adaptation_report, transfer, *, fixture):
    output = adaptation_report.get("output") or {}
    return {
        "sourceBytes": source_report["bytes"],
        "sourceFiles": source_report["files"],
        "transferBytes": 0 if fixture else transfer["transferBytes"],
        "cacheReusedBytes": 0 if fixture else transfer["cacheReusedBytes"],
        "adaptedBytes": output.get("bytes"),
        "adaptedFiles": output.get("files"),
        "temporaryBytes": source_report["bytes"] + (output.get("bytes") or 0),
    }


def execute(job):
    """Run one job and return its result document (also written to resultFile)."""
    started_at = _now()
    acquisition_id = str(job.get("acquisitionId") or "")
    result_file = job.get("resultFile")
    progress = Progress(job.get("progressFile") or None, acquisition_id)
    package_dir = None
    usage = None
    mode = "fixture" if job.get("sourceRoot") else "download"
    provider = job.get("provider")
    package = job.get("packageId")
    try:
        cache_root = _absolute(job, "cacheRoot")
        staging_dir = _absolute(job, "stagingDir")
        if not result_file:
            raise WorkerError("internal_error", "Acquisition job is missing resultFile")
        progress.path = _absolute(job, "progressFile")
        acquisition_id = str(_required(job, "acquisitionId"))
        provider = str(_required(job, "provider"))
        package = str(_required(job, "packageId"))
        revision = str(_required(job, "revision"))
        recipe = str(_required(job, "recipe"))
        limits = job.get("limits") or {}
        source_limit = int(limits.get("sourcePackageBytes", DEFAULT_SOURCE_BYTES))
        adapted_limit = int(limits.get("adaptedPackageBytes", DEFAULT_ADAPTED_BYTES))
        progress.acquisition_id = acquisition_id
        progress.write("downloading", 0, 0, 0, 0, message="Starting acquisition", force=True)
        if source_limit <= 0 or adapted_limit <= 0:
            raise WorkerError("internal_error", "Acquisition limits must be positive")
        supported = FIXTURE_PROVIDERS if mode == "fixture" else SUPPORTED_PROVIDERS
        if provider not in supported:
            raise WorkerError("source_invalid", f"Unsupported acquisition provider: {provider!r}")
        if recipe != simready.RECIPE:
            raise WorkerError("adaptation_gap", f"Unsupported adaptation recipe: {recipe!r}")
        if mode == "download":
            if revision != simready.REVISION:
                raise WorkerError("source_invalid", f"Source revision is not pinned: {revision}")
            if package not in simready.SOURCE_PACKAGE_BY_NAME:
                raise WorkerError(
                    "source_invalid", f"Package {package!r} is not in the pinned support boundary"
                )
        cache_root.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        package_dir = staging_dir / "package"
        _remove_tree(package_dir)
        cache_dir = cache_root / package
        if mode == "fixture":
            transfer = _copy_fixture(
                _absolute(job, "sourceRoot"), cache_dir, limit=source_limit, progress=progress
            )
        else:
            transfer = _download(package, cache_root, limit=source_limit, progress=progress)
        expected_files = _optional_int(_expected(job, "files", "expectedFiles", "sourceFiles"), "files")
        expected_bytes = _optional_int(_expected(job, "bytes", "expectedBytes", "sourceBytes"), "bytes")
        expected_license = _expected(job, "license", "expectedLicense")
        expected_license_sha = _expected(job, "licenseSha256", "expectedLicenseSha256")
        candidate = _candidate(
            job, package, expected_files, expected_bytes, cache_dir
        )
        source_report = _inspect(
            candidate,
            cache_dir,
            expected_license=expected_license,
            expected_license_sha=expected_license_sha,
            source_limit=source_limit,
        )
        adaptation_report = _adapt(
            candidate,
            cache_dir,
            package_dir,
            adapted_limit=adapted_limit,
            progress=progress,
            source_report=source_report,
        )
        try:
            # Mark only after both inspections: the gateway-owned marker must not
            # be counted as source content. The service marks again on success.
            mark_complete(cache_root.parents[1], provider, revision, package)
        except Exception:
            pass
        usage = _usage(source_report, adaptation_report, transfer, fixture=mode == "fixture")
        return {
            "schema": WORKER_SCHEMA,
            "acquisitionId": acquisition_id,
            "status": "succeeded",
            "mode": mode,
            "provider": provider,
            "packageId": package,
            "revision": revision,
            "recipe": recipe,
            "packageDir": str(package_dir.resolve()),
            "stagedPackage": str(package_dir.resolve()),
            "stagingDir": str(staging_dir.resolve()),
            "source": source_report,
            "adaptation": adaptation_report,
            "notices": {
                "license": "LICENSE.txt",
                "sourceNotice": adaptation_report.get("sourceNotice"),
                "generated": "NOTICE",
            },
            "usage": usage,
            "error": None,
            "startedAt": started_at,
            "finishedAt": _now(),
        }
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, WorkerError):
            error = exc
        elif isinstance(exc, KeyboardInterrupt):
            error = WorkerError("interrupted", "Acquisition worker was interrupted")
        else:
            error = WorkerError("internal_error", f"{type(exc).__name__}: {exc}")
        return _failed(
            result_file,
            acquisition_id,
            started_at,
            mode,
            provider,
            package,
            package_dir,
            usage,
            error,
        )


def _failed(
    result_file,
    acquisition_id,
    started_at,
    mode,
    provider,
    package,
    package_dir,
    usage,
    error,
):
    if package_dir is not None:
        _remove_tree(package_dir)
    result = {
        "schema": WORKER_SCHEMA,
        "acquisitionId": acquisition_id,
        "status": "failed",
        "mode": mode,
        "provider": provider,
        "packageId": package,
        "error": {"code": error.code, "message": error.message, "retryable": error.retryable},
        "usage": usage,
        "startedAt": started_at,
        "finishedAt": _now(),
    }
    if result_file:
        _atomic_json(result_file, result)
    return result


def run(job):
    """Execute the job and persist its result; return the process exit code."""
    result = execute(job)
    result_file = job.get("resultFile")
    if result_file and not Path(result_file).is_file():
        _atomic_json(result_file, result)
    if result["status"] == "succeeded":
        return 0
    if not result.get("error"):
        result["error"] = {"code": "internal_error", "message": "Acquisition failed", "retryable": True}
    print(
        json.dumps({"status": "failed", "error": result["error"]}, ensure_ascii=False),
        file=sys.stderr,
    )
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run one Scene Factory acquisition job.")
    parser.add_argument("--job", required=True, help="Path to the server-created job JSON")
    args = parser.parse_args(argv)
    try:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": {"code": "internal_error", "message": str(exc)}}))
        return 2
    if not isinstance(job, dict):
        print(
            json.dumps(
                {"status": "failed", "error": {"code": "internal_error", "message": "Job must be an object"}}
            )
        )
        return 2
    return run(job)


if __name__ == "__main__":
    raise SystemExit(main())
