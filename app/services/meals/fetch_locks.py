from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import date
from typing import Iterator


_registry_lock = threading.Lock()
_locks: dict[tuple[str, date], threading.Lock] = {}


@contextmanager
def meal_fetch_lock(restaurant_code: str, target_date: date) -> Iterator[None]:
    key = (restaurant_code, target_date)
    with _registry_lock:
        lock = _locks.setdefault(key, threading.Lock())

    lock.acquire()
    try:
        yield
    finally:
        lock.release()
