"""
memguard._suggestions
~~~~~~~~~~~~~~~~~~~~~
Stateless suggestion engine.  Given a MemGuardReport and the call
history for the same function, it returns a list of plain-English
diagnostic strings.

All thresholds are imported from ``_constants`` so they can be tuned
project-wide without touching this file.
"""

from __future__ import annotations

from ._constants import (
    DEFAULT_THRESHOLD_MB,
    HOTSPOT_BYTES,
    LEAK_OBJECT_THRESHOLD,
    PEAK_WARN_MULTIPLIER,
    SLOW_CALL_THRESHOLD_S,
)
from ._report import MemGuardReport


def build_suggestions(
    report: MemGuardReport,
    call_history: list[dict],
) -> list[str]:
    """
    Analyse *report* and the prior *call_history* for the same function.

    Parameters
    ----------
    report:
        The freshly-populated MemGuardReport for this call.
    call_history:
        List of ``report.to_dict()`` snapshots from *previous* calls
        (i.e. not including the current one).

    Returns
    -------
    list[str]
        One or more diagnostic strings.  If nothing is wrong the list
        contains a single "looks healthy" message.
    """
    tips: list[str] = []

    # ── 1. Net memory growth ─────────────────────────────────────────────────
    if report.net_mb > DEFAULT_THRESHOLD_MB:
        tips.append(
            f"HIGH NET MEMORY GROWTH: +{report.net_mb:.2f} MB retained after "
            "the call. Check for unclosed resources, unbounded caches, or "
            "circular references preventing GC collection."
        )

    # ── 2. Memory churn (high peak, low net) ─────────────────────────────────
    if (
        report.peak_mb > report.net_mb * PEAK_WARN_MULTIPLIER
        and report.net_mb > 0.1
    ):
        ratio = report.peak_mb / max(report.net_mb, 0.001)
        tips.append(
            f"MEMORY CHURN DETECTED: Peak ({report.peak_mb:.2f} MB) is "
            f"{ratio:.1f}x the net retained size. Large intermediate "
            "allocations are being created and discarded. Consider "
            "streaming or chunking the data to reduce peak pressure."
        )

    # ── 3. Slow execution ─────────────────────────────────────────────────────
    if report.duration_s > SLOW_CALL_THRESHOLD_S:
        tips.append(
            f"SLOW EXECUTION: {report.duration_s:.2f}s wall time. "
            "Run cProfile or line_profiler to identify CPU hotspots. "
            "Check for N+1 queries, redundant recomputation, or GIL contention."
        )

    # ── 4. Object-level leak candidates ──────────────────────────────────────
    for type_name, (b, a, d) in report.object_delta.items():
        if d > LEAK_OBJECT_THRESHOLD:
            tips.append(
                f"OBJECT LEAK CANDIDATE: '{type_name}' grew by {d} instances "
                f"({b} → {a}). Ensure all references are released after use. "
                "Consider weakref.WeakValueDictionary for caches."
            )

    # ── 5. Single-site allocation hotspot ────────────────────────────────────
    if report.top_stats:
        top = report.top_stats[0]
        if top["size_b"] > HOTSPOT_BYTES:
            tips.append(
                f"ALLOCATION HOTSPOT: {top['size_b'] / 1_048_576:.2f} MB "
                f"allocated at {top['file']}:{top['line']}. This single site "
                "dominates memory usage — review it for unnecessary copies or "
                "large in-memory buffers."
            )

    # ── 6. Monotonic growth trend across calls ────────────────────────────────
    recent = [h["net_mb"] for h in call_history[-3:]] + [report.net_mb]
    if len(recent) >= 3 and all(
        recent[i] < recent[i + 1] for i in range(len(recent) - 1)
    ):
        trend_str = " → ".join(f"{x:.3f} MB" for x in recent)
        tips.append(
            f"MONOTONIC GROWTH TREND: Net memory is increasing every call "
            f"({trend_str}). Strong indicator of a cross-call leak — an "
            "object is being accumulated globally and never freed."
        )

    # ── 7. All clear ──────────────────────────────────────────────────────────
    if not tips:
        tips.append(
            "No issues detected. Memory footprint, object counts, and "
            "execution time are all within acceptable thresholds."
        )

    return tips