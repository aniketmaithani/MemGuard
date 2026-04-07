"""
memguard._profiler
~~~~~~~~~~~~~~~~~~
Low-level profiling primitives shared by the decorator and the
context manager.  Nothing in here does any printing or suggestion
generation — it only collects raw measurements.
"""

from __future__ import annotations

import collections
import gc
import time
import traceback
import tracemalloc
import threading
from typing import Any, Callable

from ._constants import TRACEMALLOC_DEPTH
from ._report import MemGuardReport
from ._suggestions import build_suggestions


# ── Per-function call history (thread-safe) ──────────────────────────────────

_call_history: dict[str, list[dict]] = collections.defaultdict(list)
_history_lock = threading.Lock()


def get_call_history(func_name: str) -> list[dict]:
    """Return the list of past ``report.to_dict()`` entries for *func_name*."""
    with _history_lock:
        return list(_call_history[func_name])


def clear_call_history(func_name: str | None = None) -> None:
    """
    Clear stored call history.

    Parameters
    ----------
    func_name:
        If given, clears only the history for that function.
        If ``None``, clears *all* stored history.
    """
    with _history_lock:
        if func_name is None:
            _call_history.clear()
        else:
            _call_history.pop(func_name, None)


def all_call_history() -> dict[str, list[dict]]:
    """Return a shallow copy of the entire call-history registry."""
    with _history_lock:
        return {k: list(v) for k, v in _call_history.items()}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_bytes(b: int) -> str:
    if b < 1_024:
        return f"{b} B"
    if b < 1_024 ** 2:
        return f"{b / 1_024:.2f} KB"
    return f"{b / 1_024 ** 2:.2f} MB"


def snapshot_objects() -> dict[str, int]:
    """
    Return a ``{type_name: count}`` mapping of all gc-tracked objects.
    Forces a full collection first so counts are stable.
    """
    gc.collect()
    counts: collections.Counter = collections.Counter()
    for obj in gc.get_objects():
        counts[type(obj).__name__] += 1
    return dict(counts)


def compute_object_delta(
    before: dict[str, int],
    after: dict[str, int],
) -> dict[str, tuple[int, int, int]]:
    """Return ``{type_name: (before, after, delta)}`` for every changed type."""
    result = {}
    for k in set(before) | set(after):
        b = before.get(k, 0)
        a = after.get(k, 0)
        if a != b:
            result[k] = (b, a, a - b)
    return result


def get_tracemalloc_stats(
    snap_after: tracemalloc.Snapshot,
    snap_before: tracemalloc.Snapshot,
    top_n: int,
) -> list[dict]:
    """Diff two snapshots and return the top *top_n* allocation sites."""
    stats = []
    for stat in snap_after.compare_to(snap_before, "lineno")[:top_n]:
        frame = stat.traceback[0]
        stats.append(
            {
                "file":   frame.filename,
                "line":   frame.lineno,
                "size":   _format_bytes(stat.size),
                "size_b": stat.size,
                "count":  stat.count,
            }
        )
    return stats


def _ensure_tracemalloc() -> None:
    if not tracemalloc.is_tracing():
        tracemalloc.start(TRACEMALLOC_DEPTH)


# ── Core profiling runner ─────────────────────────────────────────────────────

def run_profiled(
    func: Callable,
    args: tuple,
    kwargs: dict,
    func_name: str,
    label: str,
    top_n: int,
    track_objects: bool,
) -> tuple[Any, MemGuardReport]:
    """
    Execute *func* with *args*/*kwargs* and return ``(result, report)``.

    This is the single entry-point for all measurement logic.  Both the
    ``@memguard`` decorator and ``MemGuardContext`` call this (or the
    equivalent streaming version for context managers).

    Parameters
    ----------
    func:
        The callable to profile.
    args / kwargs:
        Arguments forwarded to *func*.
    func_name:
        Qualified name used as the report/history key.
    label:
        Optional human-readable tag for the report header.
    top_n:
        Number of tracemalloc allocation sites to capture.
    track_objects:
        Whether to snapshot gc object counts before and after.

    Returns
    -------
    result:
        Whatever *func* returned (or ``None`` if it raised).
    report:
        Fully populated ``MemGuardReport``.
    """
    report = MemGuardReport(func_name, label)

    with _history_lock:
        report.call_index = len(_call_history[func_name]) + 1

    obj_before = snapshot_objects() if track_objects else {}

    _ensure_tracemalloc()
    snap_before = tracemalloc.take_snapshot()
    mem_before  = tracemalloc.get_traced_memory()
    report.mem_before_mb = mem_before[0] / 1_048_576

    result:   Any  = None
    exc_info: str | None = None
    t_start = time.perf_counter()

    try:
        result = func(*args, **kwargs)
    except Exception:
        exc_info = traceback.format_exc()

    report.duration_s = time.perf_counter() - t_start

    snap_after = tracemalloc.take_snapshot()
    mem_after  = tracemalloc.get_traced_memory()
    report.mem_after_mb = mem_after[0] / 1_048_576
    report.peak_mb      = mem_after[1] / 1_048_576
    report.net_mb       = report.mem_after_mb - report.mem_before_mb

    report.top_stats = get_tracemalloc_stats(snap_after, snap_before, top_n)

    if track_objects:
        obj_after = snapshot_objects()
        report.object_delta = compute_object_delta(obj_before, obj_after)
        report.leaked_types = [
            f"{name}: {b} → {a} (+{d})"
            for name, (b, a, d) in report.object_delta.items()
            if d > 50
        ]

    if exc_info:
        report.exception = exc_info

    with _history_lock:
        prior_history = list(_call_history[func_name])

    report.suggestions = build_suggestions(report, prior_history)

    with _history_lock:
        _call_history[func_name].append(report.to_dict())

    return result, report