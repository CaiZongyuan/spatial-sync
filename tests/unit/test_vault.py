"""Real local private files and the only credential-value handoff: a child environment."""

import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest

from packages.vault import Vault, VaultError

SENTINEL = "FAKE_VAULT_CHILD_ENV_SENTINEL_42"


def test_distinct_names_do_not_collide_on_case_insensitive_filesystems(tmp_path):
    vault = Vault(tmp_path)
    # These legal names encoded as base64url YWFF / YWFf, the same Windows filename.
    vault.set("aaE", "fake-first")
    vault.set("aa_", "fake-second")
    assert vault.names() == ["aaE", "aa_"]
    assert vault.adapter_environment({"FIRST": "aaE", "SECOND": "aa_"}) == {
        "FIRST": "fake-first", "SECOND": "fake-second",
    }


def test_private_credential_names_need_no_value_read_and_environment_stays_local(tmp_path):
    vault = Vault(tmp_path)
    vault.set("llm/fake", SENTINEL)
    assert vault.names() == ["llm/fake"]
    environment = vault.adapter_environment(
        {"FAKE_API_KEY": "llm/fake"},
        base={key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ},
    )
    assert "FAKE_API_KEY" not in os.environ
    trace = tmp_path / "fake-adapter-trace.jsonl"
    # A real isolated child verifies delivery; the fake adapter records only presence and a digest.
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os,json,hashlib,sys; "
                "v=os.environ['FAKE_API_KEY']; "
                "row={'credentialPresent':bool(v),'digest':hashlib.sha256(v.encode()).hexdigest()}; "
                "open(sys.argv[1],'w').write(json.dumps(row)); print(json.dumps(row))"
            ),
            str(trace),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 0
    assert json.loads(child.stdout) == {
        "credentialPresent": True,
        "digest": hashlib.sha256(SENTINEL.encode()).hexdigest(),
    }
    assert SENTINEL not in child.stdout + child.stderr + trace.read_text()
    path = next((tmp_path / "vault").glob("*.secret"))
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    # Invalid UTF-8 still lists: names do not decode or parse credential contents.
    path.write_bytes(b"\xff\xfe")
    assert vault.names() == ["llm/fake"]
    with pytest.raises(VaultError, match="Cannot resolve"):
        vault.adapter_environment({"FAKE_API_KEY": "llm/fake"})


def test_vault_refuses_real_insecure_permissions(tmp_path):
    vault = Vault(tmp_path)
    vault.set("fake", SENTINEL)
    path = next((tmp_path / "vault").glob("*.secret"))
    if os.name == "nt":
        # Everyone read is a real additional ACE, not a mocked chmod check.
        changed = subprocess.run(
            ["icacls", str(path), "/grant", "*S-1-1-0:(R)"], capture_output=True, timeout=10
        )
        assert changed.returncode == 0
    else:
        path.chmod(0o644)
    with pytest.raises(VaultError):
        vault.names()
    with pytest.raises(VaultError):
        vault.set("fake", "another-fake")
    with pytest.raises(VaultError):
        vault.adapter_environment({"FAKE_API_KEY": "fake"})


@pytest.mark.parametrize("name", ["../fake", "llm/../fake", "/absolute", "llm//fake", "fake value"])
def test_vault_refuses_path_like_credential_names(tmp_path, name):
    with pytest.raises(VaultError):
        Vault(tmp_path).set(name, SENTINEL)
    assert list(tmp_path.iterdir()) == []
