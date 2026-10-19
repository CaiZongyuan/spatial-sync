"""Own a bounded structural worker and append reports without changing version bytes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from packages.library.archive import package_files
from packages.library.common import REPO_ROOT, TYPE_ROOT, LibraryError, content_hash, document
from packages.library.reports import parse_report


class StructuralValidator:
    def __init__(self, state_root, programs=None, *, timeout=20):
        self.state_root = Path(state_root)
        self.programs = programs
        self.timeout = timeout

    @staticmethod
    def scope(path, *, identifier="package", metadata=None, digest=None):
        return {
            "id": identifier,
            "path": str(path),
            "metadata": metadata,
            "contentHash": digest,
            "files": {name: str(path / name) for name in package_files(path)},
        }

    def run(self, path, kind, subject, metadata, assets, bindings=None, locations=None):
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
        }
        self.state_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".structure-", dir=self.state_root) as temporary:
            directory = Path(temporary)
            source, result = directory / "input.json", directory / "output.json"
            source.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
            command = [sys.executable, "-m", "packages.library.structural", str(source), str(result)]
            environment = os.environ | {
                "CUDA_VISIBLE_DEVICES": "",
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
            }
            with subprocess.Popen(
                command, cwd=REPO_ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ) as process:
                try:
                    process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    process.kill()  # The current Popen owns this CPU-only child; no historical PID lookup.
                    process.wait()
                    raise LibraryError(
                        "validation_unavailable", "Structural validation timed out", 503
                    ) from None
            if process.returncode != 0 or not result.is_file():
                raise LibraryError("validation_unavailable", "Structural validation worker failed", 503)
            return parse_report(json.loads(result.read_text(encoding="utf-8")), subject)

    def revalidate(self, version, kind, assets):
        if content_hash(version.path) != version.content_hash:
            raise LibraryError(
                "asset_changed" if kind == "asset" else "scene_not_launchable",
                "Immutable package changed after indexing",
                409,
            )
        report = self.run(
            version.path,
            kind,
            f"{kind}:{version.id}",
            version.metadata,
            assets,
            getattr(version, "bindings", None),
            getattr(version, "locations", None),
        )
        if content_hash(version.path) != version.content_hash:
            raise LibraryError(
                "asset_changed" if kind == "asset" else "scene_not_launchable",
                "Immutable package changed during validation",
                409,
            )
        return version.reports.save(report, version.content_hash)
