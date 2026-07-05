from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    return _hash_file(path, "sha256")


def md5_file(path: Path) -> str:
    return _hash_file(path, "md5")


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
