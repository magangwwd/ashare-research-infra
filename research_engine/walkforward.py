"""Deterministic time-only walk-forward split construction."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WalkForwardSplit:
    train: tuple
    validation: tuple
    test: tuple


def walk_forward_splits(
    dates,
    *,
    train_sessions: int,
    validation_sessions: int,
    test_sessions: int,
    step_sessions: int | None = None,
) -> list[WalkForwardSplit]:
    ordered = tuple(sorted(set(dates)))
    if min(train_sessions, validation_sessions, test_sessions) <= 0:
        raise ValueError("walk-forward windows must be positive")
    step = step_sessions or test_sessions
    if step <= 0:
        raise ValueError("step_sessions must be positive")
    width = train_sessions + validation_sessions + test_sessions
    result = []
    for start in range(0, len(ordered) - width + 1, step):
        a = start + train_sessions
        b = a + validation_sessions
        c = b + test_sessions
        result.append(WalkForwardSplit(
            train=ordered[start:a],
            validation=ordered[a:b],
            test=ordered[b:c],
        ))
    return result
