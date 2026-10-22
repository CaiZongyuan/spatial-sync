"""Local private credential files. Listing never reads credential contents."""

import base64
import os
import re
import stat
import tempfile
from pathlib import Path


class VaultError(ValueError):
    """Safe diagnostics: never carry a credential value or underlying exception text."""


def state_root():
    return Path(os.getenv("SPATIAL_STATE_ROOT", str(Path.home() / ".spatial-sync" / "state")))


def valid_name(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", name) or any(
        part in ("", ".", "..") for part in name.split("/")
    ):
        raise VaultError("Credential names use letters, numbers, dots, dashes and slash-separated names")
    return name


def _private(path, *, directory=False, created=False):
    entry = path.lstat()
    if (
        path.is_symlink()
        or getattr(entry, "st_file_attributes", 0) & 0x400
        or not (stat.S_ISDIR(entry.st_mode) if directory else stat.S_ISREG(entry.st_mode))
    ):
        raise VaultError("Vault paths must be regular local files and directories")
    if os.name == "nt":
        from .windows import protect, verify

        (protect if created else verify)(path)
    elif entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != (0o700 if directory else 0o600):
        raise VaultError("Vault requires owner-only permissions (directory 0700, file 0600)")


class Vault:
    def __init__(self, root=None):
        self.directory = (Path(root) if root is not None else state_root()) / "vault"

    def _directory(self, *, create=False):
        if not self.directory.exists() and not self.directory.is_symlink():
            if not create:
                return False
            self.directory.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
            else:
                _private(self.directory, directory=True, created=True)
        _private(self.directory, directory=True)
        return True

    def _path(self, name):
        # One filename alphabet is safe on case-insensitive Windows filesystems too.
        encoded = base64.b32encode(valid_name(name).encode()).decode().lower().rstrip("=")
        return self.directory / (encoded + ".secret")

    def names(self):
        try:
            if not self._directory():
                return []
            names = []
            for path in self.directory.glob("*.secret"):
                _private(path)
                encoded = path.stem
                name = base64.b32decode(encoded + "=" * (-len(encoded) % 8), casefold=True)
                name = valid_name(name.decode("ascii"))
                if self._path(name) != path:
                    raise VaultError("Vault contains an invalid credential filename")
                names.append(name)
            return sorted(names)
        except (OSError, ValueError, UnicodeError):
            raise VaultError("Vault is unavailable or its private permissions are invalid") from None

    def set(self, name, value):
        valid_name(name)
        if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > 65536:
            raise VaultError("A credential must be nonempty text of at most 64 KiB without NUL")
        temporary = None
        try:
            self._directory(create=True)
            path = self._path(name)
            if path.exists() or path.is_symlink():
                _private(path)
            descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.directory)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                _private(Path(temporary), created=True)
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            temporary = None
            _private(path)
        except OSError:
            raise VaultError("Cannot write a private Vault credential") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def adapter_environment(self, references, *, base=None):
        """Resolve explicit env-var -> Vault-name bindings for a child process only.

        No global environment mutation, serialization, public read/export API or runner.
        The future adapter must pass this result directly as subprocess env and keep it
        out of arguments, trace, logs and model messages.
        """
        result = dict(base or {})
        try:
            self._directory()
            for variable, name in references.items():
                if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", variable):
                    raise VaultError("Adapter credential bindings require environment variable names")
                path = self._path(name)
                _private(path)
                result[variable] = path.read_text(encoding="utf-8")
            return result
        except (OSError, UnicodeError):
            raise VaultError("Cannot resolve a private adapter credential") from None
