"""
memguard.context
~~~~~~~~~~~~~~~~
``MemGuardContext`` — context manager for profiling arbitrary code blocks.

Usage
-----
::

    from memguard import MemGuardContext

    with MemGuardContext("load-users"):
        users = db.query(User).all()
        process(users)

With full options::

    with MemGuardContext(
        name="etl-pipeline",
        threshold_mb=50,
        top_n=20,
        verbose=True,
        export_json="reports/etl.json",
        track_objects=True,
    ):
        run_etl()
"""

from __future__ import annotations

import time
import tracemalloc
from typing import Optional

from ._constants import DEFAULT_THRESHOLD_MB, DEFAULT_TOP_N, TRACEMALLOC_DEPTH
from ._profiler import (
    _call_history,
    _history_lock,
    compute_object_delta,
    get_tracemalloc_stats,
    snapshot_objects,
)
from ._report import MemGuardReport, print_report
from ._suggestions import build_suggestions


class MemGuardContext:
    """
    Context manager that profiles the memory behaviour of an arbitrary
    code block.

    Parameters
    ----------
    name : str
        Label used as the function-name key in the report and call history.
        Defaults to ``"block"``.
    threshold_mb : float
        Net growth threshold for leak warnings.  Default: ``2.0`` MB.
    top_n : int
        Number of tracemalloc allocation sites to display.  Default: ``15``.
    verbose : bool
        Print the object-delta and allocation-site tables.  Default: ``True``.
    export_json : str | None
        Path to write a JSON report after exit.  ``"auto"`` generates a
        timestamped name.
    track_objects : bool
        Snapshot gc object counts.  Default: ``True``.

    Attributes
    ----------
    report : MemGuardReport | None
        Available after the ``with`` block exits; ``None`` before entry.

    Examples
    --------
    Basic::

        with MemGuardContext("redis-warm"):
            redis_client.flushdb()
            warm_cache(redis_client)

    Accessing the report after the block::

        ctx = MemGuardContext("analyse")
        with ctx:
            heavy_computation()
        print(ctx.report.net_mb)
    """

    def __init__(
        self,
        name: str = "block",
        threshold_mb: float = DEFAULT_THRESHOLD_MB,
        top_n: int = DEFAULT_TOP_N,
        verbose: bool = True,
        export_json: Optional[str] = None,
        track_objects: bool = True,
    ) -> None:
        self.name          = name
        self.threshold_mb  = threshold_mb
        self.top_n         = top_n
        self.verbose       = verbose
        self.export_json   = export_json
        self.track_objects = track_objects

        # Set after __exit__
        self.report: MemGuardReport | None = None

        # Internal state
        self._obj_before:   dict = {}
        self._snap_before:  tracemalloc.Snapshot | None = None
        self._mem_before:   tuple[int, int] = (0, 0)
        self._t_start:      float = 0.0

    # ── Context protocol ─────────────────────────────────────────────────────

    def __enter__(self) -> "MemGuardContext":
        self._obj_before = snapshot_objects() if self.track_objects else {}

        if not tracemalloc.is_tracing():
            tracemalloc.start(TRACEMALLOC_DEPTH)

        self._snap_before = tracemalloc.take_snapshot()
        self._mem_before  = tracemalloc.get_traced_memory()
        self._t_start     = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        duration   = time.perf_counter() - self._t_start
        snap_after = tracemalloc.take_snapshot()
        mem_after  = tracemalloc.get_traced_memory()

        report = MemGuardReport(self.name, "context-manager")
        report.duration_s    = duration
        report.mem_before_mb = self._mem_before[0] / 1_048_576
        report.mem_after_mb  = mem_after[0] / 1_048_576
        report.peak_mb       = mem_after[1] / 1_048_576
        report.net_mb        = report.mem_after_mb - report.mem_before_mb

        report.top_stats = get_tracemalloc_stats(
            snap_after, self._snap_before, self.top_n
        )

        if self.track_objects:
            obj_after = snapshot_objects()
            report.object_delta = compute_object_delta(self._obj_before, obj_after)
            report.leaked_types = [
                f"{name}: {b} → {a} (+{d})"
                for name, (b, a, d) in report.object_delta.items()
                if d > 50
            ]

        if exc_type is not None:
            report.exception = f"{exc_type.__name__}: {exc_val}"

        with _history_lock:
            report.call_index = len(_call_history[self.name]) + 1
            prior_history = list(_call_history[self.name])

        report.suggestions = build_suggestions(report, prior_history)

        with _history_lock:
            _call_history[self.name].append(report.to_dict())

        self.report = report

        print_report(report, verbose=self.verbose, top_n=self.top_n)

        if self.export_json:
            if self.export_json == "auto":
                from datetime import datetime
                path = (
                    f"memguard_{self.name}_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                )
            else:
                path = self.export_json
            report.save_json(path)

        return False  # never suppress exceptions