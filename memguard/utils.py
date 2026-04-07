"""
memguard.utils
~~~~~~~~~~~~~~
Standalone utilities that complement the decorator and context manager.

Functions
---------
inspect_object(obj, label)
    Deep-dive a single live object: shallow size, referrers, gc.garbage.

summary()
    Print a cross-function summary table of all profiled calls so far.
"""

from __future__ import annotations

import collections
import gc
import sys
from typing import Any

from ._colors import BOLD, CYAN, DIM, GREEN, MAGENTA, RED, YELLOW
from ._constants import DEFAULT_THRESHOLD_MB, REPORT_WIDTH
from ._profiler import all_call_history


# ── Helper ────────────────────────────────────────────────────────────────────

def _fmt(b: int) -> str:
    if b < 1_024:
        return f"{b} B"
    if b < 1_024 ** 2:
        return f"{b / 1_024:.2f} KB"
    return f"{b / 1_024 ** 2:.2f} MB"


# ── Public API ────────────────────────────────────────────────────────────────

def inspect_object(obj: Any, label: str = "") -> None:
    """
    Inspect a live Python object and print a detailed retention report.

    Covers:

    * Shallow ``sys.getsizeof`` size.
    * Container length (if applicable).
    * Referrer count and type breakdown — the objects keeping *obj* alive.
    * Whether *obj* appears in ``gc.garbage`` (a sign of a gc-uncollectable
      reference cycle).

    Parameters
    ----------
    obj : Any
        The object to inspect.
    label : str
        Human-readable name shown in the report header.

    Examples
    --------
    ::

        cache = {}
        memguard.inspect_object(cache, label="user_cache")

        # Or via the top-level alias:
        memguard.inspect(cache, label="user_cache")
    """
    W = REPORT_WIDTH
    name = label or repr(obj)[:60]

    size_b   = sys.getsizeof(obj)
    refs     = gc.get_referrers(obj)
    ref_types = collections.Counter(type(r).__name__ for r in refs)

    print()
    print(BOLD(DIM("═" * W)))
    print(BOLD(MAGENTA(f"  🔬  MemGuard Inspector  →  {name}")))
    print(BOLD(DIM("─" * W)))
    print(f"  {'Python type':<25s}  {type(obj).__qualname__}")
    print(f"  {'Shallow size':<25s}  {_fmt(size_b)}")

    if hasattr(obj, "__len__"):
        try:
            print(f"  {'Length / item count':<25s}  {len(obj)}")
        except Exception:
            pass

    if hasattr(obj, "__sizeof__"):
        pass  # sys.getsizeof already calls __sizeof__

    print(f"  {'Direct referrer count':<25s}  {len(refs)}")
    if ref_types:
        parts = ", ".join(f"{k} ×{v}" for k, v in ref_types.most_common(6))
        print(f"  {'Referrer types':<25s}  {parts}")

    # gc.garbage check
    gc.collect()
    in_garbage = any(obj is g for g in gc.garbage)
    garbage_str = RED("YES — uncollectable cycle!") if in_garbage else GREEN("No")
    print(f"  {'In gc.garbage':<25s}  {garbage_str}")

    # Weak-reference support
    import weakref
    try:
        weakref.ref(obj)
        wr_support = GREEN("Yes")
    except TypeError:
        wr_support = YELLOW("No (built-in type)")
    print(f"  {'Supports weakref':<25s}  {wr_support}")

    print(BOLD(DIM("═" * W)))
    print()


def summary() -> None:
    """
    Print a summary table of every function/block profiled in this session.

    The table shows call count, maximum net memory growth, and average
    net growth per call.  Functions are colour-coded:

    * 🔴 Red   — max net growth exceeds ``DEFAULT_THRESHOLD_MB``
    * 🟡 Yellow — max net growth is between 0.5 MB and threshold
    * 🟢 Green  — within healthy bounds

    Example
    -------
    ::

        import memguard
        memguard.summary()
    """
    W    = REPORT_WIDTH
    data = all_call_history()

    if not data:
        print(YELLOW("  [MemGuard] No profiling data collected yet."))
        return

    print()
    print(BOLD(DIM("═" * W)))
    print(BOLD(CYAN("  📋  MEMGUARD SESSION SUMMARY")))
    print(BOLD(DIM("─" * W)))
    print(
        f"  {'Function / Block':<36s} {'Calls':>5s}  "
        f"{'Max Net':>10s}  {'Avg Net':>10s}  {'Leaked?':>8s}"
    )
    print(BOLD(DIM("─" * W)))

    for fname, calls in sorted(data.items()):
        nets      = [c["net_mb"] for c in calls]
        max_net   = max(nets)
        avg_net   = sum(nets) / len(nets)
        has_leaks = any(c["leaked_types"] for c in calls)

        if max_net > DEFAULT_THRESHOLD_MB:
            color = RED
        elif max_net > 0.5:
            color = YELLOW
        else:
            color = GREEN

        leaked_str = RED("YES ⚠") if has_leaks else GREEN("No")
        fn_display = fname if len(fname) <= 36 else "…" + fname[-35:]
        print(
            f"  {fn_display:<36s} {len(calls):>5d}  "
            f"{color(f'{max_net:>+10.4f}')}"
            f"  {avg_net:>+10.4f}  {leaked_str:>8s}"
        )

    print(BOLD(DIM("═" * W)))
    print()