"""
memguard._constants
~~~~~~~~~~~~~~~~~~~
All tuneable thresholds and default values used across MemGuard.
Import and override these before calling any MemGuard API if you need
project-wide defaults different from the shipped ones.

Example::

    import memguard._constants as C
    C.DEFAULT_THRESHOLD_MB = 10.0
    C.LEAK_OBJECT_THRESHOLD = 200
"""

# ── Memory thresholds ────────────────────────────────────────────────────────

# Net memory growth (MB) above which a call is flagged as leaking.
DEFAULT_THRESHOLD_MB: float = 2.0

# Peak/net ratio above which "memory churn" is reported.
PEAK_WARN_MULTIPLIER: float = 3.0

# ── Object tracking ──────────────────────────────────────────────────────────

# A gc-tracked type that grows by more than this many instances per call
# is flagged as a leak candidate.
LEAK_OBJECT_THRESHOLD: int = 50

# ── Performance ──────────────────────────────────────────────────────────────

# Wall-clock time (seconds) above which "SLOW EXECUTION" is flagged.
SLOW_CALL_THRESHOLD_S: float = 1.0

# Single allocation site (file:line) size in bytes above which a HOTSPOT
# warning is emitted.
HOTSPOT_BYTES: int = 1 * 1024 * 1024  # 1 MB

# ── Display ───────────────────────────────────────────────────────────────────

# Default number of top allocation sites shown in the report.
DEFAULT_TOP_N: int = 15

# Column width used when rendering separator lines in reports.
REPORT_WIDTH: int = 68

# ── tracemalloc ───────────────────────────────────────────────────────────────

# Stack depth passed to tracemalloc.start().  Higher = more precise blame
# attribution, but slightly more overhead.
TRACEMALLOC_DEPTH: int = 25