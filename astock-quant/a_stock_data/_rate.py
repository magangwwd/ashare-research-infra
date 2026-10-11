"""Per-source rate limiter (simple token-bucket via monotonic clock)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# Minimum seconds between successive calls to the same source.
# Tuned from P1 stability test results.
_DEFAULTS: dict[str, float] = {
    "mootdx": 0.2,
    "akshare": 1.2,
    "cninfo": 6.0,   # cninfo triggers soft-block under 5s; use 6s floor
}


@dataclass
class _Limiter:
    min_interval: float
    _last: float = field(default=0.0, init=False, repr=False)

    def wait(self) -> None:
        now = time.monotonic()
        gap = self.min_interval - (now - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


_limiters: dict[str, _Limiter] = {
    src: _Limiter(interval) for src, interval in _DEFAULTS.items()
}


def wait(source: str) -> None:
    """Block until the per-source interval has elapsed since the last call."""
    limiter = _limiters.get(source)
    if limiter is not None:
        limiter.wait()
