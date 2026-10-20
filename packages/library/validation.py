"""Own bounded validation workers and append reports without changing version bytes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from packages.library.archive import destination_parent, import_lock, package_files
from packages.library.common import REPO_ROOT, TYPE_ROOT, LibraryError, content_hash, document
from packages.library.reports import merge_physics, parse_report, pinned_assets


class StructuralValidator:
    def __init__(self, state_root, programs=None, *, timeout=20, physics_timeout=180):
        self.state_root = Path(state_root).resolve()
        self.programs = programs
        self.timeout = timeout
        self.physics_timeout = physics_timeout

    @staticmethod
    def scope(path, *, identifier="package", metadata=None, digest=None):
        return {
            "id": identifier,
            "path": str(path),
            "metadata": metadata,
            "contentHash": digest,
            "files": {name: str(path / name) for name in package_files(path)},
        }

    def run(
        self, path, kind, subject, metadata, assets, bindings=None, locations=None, *,
        expected_assets=(), include_physics=True
    ):
        payload = {
            "kind": kind,
            "subject": subject,
            "metadata": metadata,
            "bindings": bindings or {},
            "locations": locations or {},
            "package": self.scope(path),
            "assets": [
                self.scope(
                    version.path,
                    identifier=version.id,
                    metadata=version.metadata,
                    digest=version.content_hash,
                )
                for version in assets.versions.values()
            ],
            "types": {
                value["type"]: value for file in sorted(TYPE_ROOT.glob("*.yaml")) if (value := document(file))
            },
            "programs": self.programs() if self.programs else {},
            "expectedAssets": list(expected_assets),
        }
        try:
            structure = parse_report(self.worker("structural", payload, self.timeout), subject)
            if not include_physics:
                return structure
            payload |= {"structure": structure, "kernelCache": str(self.state_root / "physics-kernel-cache")}
            return merge_physics(structure, self.worker("physics", payload, self.physics_timeout))
        except (ValueError, KeyError, TypeError) as exc:
            raise LibraryError(
                "validation_unavailable", "Validation worker returned invalid data", 503
            ) from exc

    def run_structure(
        self, path, kind, subject, metadata, assets, bindings=None, locations=None, *, expected_assets=()
    ):
        """Dependency/reference inspection must never start a physics worker."""
        return self.run(path, kind, subject, metadata, assets, bindings, locations,
                        expected_assets=expected_assets, include_physics=False)

    def worker(self, module, payload, timeout):
        self.state_root.mkdir(parents=True, exist_ok=True)
        prefix = ".structure-" if module == "structural" else ".physics-"
        with tempfile.TemporaryDirectory(prefix=prefix, dir=self.state_root) as temporary:
            directory = Path(temporary)
            source, result = directory / "input.json", directory / "output.json"
            source.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
            command = [sys.executable, "-m", f"packages.library.{module}", str(source), str(result)]
            environment = os.environ | {
                "CUDA_VISIBLE_DEVICES": "",
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "PXR_WORK_THREAD_LIMIT": "1",
                "MUJOCO_GL": "disable",
            }
            try:
                process = subprocess.Popen(
                    command, cwd=REPO_ROOT, env=environment,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except OSError as exc:
                raise LibraryError(
                    "validation_unavailable", "Validation worker could not start", 503
                ) from exc
            try:
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    raise LibraryError(
                        "validation_unavailable", f"{module.capitalize()} validation timed out", 503
                    ) from None
            finally:
                if process.poll() is None:
                    try:
                        process.kill()  # Only this current creating handle is eligible for cleanup.
                        process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        raise LibraryError(
                            "validation_unavailable", "Validation worker cleanup could not be verified", 503
                        ) from None
            if process.returncode != 0 or not result.is_file():
                raise LibraryError(
                    "validation_unavailable", f"{module.capitalize()} validation worker failed", 503
                )
            try:
                return json.loads(result.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise LibraryError(
                    "validation_unavailable", "Validation worker returned invalid data", 503
                ) from exc

    def revalidate(self, version, kind, assets):
        if content_hash(version.path) != version.content_hash:
            raise LibraryError(
                "asset_changed" if kind == "asset" else "scene_not_launchable",
                "Immutable package changed after indexing",
                409,
            )
        try:
            expected_assets = pinned_assets(version, kind)
        except (ValueError, OSError) as exc:
            raise LibraryError(
                "asset_changed" if kind == "asset" else "scene_not_launchable", str(exc), 409
            ) from exc
        report = self.run(
            version.path,
            kind,
            f"{kind}:{version.id}",
            version.metadata,
            assets,
            getattr(version, "bindings", None),
            getattr(version, "locations", None),
            expected_assets=expected_assets,
        )
        return self.commit(version, kind, assets, report, expected_assets=expected_assets)

    def commit(self, version, kind, assets, report, *, expected_assets=()):
        """Shared final publication boundary for structural and later physics workers.

        Already durable pins survive failed revalidation of missing/changed dependencies.
        A newly learned byte identity must still exist when its report is published.
        """
        with assets._lock, import_lock(assets.roots["user"]):
            code = "asset_changed" if kind == "asset" else "scene_not_launchable"
            try:
                root = assets.roots[version.root]
                destination_parent(root, version.path.relative_to(root).parts)
                package_files(version.path)
                if not version.path.is_dir() or content_hash(version.path) != version.content_hash:
                    raise ValueError("Immutable package changed or disappeared during validation")
                # Scan the actual library again: deletion may have committed while the worker ran.
                assets.startup()
                previous = {item["id"]: item["contentHash"] for item in expected_assets}
                current = {item["id"]: item["contentHash"] for item in pinned_assets(version, kind)}
                incoming = {item["id"]: item["contentHash"] for item in report.get("referencedAssets", [])}
                if any(incoming.get(name) != digest for name, digest in current.items()):
                    raise ValueError("Validation would forget or contradict a durable dependency pin")
                for name, digest in incoming.items():
                    if previous.get(name) == digest:
                        continue
                    dependency = assets.resolve(name)
                    dependency_root = assets.roots[dependency.root]
                    destination_parent(dependency_root, dependency.path.relative_to(dependency_root).parts)
                    package_files(dependency.path)
                    if dependency.content_hash != digest or content_hash(dependency.path) != digest:
                        raise ValueError("Newly recorded dependency changed during validation: " + name)
            except (OSError, ValueError, LibraryError) as exc:
                raise LibraryError(code, str(exc), 409) from exc
            return version.reports.save(report, version.content_hash)
