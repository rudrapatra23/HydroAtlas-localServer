from __future__ import annotations

import asyncio
from collections import defaultdict
from threading import Lock


class LockRegistry:
    """Per-key download locks for the era5 ingestion pipeline."""

    def __init__(self) -> None:
        self.queue_lock = asyncio.Lock()
        self._registry_lock = Lock()
        self._download_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def download_lock(self, key: str) -> asyncio.Lock:
        with self._registry_lock:
            return self._download_locks[key]


lock_registry = LockRegistry()
