"""Real Linux descendant cleanup; a dead wrapper alone cannot establish an idle resource."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from apps.node.process import OwnedProcess

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX subreaper semantics")
ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/owned_process_tree.py"


def ready(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), path


def read_first_visible_bytes(path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            pass
    raise AssertionError(f"Timed out waiting for {path}")


def stop_fixture(attempt, proc):
    (attempt / "stop").write_text("owned fixture cleanup", encoding="utf-8")
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def test_leaf_fixture_publishes_complete_json_on_first_visibility(tmp_path):
    env = os.environ | {"PYTHONPATH": str(ROOT)}
    for index in range(15):
        attempt = tmp_path / f"leaf-{index}"
        attempt.mkdir()
        proc = subprocess.Popen(
            [sys.executable, str(FIXTURE), "leaf", str(attempt)],
            cwd=ROOT,
            env=env,
        )
        try:
            payload = json.loads(read_first_visible_bytes(attempt / "leaf.json").decode("utf-8"))
            assert payload["pid"] == proc.pid
            assert isinstance(payload["createdAt"], float)
        finally:
            stop_fixture(attempt, proc)
        assert {item.name for item in attempt.iterdir()} == {"leaf.json", "stop"}


def test_owner_fixture_publishes_complete_json_on_first_visibility(tmp_path):
    env = os.environ | {"PYTHONPATH": str(ROOT)}
    for index in range(10):
        attempt = tmp_path / f"owner-{index}"
        attempt.mkdir()
        cleanup_proof = None
        proc = subprocess.Popen(
            [sys.executable, str(FIXTURE), "owner", str(attempt)],
            cwd=ROOT,
            env=env,
        )
        try:
            payload = json.loads(read_first_visible_bytes(attempt / "owner-ready.json").decode("utf-8"))
            cleanup_proof = Path(payload["cleanup"])
            assert cleanup_proof.parent == attempt
            assert cleanup_proof.name.startswith("clean-")
        finally:
            stop_fixture(attempt, proc)
            if cleanup_proof is not None:
                ready(cleanup_proof)
        entries = {item.name for item in attempt.iterdir()}
        receipts = {name for name in entries if name.startswith("process-") and name.endswith(".json")}
        assert len(receipts) == 1
        assert entries - receipts == {
            "owner-ready.json",
            "leaf.json",
            "out.log",
            "err.log",
            "stop",
            cleanup_proof.name,
        }


@pytest.mark.parametrize("lose_owner", [False, True])
def test_resistant_grandchild_is_gone_before_cleanup_proof_even_after_owner_loss(tmp_path, lose_owner):
    env = os.environ | {"PYTHONPATH": str(ROOT)}
    owner = None
    try:
        with (tmp_path / "outer.log").open("wb") as log:
            if lose_owner:
                owner = subprocess.Popen(
                    [sys.executable, str(FIXTURE), "owner", str(tmp_path)],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                ready(tmp_path / "owner-ready.json")
                proof = Path(json.loads((tmp_path / "owner-ready.json").read_text())["cleanup"])
            else:
                owner = OwnedProcess(
                    [sys.executable, str(FIXTURE), "trainer", str(tmp_path)],
                    cwd=tmp_path,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                proof = owner.cleanup
            ready(tmp_path / "leaf.json")
            leaf = json.loads((tmp_path / "leaf.json").read_text())
            assert psutil.Process(leaf["pid"]).is_running()
            if lose_owner:
                owner.kill()
                owner.wait(timeout=5)
            else:
                assert owner.process.wait(timeout=5) == 0
                owner.close()
            ready(proof)
            try:
                process = psutil.Process(leaf["pid"])
                assert process.create_time() != leaf["createdAt"] or not process.is_running()
            except psutil.NoSuchProcess:
                pass
    finally:
        (tmp_path / "stop").write_text("owned fixture cleanup")
        if owner is not None:
            if isinstance(owner, OwnedProcess):
                owner.close()
            elif owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=5)
