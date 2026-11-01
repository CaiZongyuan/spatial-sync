"""Package-local source receipts shared by procedural and derived assets."""

import hashlib


def file_hashes(package):
    return {
        path.relative_to(package).as_posix(): {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(package.rglob("*"))
        if path.is_file() and path.name != "provenance.json"
    }
