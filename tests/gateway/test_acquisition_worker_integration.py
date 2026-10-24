"""Worker staging -> internal import -> offline local asset.

The real worker subprocess is covered by ``tests/unit/test_acquisition_worker.py``;
the HTTP service, budgets and lifecycle by ``tests/gateway/test_acquisitions.py``.
This test wires the worker's staged package through the exact internal import used
by the service and proves the published asset survives raw-cache removal. The real
box/bucket service and scene launch evidence is separate from this GPU-free check.
"""

import json
import shutil
from pathlib import Path

from factory.acquisition_worker import execute
from factory.fixture import build_source
from factory.simready import REVISION, inspect_source
from packages.library.assets import AssetLibrary

PACKAGE = "fixture_synthetic_box"
LOCAL_ASSET = "environment/fixture-box"


def test_worker_stage_imports_and_survives_raw_cache_removal(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    source_facts = inspect_source(candidate, source)
    assert source_facts["status"] == "pass"

    library = AssetLibrary(
        tmp_path / "state", builtin_root=tmp_path / "builtin", user_root=tmp_path / "library"
    )
    library.startup()
    staging_root = tmp_path / "library" / ".acquisition-staging"
    cache_root = tmp_path / "state" / "acquisitions" / "cache" / "simready" / REVISION
    progress = tmp_path / "work" / "progress.json"
    result = tmp_path / "work" / "result.json"
    progress.parent.mkdir(parents=True)
    job = {
        "schema": "lw.acquisition.worker-job/1",
        "acquisitionId": "acq-" + "0" * 32,
        "provider": "simready",
        "packageId": PACKAGE,
        "revision": REVISION,
        "recipe": "simready-newton-usdpreview-v1",
        "cacheRoot": str(cache_root),
        "stagingDir": str(staging_root / "acq-fixture"),
        "progressFile": str(progress),
        "resultFile": str(result),
        "limits": {"sourcePackageBytes": 64 * 1024 * 1024,
                    "adaptedPackageBytes": 64 * 1024 * 1024},
        "expected": {
            "files": candidate.expected_files,
            "bytes": candidate.expected_bytes,
            "sourceFiles": candidate.expected_files,
            "sourceBytes": candidate.expected_bytes,
            "localAssetId": LOCAL_ASSET,
            "title": candidate.title,
            "description": candidate.description,
            "role": "dressing and static collision prop",
        },
        "sourceRoot": str(source),
    }
    completed = execute(job)
    assert completed["status"] == "succeeded", completed
    assert completed["source"]["files"] == candidate.expected_files
    staged = Path(completed["stagedPackage"])
    assert staged.is_relative_to(staging_root)
    for required in ("asset.yaml", "LICENSE.txt", "NOTICE", "provenance.json"):
        assert (staged / required).is_file(), required
    _ = json.loads(progress.read_text(encoding="utf-8"))

    imported = library.import_staged(staged, allowed_root=staging_root)
    assert imported["assetVersion"] == f"{LOCAL_ASSET}@1"
    detail = library.describe(f"{LOCAL_ASSET}@1")
    assert detail["contentHash"] == imported["contentHash"]
    assert detail["visualUrl"]
    visual = next(
        path for path in (tmp_path / "library/assets").rglob("*") if path.name == "visual.glb"
    )
    assert visual.is_file() and visual.read_bytes().startswith(b"glTF")

    # Removing the raw cache must not affect the immutable imported version.
    shutil.rmtree(cache_root.parents[1], ignore_errors=True)
    assert not any((tmp_path / "state/acquisitions/cache").rglob("*"))
    offline = library.describe(f"{LOCAL_ASSET}@1")
    assert offline["contentHash"] == imported["contentHash"]
    assert visual.is_file()
