"""
memguard.decorator
~~~~~~~~~~~~~~~~~~
``@memguard()`` — the primary public interface for profiling functions.

Usage
-----
Basic::

    from memguard import memguard

    @memguard()
    def process(records):
        ...

With options::

    @memguard(
        threshold_mb=10,
        top_n=20,
        verbose=True,
        label="batch-processor",
        export_json="reports/process.json",
        track_objects=True,
    )
    def process(records):
        ...

The decorator is safe to stack on top of other decorators and works
with methods, classmethods, staticmethods, async functions are NOT yet
supported (open an issue).
"""

from __future__ import annotations

import functools
from typing import Callable, Optional

from ._constants import DEFAULT_THRESHOLD_MB, DEFAULT_TOP_N
from ._profiler import run_profiled
from ._report import print_report


def memguard(
    threshold_mb: float = DEFAULT_THRESHOLD_MB,
    top_n: int = DEFAULT_TOP_N,
    verbose: bool = True,
    label: str = "",
    export_json: Optional[str] = None,
    track_objects: bool = True,
) -> Callable:
    """
    Decorator factory that wraps a function with memory leak analysis.

    Parameters
    ----------
    threshold_mb : float
        Net memory growth in MB above which the call is flagged as
        leaking.  Default: ``2.0``.
    top_n : int
        Number of top tracemalloc allocation sites to display and store.
        Default: ``15``.
    verbose : bool
        When ``True`` (default), prints the object-delta table and the
        full allocation-site table.  Set to ``False`` for a one-liner
        summary only.
    label : str
        Optional human-readable tag appended to the report header, e.g.
        ``"first-load"`` vs ``"warm-cache"``.
    export_json : str | None
        If given, the report is written to this path as a JSON file
        after every call.  Use ``"auto"`` to generate a timestamped
        filename automatically.
    track_objects : bool
        When ``True`` (default), gc object counts are snapshotted before
        and after the call to detect per-type leaks.  Adds a small but
        measurable overhead for functions called at very high frequency.

    Returns
    -------
    Callable
        A wrapped version of the decorated function.  The wrapper
        preserves ``__name__``, ``__doc__``, and all other attributes
        via ``functools.wraps``.

    Examples
    --------
    Minimal::

        @memguard()
        def load_data(path: str) -> list:
            with open(path) as f:
                return f.readlines()

    With JSON export::

        @memguard(threshold_mb=5, export_json="reports/load_data.json")
        def load_data(path: str) -> list:
            ...

    Suppress verbose output in CI::

        @memguard(verbose=False)
        def nightly_job():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result, report = run_profiled(
                func=func,
                args=args,
                kwargs=kwargs,
                func_name=func.__qualname__,
                label=label,
                top_n=top_n,
                track_objects=track_objects,
            )

            print_report(report, verbose=verbose, top_n=top_n)

            if export_json:
                if export_json == "auto":
                    from datetime import datetime
                    path = (
                        f"memguard_{func.__name__}_"
                        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    )
                else:
                    path = export_json
                report.save_json(path)
                from ._colors import CYAN
                print(CYAN(f"  [MemGuard] Report saved → {path}\n"))

            if report.exception:
                raise RuntimeError(
                    f"[MemGuard] Wrapped function raised:\n{report.exception}"
                )

            return result

        # Marker so callers can detect instrumented functions.
        wrapper.__memguard__ = True  # type: ignore[attr-defined]
        return wrapper

    return decorator