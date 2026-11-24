"""Rate gate for image calls: OpenAI limits images per minute (IPM), not calls in flight.

Full sets may use `use_ratio` of the per-minute limit; single-image redos may use all of it,
so a redo never waits behind a queue of full sets. After a 429 the gate pauses everyone
briefly instead of letting each call hammer the API on its own.
"""
from __future__ import annotations

import threading
import time
from collections import deque


class RateGate:
    def __init__(self, images_per_minute: float = 150, use_ratio: float = 0.8, window: float = 60.0):
        self.ipm = max(1.0, float(images_per_minute))
        self.ratio = min(1.0, max(0.1, float(use_ratio)))
        self.window = window
        self.starts: deque[float] = deque()
        self.paused_until = 0.0
        self.lock = threading.Lock()

    def limit(self, priority: bool) -> int:
        return max(1, int(self.ipm if priority else self.ipm * self.ratio))

    def _prune(self, now: float):
        while self.starts and now - self.starts[0] >= self.window:
            self.starts.popleft()

    def try_acquire(self, priority: bool = False, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            if now < self.paused_until:
                return False
            self._prune(now)
            if len(self.starts) >= self.limit(priority):
                return False
            self.starts.append(now)
            return True

    def acquire(self, priority: bool = False, poll: float = 0.2) -> None:
        while not self.try_acquire(priority):
            time.sleep(poll)

    def cool_down(self, seconds: float) -> None:
        with self.lock:
            self.paused_until = max(self.paused_until, time.monotonic() + seconds)

    def used(self) -> int:
        with self.lock:
            self._prune(time.monotonic())
            return len(self.starts)
