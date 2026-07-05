from __future__ import annotations

import asyncio
from collections import defaultdict
from threading import Lock


class LockRegistry:
    """Per-key download locks for the era5 ingestion pipeline.

    Each ``download_lock(key)`` returns an :class:`asyncio.Lock` keyed on a
    caller-chosen string (e.g. ``month_bundle_hash(provider, variable,
    year, month)``). The first caller for a given key creates the lock;
    subsequent callers share it. ``_registry_lock`` serialises the
    lookup-or-create in the defaultdict itself.

    ``asyncio.Lock`` is used (not ``threading.Lock``) because the lock is
    held across ``await`` boundaries in
    :meth:`Downloader.ensure_dataset`. Using a thread lock there would
    block the event loop while a CDS download runs, starving every other
    coroutine in the process.

    ``_registry_lock`` itself is a :class:`threading.Lock` because it only
    guards a single dict lookup (``download_lock`` is called from sync
    code paths and must not require ``await``). Using ``asyncio.Lock``
    here would surface as ``TypeError: 'Lock' object does not support the
    context manager protocol`` at every call site.

    ``manifest_lock`` was removed when ``ManifestManager`` was deleted:
    there is no longer a JSON file to protect, so the manifest-level
    lock would have been dead weight.
    """

    def __init__(self) -> None:
        self.queue_lock = asyncio.Lock()
        self._registry_lock = Lock()
        self._download_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def download_lock(self, key: str) -> asyncio.Lock:
        with self._registry_lock:
            return self._download_locks[key]


lock_registry = LockRegistry()
