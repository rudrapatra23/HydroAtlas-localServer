from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import BinaryIO, Sequence

from domain.ports.storage_port import StoragePort
from core.config import get_settings


class LocalStorageAdapter(StoragePort):
    def __init__(self):
        settings = get_settings()
        base_dir = settings.storage_root_resolved()
        self.storage_dir = Path(os.getenv("LOCAL_STORAGE_DIR", base_dir))
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, key: str) -> Path:
        path = self.storage_dir / key
        # Prevent directory traversal attacks
        if not path.resolve().is_relative_to(self.storage_dir.resolve()):
            raise ValueError(f"Invalid key: {key}")
        return path

    def upload(self, key: str, data: BinaryIO | bytes | Path) -> None:
        path = self._get_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        if isinstance(data, Path):
            shutil.copy2(data, path)
        elif isinstance(data, bytes):
            path.write_bytes(data)
        else:
            with open(path, "wb") as f:
                shutil.copyfileobj(data, f)

    def download(self, key: str, target: BinaryIO | Path) -> None:
        path = self._get_path(key)
        if not path.exists():
            raise FileNotFoundError(f"Key not found: {key}")
            
        if isinstance(target, Path):
            shutil.copy2(path, target)
        else:
            with open(path, "rb") as f:
                shutil.copyfileobj(f, target)

    def exists(self, key: str) -> bool:
        return self._get_path(key).exists()

    def delete(self, key: str) -> None:
        path = self._get_path(key)
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self._get_path(key).as_uri()

    def list(self, prefix: str = "") -> Sequence[str]:
        keys = []
        prefix_path = self.storage_dir / prefix
        
        # If the prefix itself is a directory, list its contents
        if prefix_path.is_dir():
            search_dir = prefix_path
        else:
            # Otherwise, list from the parent directory matching the prefix
            search_dir = prefix_path.parent
            
        if not search_dir.exists():
            return keys
            
        for path in search_dir.rglob("*"):
            if path.is_file():
                rel_key = path.relative_to(self.storage_dir).as_posix()
                if rel_key.startswith(prefix):
                    keys.append(rel_key)
        return keys
