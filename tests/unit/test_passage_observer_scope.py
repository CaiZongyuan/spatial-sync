"""The opt-in demo observer belongs to spawned physics, not the gateway."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def observer_environment(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((
        str(ROOT / "tools/passage_observer"), str(ROOT), str(ROOT / "apps/cli"),
    ))
    env["SPATIAL_PASSAGE_CONTACT_TRACE_DIR"] = str(tmp_path / "contacts")
    for key, leaf in (
        ("LW_LIBRARY", "library"), ("SPATIAL_STATE_ROOT", "state"),
        ("SPATIAL_RECORDINGS_ROOT", "recordings"), ("SPATIAL_SCENE_PREVIEWS_ROOT", "previews"),
        ("SPATIAL_CATALOG_METADATA_ROOT", "catalog-metadata"),
    ):
        directory = tmp_path / leaf
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    env["SPATIAL_CATALOG_ROOT"] = ""
    return env


def test_demo_observer_keeps_gateway_cold_import_engine_free(tmp_path):
    code = (
        "import sys; import apps.api.app.main; "
        "assert not set(('newton', 'warp', 'pxr')).intersection(sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=observer_environment(tmp_path),
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Error in sitecustomize" not in result.stderr
    assert not (tmp_path / "contacts").exists()


def test_demo_observer_installs_in_real_spawn_child(tmp_path):
    pytest.importorskip("warp")
    script = tmp_path / "spawn_probe.py"
    script.write_text(
        """import multiprocessing as mp
import sys
from pathlib import Path

def child(path):
    from packages.sim_runtime.physics_port import PhysicsPort
    Path(path).write_text(PhysicsPort.__init__.__module__, encoding="utf8")

if __name__ == "__main__":
    process = mp.get_context("spawn").Process(target=child, args=(sys.argv[1],))
    process.start()
    process.join(30)
    if process.is_alive():
        process.terminate()
        process.join()
        raise RuntimeError("Owned observer probe did not stop")
    assert process.exitcode == 0
""", encoding="utf8",
    )
    result_path = tmp_path / "result.txt"
    result = subprocess.run(
        [sys.executable, str(script), str(result_path)], cwd=ROOT,
        env=observer_environment(tmp_path), capture_output=True, text=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result_path.read_text(encoding="utf8") == "sitecustomize"
