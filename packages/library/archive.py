"""Confined portable tar packages and cross-process immutable revision allocation."""

import os
import re
import tarfile
import unicodedata
from contextlib import contextmanager
from pathlib import PurePosixPath

from packages.library.common import LibraryError, content_hash

MAX_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_FILES = 4096
RESERVED = re.compile(r"(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", re.IGNORECASE)


def indexed_file(roots, files, root, relative, *, changed):
    match = files.get((root, relative))
    if match is None:
        raise LibraryError("unknown_file", "Unknown library file")
    version, filename = match
    try:
        path = roots[root]
        for part in version.path.relative_to(path).parts:
            path = path / part
            if path.is_symlink() or path.is_junction():
                raise ValueError("Package ancestor changed to a filesystem link")
        package_files(version.path)
        if content_hash(version.path) != version.content_hash:
            raise ValueError("Changed package")
    except (OSError, ValueError) as exc:
        raise LibraryError(changed, "Library package changed after indexing", 409) from exc
    return version.path / filename


def invalid(message, *, path="", **details):
    return LibraryError(
        "import_invalid",
        "Invalid library package",
        422,
        problems=[{"path": path, "message": message}],
        **details,
    )


def package_entries(path, manifest):
    files = set(package_files(path))
    if manifest not in files:
        raise invalid(f"Package metadata must be named exactly {manifest}", path=manifest)
    for entry in path.iterdir():
        if entry.name.casefold() == "validation.json" and (
            entry.name != "validation.json" or not entry.is_file()
        ):
            raise invalid("validation.json is reserved for gateway-generated reports", path="validation.json")
    return files


def destination_parent(root, parts, *, create=False):
    path = root
    for part in parts:
        path = path / part
        if (
            path.is_symlink()
            or path.is_junction()
            or not path.resolve().is_relative_to(root)
            or (path.exists() and not path.is_dir())
        ):
            raise invalid("Library destination contains a link or invalid ancestor")
        if create:
            path.mkdir(exist_ok=True)
    return path


def relative_path(value):
    """One portable path spelling, including when the gateway runs on Windows."""
    if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value:
        raise ValueError("Expected a nonempty NFC relative path")
    parts = value.split("/")
    if any(
        part in ("", ".", "..")
        or part.endswith((".", " "))
        or RESERVED.fullmatch(part)
        or any(character in '<>:"\\|?*' or ord(character) < 32 for character in part)
        for part in parts
    ):
        raise ValueError("Unsafe or ambiguous package path")
    return PurePosixPath(value)


def package_files(path):
    files, names = [], {}
    for entry in sorted(path.rglob("*")):
        relative = entry.relative_to(path).as_posix()
        relative_path(relative)
        key = relative.casefold()
        if (
            key in names
            or entry.is_symlink()
            or entry.is_junction()
            or not entry.resolve().is_relative_to(path.resolve())
        ):
            raise ValueError("Package contains links or ambiguous filenames")
        names[key] = relative
        if entry.is_file():
            files.append(relative)
        elif not entry.is_dir():
            raise ValueError("Package contains a nonregular file")
    return files


def extract_package(stream, path):
    names, entries, files, size = {}, set(), set(), 0
    try:
        # Uncompressed streaming tar keeps metadata reads inside the upload byte budget.
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for index, entry in enumerate(archive):
                if index >= MAX_FILES:
                    raise invalid(f"Package exceeds {MAX_FILES} entries")
                relative = entry.name.removesuffix("/") if entry.isdir() else entry.name
                try:
                    parts = relative_path(relative).parts
                except ValueError as exc:
                    raise invalid(str(exc), path=entry.name) from exc
                key = relative.casefold()
                if key in entries or any(
                    "/".join(parts[:i]).casefold() in files for i in range(1, len(parts))
                ):
                    raise invalid("Duplicate or conflicting archive path", path=relative)
                for i in range(1, len(parts) + 1):
                    prefix = "/".join(parts[:i])
                    if prefix.casefold() in names and names[prefix.casefold()] != prefix:
                        raise invalid("Case aliases are not portable", path=relative)
                    names.setdefault(prefix.casefold(), prefix)
                entries.add(key)
                if (not entry.isdir() and not entry.isreg()) or entry.sparse is not None:
                    raise invalid("Only regular files and directories are accepted", path=relative)
                target = path.joinpath(*parts)
                if entry.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if any(name.startswith(key + "/") for name in names):
                    raise invalid("File conflicts with an archive directory", path=relative)
                size += entry.size
                if entry.size < 0 or size > MAX_PACKAGE_BYTES:
                    raise invalid("Expanded package exceeds 256 MiB")
                files.add(key)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as source, target.open("xb") as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise invalid("Unreadable tar package") from exc


@contextmanager
def import_lock(root):
    """Serialize revision allocation across workers using the same user library."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".import.lock"
    if lock_path.is_symlink() or lock_path.is_junction():
        raise invalid("Library import lock cannot be a filesystem link")
    with lock_path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
