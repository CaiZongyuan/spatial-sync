"""Real pytest executions must cover an independent collection exactly once."""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ci_shards_execute_the_complete_collection_and_reject_missing_junit_nodes(tmp_path):
    for directory in ("contract", "unit", "cli", "gateway"):
        path = tmp_path / "tests" / directory / "test_probe.py"
        path.parent.mkdir(parents=True)
        path.write_text(
            "import pytest\n@pytest.mark.parametrize('value', range(4))\n"
            "def test_probe(value):\n    assert value >= 0\n",
            encoding="utf-8",
        )
    base = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SPATIAL_CI_PYTEST_") and not key.startswith("PYTEST_XDIST_")
    }
    base["PYTHONPATH"] = str(ROOT)
    evidence = tmp_path / "evidence"

    def run(lane, index, count, paths, collect=False):
        output = evidence / f"{lane}-{index}-of-{count}"
        env = base | {"SPATIAL_CI_PYTEST_LANE": lane, "SPATIAL_CI_PYTEST_MANIFEST_DIR": str(output)}
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.ci_sharding",
            "--import-mode=importlib",
            *paths,
            "-q",
        ]
        if collect:
            argv.append("--collect-only")  # No shard env: the default must retain every node.
        else:
            env |= {
                "SPATIAL_CI_PYTEST_INDEX": str(index),
                "SPATIAL_CI_PYTEST_COUNT": str(count),
                "PYTEST_XDIST_AUTO_NUM_WORKERS": "2",
            }
            argv += ["-n", "auto", f"--junitxml={output / f'{lane}-{index}-of-{count}.xml'}"]
        result = subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr

    run("reference", 1, 1, ["tests/contract", "tests/unit", "tests/cli", "tests/gateway"], collect=True)
    run("core", 1, 1, ["tests/contract", "tests/unit", "tests/cli"])
    for index in (1, 2):
        run("gateway", index, 2, ["tests/gateway"])
    command = [sys.executable, str(ROOT / "tools/check_ci_python_coverage.py"), str(evidence)]
    result = subprocess.run(command, cwd=tmp_path, env=base, capture_output=True, text=True, timeout=10)
    (tmp_path / "validator-green.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"completeNodes": 16' in result.stdout

    junit = evidence / "gateway-1-of-2/gateway-1-of-2.xml"
    tree = ET.parse(junit)
    tree.write(tmp_path / "complete-gateway-junit.xml")
    suite = next(tree.iter("testsuite"))
    suite.remove(next(suite.iter("testcase")))
    tree.write(junit)
    result = subprocess.run(command, cwd=tmp_path, env=base, capture_output=True, text=True, timeout=10)
    (tmp_path / "validator-missing-node-red.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 1 and "JUnit differs from selection" in result.stderr

    invalid = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.ci_sharding", "tests/gateway", "--collect-only", "-q"],
        cwd=tmp_path,
        env=base | {"SPATIAL_CI_PYTEST_INDEX": "0", "SPATIAL_CI_PYTEST_COUNT": "2"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    (tmp_path / "invalid-shard-red.log").write_text(invalid.stdout + invalid.stderr, encoding="utf-8")
    assert invalid.returncode != 0 and "1 <= INDEX <= COUNT" in invalid.stderr
