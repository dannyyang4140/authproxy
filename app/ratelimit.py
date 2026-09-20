"""进程内滑动窗口限流（按 Token）。

单 uvicorn worker 下准确；多 worker 水平扩展时请改为 Redis 实现（接口保持 allow 即可）。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit_per_min: int) -> bool:
        if not limit_per_min or limit_per_min <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit_per_min:
                return False
            bucket.append(now)
            return True
