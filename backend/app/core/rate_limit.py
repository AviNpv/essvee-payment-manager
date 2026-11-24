"""Small in-process sliding-window limiter for authentication endpoints."""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_HITS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def limit(key: str, maximum: int, window_seconds: int = 60):
    def _check(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        bucket = _HITS[(key, ip)]
        now = time.monotonic()
        while bucket and bucket[0] < now - window_seconds:
            bucket.popleft()
        if len(bucket) >= maximum:
            raise HTTPException(
                status_code=429, detail="Too many requests. Please try again shortly."
            )
        bucket.append(now)

    return _check
