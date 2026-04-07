# MemGuard

> Zero-dependency Python memory leak detector and analyser.  
> Drop it on any function or code block and get a verbose, colour-coded report in your terminal.

**Author:** Aniket Maithani  
**License:** MIT  
**Python:** 3.10+  
**Dependencies:** none (stdlib only — `tracemalloc`, `gc`, `threading`)

---

## Table of Contents

1. [Why MemGuard?](#why-memguard)
2. [Installation](#installation)
3. [Quick Start](#quick-start)
4. [Core Concepts](#core-concepts)
   - [How memory is measured](#how-memory-is-measured)
   - [How object leaks are detected](#how-object-leaks-are-detected)
   - [The suggestion engine](#the-suggestion-engine)
5. [API Reference](#api-reference)
   - [@memguard() decorator](#memguard-decorator)
   - [MemGuardContext](#memguardcontext)
   - [inspect / inspect_object](#inspect--inspect_object)
   - [summary](#summary)
   - [clear_history](#clear_history)
6. [Reading a Report](#reading-a-report)
7. [Diagnostic Signals](#diagnostic-signals)
8. [Configuration & Tuning](#configuration--tuning)
9. [JSON Reports](#json-reports)
10. [Testing Your Own Code](#testing-your-own-code)
11. [Project Structure](#project-structure)
12. [Contributing](#contributing)

---

## Why MemGuard?

Python's garbage collector makes explicit memory management rare — until it isn't.  
Common silent killers:

| Pattern                                      | Why it leaks                              |
| -------------------------------------------- | ----------------------------------------- |
| Growing module-level `list` / `dict`         | Never collected; reference held forever   |
| Event listeners / callbacks not deregistered | Listener holds reference to the object    |
| Circular references with `__del__`           | gc cannot break the cycle                 |
| `functools.lru_cache` with mutable keys      | Cache grows without bound                 |
| ORM sessions never closed                    | SQLAlchemy / Django ORM hold live objects |
| Large temporary buffers                      | Peak can be 10–50× the retained size      |

Tools like `tracemalloc` and `gc` expose all the raw data you need — but interpreting
that data during fast iteration is tedious. **MemGuard wraps those tools in a single
decorator** and adds:

- Per-call object-count delta by type (not just byte sizes)
- Cross-call trend detection (monotonically growing net memory = definite leak)
- Human-readable, coloured terminal output
- JSON report export for CI/post-mortem analysis
- Zero runtime overhead when not active (the decorator is a thin wrapper)

---

## Installation

**From source (development):**

```bash
git clone https://github.com/aniketmaithani/memguard.git
cd memguard
pip install -e ".[dev]"
```

**As a dependency in another project:**

```bash
pip install memguard          # once published to PyPI
# or
pip install git+https://github.com/aniketmaithani/memguard.git
```

**Verify:**

```python
import memguard
print(memguard.__version__)   # 1.0.0
```

---

## Quick Start

```python
import memguard
from memguard import MemGuardContext

# ── 1. Decorator ──────────────────────────────────────────────────────────────
@memguard.memguard()
def load_records(path: str) -> list:
    with open(path) as f:
        return f.readlines()

load_records("data.csv")


# ── 2. Context manager ────────────────────────────────────────────────────────
with MemGuardContext("cache-warm"):
    from myapp.cache import warm_all
    warm_all()


# ── 3. Inspect a live object ─────────────────────────────────────────────────
_user_cache = {}
memguard.inspect(_user_cache, label="user_cache")


# ── 4. Session summary ────────────────────────────────────────────────────────
memguard.summary()
```

---

## Core Concepts

### How memory is measured

MemGuard uses Python's built-in `tracemalloc` module, which instruments the
allocator at the C level. This means every `malloc` call is tracked — including
allocations inside C extensions that create Python objects.

Three numbers are captured around every call:

| Metric     | What it means                                   |
| ---------- | ----------------------------------------------- |
| **Before** | `tracemalloc` current usage at call entry       |
| **After**  | `tracemalloc` current usage at call exit        |
| **Peak**   | `tracemalloc` high-water mark _during_ the call |
| **Net**    | `After − Before` — the headline leak indicator  |

`tracemalloc` is started with a stack depth of 25 frames (configurable via
`memguard._constants.TRACEMALLOC_DEPTH`) the first time MemGuard is used.
If you already have `tracemalloc.start()` in your code, MemGuard will not
interfere — it calls `tracemalloc.is_tracing()` first.

> **Important:** `tracemalloc` measures the Python allocator, not total
> process RSS. For NumPy / C-extension allocations that bypass `pymalloc`,
> use a tool like `memory_profiler` or Valgrind in addition to MemGuard.

### How object leaks are detected

Before and after every call, MemGuard calls `gc.collect()` and then iterates
`gc.get_objects()` to count every gc-tracked object by type. The delta is
computed per type.

A type is flagged as a **leak candidate** when its count grew by more than
`LEAK_OBJECT_THRESHOLD` (default: 50) in a single call. This catches:

- Unbounded `list` / `dict` growth
- Custom class instances that are never released
- Internal ORM / cache objects accumulating silently

The `track_objects=False` option skips this step for hot-path functions
where the gc iteration overhead is unacceptable.

### The suggestion engine

After every call, the `build_suggestions()` function inspects the report and
produces one or more plain-English diagnostics. The full list of signals:

| Signal                   | Condition                                  |
| ------------------------ | ------------------------------------------ |
| `HIGH NET MEMORY GROWTH` | `net_mb > threshold_mb`                    |
| `MEMORY CHURN DETECTED`  | `peak_mb > net_mb × 3` and `net_mb > 0.1`  |
| `SLOW EXECUTION`         | `duration_s > 1.0`                         |
| `OBJECT LEAK CANDIDATE`  | any type grew by > 50 instances            |
| `ALLOCATION HOTSPOT`     | top tracemalloc site > 1 MB                |
| `MONOTONIC GROWTH TREND` | last 3+ calls all show increasing `net_mb` |
| `No issues detected`     | none of the above triggered                |

All thresholds live in `memguard/_constants.py` and can be overridden at module
import time.

---

## API Reference

### `@memguard()` decorator

```python
from memguard import memguard

@memguard(
    threshold_mb  = 2.0,    # float  — net growth above this is flagged
    top_n         = 15,     # int    — allocation sites to display
    verbose       = True,   # bool   — show object-delta and site tables
    label         = "",     # str    — tag shown in report header
    export_json   = None,   # str    — path to write JSON; "auto" = timestamped name
    track_objects = True,   # bool   — snapshot gc object counts
)
def my_function(...):
    ...
```

**Return value:** the wrapped function returns whatever the original function
returns, unchanged.

**Exception handling:** if the wrapped function raises, MemGuard still prints
the report (so you can see how much memory was allocated before the crash),
then re-raises as a `RuntimeError` wrapping the original traceback.

**`__memguard__` attribute:** the wrapper exposes `wrapper.__memguard__ = True`
so you can programmatically detect instrumented functions.

---

### `MemGuardContext`

```python
from memguard import MemGuardContext

with MemGuardContext(
    name          = "block",   # str   — used as the history key
    threshold_mb  = 2.0,
    top_n         = 15,
    verbose       = True,
    export_json   = None,
    track_objects = True,
) as ctx:
    ...

# After exit:
print(ctx.report.net_mb)
print(ctx.report.suggestions)
```

`MemGuardContext` **never suppresses exceptions** — it records the exception
type and message in `report.exception`, prints the report, then lets the
exception propagate normally.

---

### `inspect` / `inspect_object`

```python
import memguard

# Both names are equivalent
memguard.inspect(obj, label="my_object")
memguard.inspect_object(obj, label="my_object")
```

Prints:

| Field                 | Description                                                 |
| --------------------- | ----------------------------------------------------------- |
| Python type           | `type(obj).__qualname__`                                    |
| Shallow size          | `sys.getsizeof(obj)`                                        |
| Length / item count   | `len(obj)` if applicable                                    |
| Direct referrer count | `len(gc.get_referrers(obj))`                                |
| Referrer types        | Top 6 types holding a reference to `obj`                    |
| In gc.garbage         | Whether `obj` appears in `gc.garbage` (uncollectable cycle) |
| Supports weakref      | Whether a `weakref.ref` can be created                      |

This is useful for investigating _why_ a specific object is staying alive
after you expected it to be collected.

---

### `summary`

```python
import memguard
memguard.summary()
```

Prints a table of every profiled function/block in the current process session:

```
════════════════════════════════════════════════════════════════════
  📋  MEMGUARD SESSION SUMMARY
────────────────────────────────────────────────────────────────────
  Function / Block                     Calls  Max Net     Avg Net  Leaked?
────────────────────────────────────────────────────────────────────
  load_records                             3   +0.0210    +0.0180     No
  process_batch                            5   +8.4321    +3.1200  YES ⚠
  cache_warm                               1   +0.0043    +0.0043     No
════════════════════════════════════════════════════════════════════
```

Color coding: 🔴 red = `max_net > threshold_mb`, 🟡 yellow = 0.5–2 MB, 🟢 green = clean.

---

### `clear_history`

```python
import memguard

memguard.clear_history()                     # clear ALL history
memguard.clear_history("my_module.my_func")  # clear one function only
```

Useful in tests to isolate each test case from call history accumulated in
previous tests.

---

## Reading a Report

```
════════════════════════════════════════════════════════════════════
  🔍  [MemGuard] process_batch (etl-run)  •  call #3
      2026-03-15T11:42:07.123456
════════════════════════════════════════════════════════════════════
  📊  MEMORY SUMMARY
────────────────────────────────────────────────────────────────────
  Before                    1.2341 MB
  After                     9.6201 MB
  Peak                     38.4100 MB      ← very high relative to net
  Net Growth               +8.3860 MB      ← red = above threshold
  Wall Time                 3.1200 s
────────────────────────────────────────────────────────────────────
  🧬  OBJECT DELTA
────────────────────────────────────────────────────────────────────
  Type                           Before     After     Delta
  dict                             1241      2890    +1649  ← red
  list                              820       835      +15
  MyRecord                            0       500     +500  ← red
────────────────────────────────────────────────────────────────────
  📍  TOP 15 ALLOCATION SITES
────────────────────────────────────────────────────────────────────
  File:Line                                          Size      Count
  myapp/etl.py:87                                 7.80 MB      1000  ← red
  myapp/models.py:44                            512.00 KB       500
────────────────────────────────────────────────────────────────────
  ⚠️  LEAK CANDIDATES
  ● MyRecord: 0 → 500 (+500)
────────────────────────────────────────────────────────────────────
  💡  ANALYSIS & SUGGESTIONS
  ⛔  HIGH NET MEMORY GROWTH: +8.39 MB retained after the call.
  ⚡  MEMORY CHURN DETECTED: Peak (38.41 MB) is 4.6× the net size.
  ⛔  SLOW EXECUTION: 3.12s wall time.
  ⛔  OBJECT LEAK CANDIDATE: 'MyRecord' grew by 500 instances...
════════════════════════════════════════════════════════════════════
```

**Section by section:**

- **MEMORY SUMMARY** — the four key numbers. Net Growth is the most important;
  if it grows call-over-call your function is leaking.
- **OBJECT DELTA** — sorted by absolute change, largest first. Red rows are leak
  candidates. Green rows (negative delta) mean objects were freed — good.
- **TOP ALLOCATION SITES** — the exact file and line responsible for the most bytes.
  Red = > 1 MB at a single site.
- **LEAK CANDIDATES** — types whose instance count grew beyond the threshold.
- **ANALYSIS & SUGGESTIONS** — plain-English diagnostics with actionable advice.

---

## Diagnostic Signals

### `HIGH NET MEMORY GROWTH`

Net memory retained after the call exceeds `threshold_mb`.

**Common causes:**

- Appending to a module-level collection (`_cache`, `_registry`, `_events`)
- Returning large objects from a function into an outer scope that never releases them
- Django / SQLAlchemy sessions keeping result-set objects alive

**Fix:** Profile the allocation sites, trace which collection they end up in,
add explicit `del` or use `weakref.WeakValueDictionary`.

---

### `MEMORY CHURN DETECTED`

The peak allocation was significantly higher than what was retained. This means
large objects were created and discarded during the call.

**Common causes:**

- `list(big_generator)` — materialises the whole thing before slicing
- String concatenation in a loop (`s += chunk`)
- Serialising / deserialising large structures without streaming

**Fix:** Stream data with generators, use `io.BytesIO` / `io.StringIO`, or process
in chunks to keep peak pressure low.

---

### `SLOW EXECUTION`

Wall-clock time exceeded `SLOW_CALL_THRESHOLD_S` (default 1s).

**Fix:** Profile with `cProfile` (`python -m cProfile -s cumtime script.py`) or
`line_profiler` (`@profile` decorator) to find CPU hotspots.

---

### `OBJECT LEAK CANDIDATE`

A gc-tracked type grew by more than `LEAK_OBJECT_THRESHOLD` instances in one call.

**Fix:** Check every place that creates instances of the flagged type. Look for
global registries, event buses, or class-level `_instances` lists. Use
`gc.get_referrers(obj)` or `memguard.inspect(obj)` to find what is holding the
reference.

---

### `ALLOCATION HOTSPOT`

A single source line is responsible for more than `HOTSPOT_BYTES` (default 1 MB)
of live allocations.

**Fix:** Open the file at that line. Common culprits: reading an entire file into
memory, a list comprehension over a large dataset, or an ORM `fetchall()`.

---

### `MONOTONIC GROWTH TREND`

Over the last 3+ calls, net memory has increased every single time. This is the
strongest possible signal of a cross-call leak.

**Fix:** Find what is accumulating between calls. Add `memguard.summary()` at
the end of a long-running process to see which function is responsible, then
inspect its module-level state.

---

## Configuration & Tuning

All default thresholds are defined in `memguard/_constants.py`. Override them
once at startup, before you instrument any functions:

```python
import memguard._constants as C

C.DEFAULT_THRESHOLD_MB   = 10.0   # more lenient for a data-heavy service
C.LEAK_OBJECT_THRESHOLD  = 200    # only flag very large object growth
C.SLOW_CALL_THRESHOLD_S  = 5.0    # tolerate slower operations
C.HOTSPOT_BYTES          = 5 * 1024 * 1024  # 5 MB hotspot threshold
C.DEFAULT_TOP_N          = 30     # show more allocation sites
```

These are module-level variables, so the change is global and immediate.

---

## JSON Reports

Every decorator and context manager call can export a JSON file:

```python
@memguard(export_json="reports/etl.json")
def run_etl():
    ...

# Auto-named timestamped file:
@memguard(export_json="auto")
def run_etl():
    ...
```

Schema:

```json
{
  "func_name": "run_etl",
  "label": "",
  "timestamp": "2026-03-15T11:42:07.123456",
  "call_index": 1,
  "duration_s": 3.12,
  "mem_before_mb": 1.23,
  "mem_after_mb": 9.62,
  "peak_mb": 38.41,
  "net_mb": 8.39,
  "leaked_types": ["MyRecord: 0 → 500 (+500)"],
  "suggestions": ["HIGH NET MEMORY GROWTH: ..."],
  "exception": null,
  "top_stats": [
    {
      "file": "myapp/etl.py",
      "line": 87,
      "size": "7.80 MB",
      "size_b": 8180000,
      "count": 1000
    }
  ]
}
```

You can aggregate these in CI to track memory regressions over time.

---

## Testing Your Own Code

MemGuard is test-friendly. Use `clear_history()` in `setUp` / `tearDown` or as
a pytest fixture to prevent cross-test contamination:

```python
import pytest
import memguard

@pytest.fixture(autouse=True)
def reset_memguard():
    memguard.clear_history()
    yield
    memguard.clear_history()
```

Assert on the report object directly:

```python
from memguard import MemGuardContext

def test_process_does_not_leak():
    ctx = MemGuardContext("process", threshold_mb=1.0, verbose=False)
    with ctx:
        result = process_records(load_fixtures())

    assert ctx.report.net_mb < 1.0, (
        f"process_records retained {ctx.report.net_mb:.2f} MB — possible leak"
    )
    assert ctx.report.leaked_types == [], ctx.report.leaked_types
```

Run the MemGuard test suite itself:

```bash
cd memguard
pytest -v
pytest -v --cov=memguard --cov-report=term-missing
```

---

## Project Structure

```
memguard/
│
├── memguard/                   # Installable package
│   ├── __init__.py             # Public API surface
│   ├── decorator.py            # @memguard() decorator
│   ├── context.py              # MemGuardContext context manager
│   ├── utils.py                # inspect_object(), summary()
│   ├── _profiler.py            # Low-level measurement engine
│   ├── _report.py              # MemGuardReport + pretty-printer
│   ├── _suggestions.py         # Diagnostic suggestion engine
│   ├── _colors.py              # ANSI color helpers
│   └── _constants.py           # All tuneable thresholds
│
├── tests/
│   ├── __init__.py
│   └── test_memguard.py        # Full test suite (~60 test cases)
│
├── pyproject.toml              # Build config, metadata, dev deps
└── README.md
```

**Module responsibilities at a glance:**

| Module            | Responsibility                                          |
| ----------------- | ------------------------------------------------------- |
| `_colors.py`      | ANSI terminal helpers, TTY detection                    |
| `_constants.py`   | Single source of truth for all thresholds               |
| `_report.py`      | `MemGuardReport` dataclass + terminal pretty-printer    |
| `_suggestions.py` | Stateless diagnostic engine, pure function              |
| `_profiler.py`    | `tracemalloc` + `gc` measurement, call history registry |
| `decorator.py`    | `@memguard()` public API                                |
| `context.py`      | `MemGuardContext` public API                            |
| `utils.py`        | `inspect_object`, `summary`                             |
| `__init__.py`     | Re-exports, version, author                             |

---

## Contributing

1. Fork the repo and create a feature branch.
2. Write tests for any new signal or threshold.
3. Run the test suite: `pytest -v --cov=memguard`.
4. Open a PR against `main`.

Issues, ideas, and pull requests are welcome.

---

_Made with care by Aniket Maithani._
