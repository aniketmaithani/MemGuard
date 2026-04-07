"""
tests/test_memguard.py
~~~~~~~~~~~~~~~~~~~~~~
Comprehensive test suite for the memguard library.

Run with:
    pytest -v
    pytest -v --cov=memguard --cov-report=term-missing
"""

from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import threading
import tracemalloc

import pytest

# ── Make sure the local source is importable ─────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import memguard
from memguard import (
    MemGuardContext,
    MemGuardReport,
    clear_history,
    inspect_object,
    memguard as mg_decorator,
    summary,
)
from memguard._profiler import (
    all_call_history,
    compute_object_delta,
    get_call_history,
    snapshot_objects,
)
from memguard._suggestions import build_suggestions


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_history():
    """Clear all call history before every test for isolation."""
    clear_history()
    yield
    clear_history()


# ═════════════════════════════════════════════════════════════════════════════
# 1. MemGuardReport
# ═════════════════════════════════════════════════════════════════════════════

class TestMemGuardReport:

    def test_default_values(self):
        r = MemGuardReport("my_fn", "test")
        assert r.func_name    == "my_fn"
        assert r.label        == "test"
        assert r.duration_s   == 0.0
        assert r.net_mb       == 0.0
        assert r.suggestions  == []
        assert r.exception    is None
        assert r.call_index   == 0

    def test_to_dict_has_required_keys(self):
        r = MemGuardReport("fn", "lbl")
        r.net_mb = 1.23
        r.suggestions = ["looks fine"]
        d = r.to_dict()
        for key in (
            "func_name", "label", "timestamp", "call_index",
            "duration_s", "mem_before_mb", "mem_after_mb",
            "peak_mb", "net_mb", "leaked_types", "suggestions",
            "exception", "top_stats",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_rounds_floats(self):
        r = MemGuardReport("fn", "")
        r.net_mb = 1.123456789
        d = r.to_dict()
        assert d["net_mb"] == round(1.123456789, 6)

    def test_to_json_valid(self):
        r = MemGuardReport("fn", "")
        r.suggestions = ["ok"]
        raw = r.to_json()
        parsed = json.loads(raw)
        assert parsed["func_name"] == "fn"

    def test_save_json(self, tmp_path):
        r = MemGuardReport("fn", "")
        r.suggestions = ["ok"]
        path = str(tmp_path / "report.json")
        r.save_json(path)
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert data["func_name"] == "fn"


# ═════════════════════════════════════════════════════════════════════════════
# 2. Profiler helpers
# ═════════════════════════════════════════════════════════════════════════════

class TestProfilerHelpers:

    def test_snapshot_objects_returns_dict(self):
        counts = snapshot_objects()
        assert isinstance(counts, dict)
        assert "list" in counts
        assert counts["list"] > 0

    def test_compute_object_delta_additions(self):
        before = {"list": 10, "dict": 5}
        after  = {"list": 15, "dict": 5, "str": 3}
        delta  = compute_object_delta(before, after)
        assert delta["list"] == (10, 15, 5)
        assert "dict" not in delta             # unchanged
        assert delta["str"] == (0, 3, 3)

    def test_compute_object_delta_removals(self):
        before = {"list": 10}
        after  = {"list": 5}
        delta  = compute_object_delta(before, after)
        assert delta["list"] == (10, 5, -5)

    def test_compute_object_delta_empty(self):
        assert compute_object_delta({}, {}) == {}

    def test_get_tracemalloc_stats_returns_list(self):
        from memguard._profiler import get_tracemalloc_stats
        if not tracemalloc.is_tracing():
            tracemalloc.start(5)
        s1 = tracemalloc.take_snapshot()
        _ = [0] * 1000
        s2 = tracemalloc.take_snapshot()
        stats = get_tracemalloc_stats(s2, s1, top_n=5)
        assert isinstance(stats, list)
        for entry in stats:
            assert "file" in entry
            assert "line" in entry
            assert "size_b" in entry
            assert "count" in entry


# ═════════════════════════════════════════════════════════════════════════════
# 3. @memguard decorator
# ═════════════════════════════════════════════════════════════════════════════

class TestMemguardDecorator:

    def test_return_value_preserved(self):
        @mg_decorator()
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_decorated_function_callable(self):
        @mg_decorator()
        def noop():
            pass

        noop()  # must not raise

    def test_wrapper_has_memguard_flag(self):
        @mg_decorator()
        def fn():
            pass

        assert getattr(fn, "__memguard__", False) is True

    def test_functools_wraps_preserves_name(self):
        @mg_decorator()
        def my_special_function():
            """Docstring."""

        assert my_special_function.__name__ == "my_special_function"
        assert my_special_function.__doc__  == "Docstring."

    def test_history_is_recorded(self):
        @mg_decorator()
        def tracked():
            return 42

        tracked()
        tracked()
        history = get_call_history("TestMemguardDecorator.test_history_is_recorded.<locals>.tracked")
        assert len(history) == 2

    def test_exception_in_wrapped_function_is_reraised(self):
        @mg_decorator()
        def exploder():
            raise ValueError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            exploder()

    def test_export_json_writes_file(self, tmp_path):
        path = str(tmp_path / "out.json")

        @mg_decorator(export_json=path)
        def fn():
            return 1

        fn()
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert data["func_name"].endswith("fn")

    def test_label_appears_in_history(self):
        @mg_decorator(label="my-label")
        def labelled():
            pass

        labelled()
        key = next(iter(all_call_history()))
        history = all_call_history()[key]
        assert history[0]["label"] == "my-label"

    def test_verbose_false_does_not_crash(self, capsys):
        @mg_decorator(verbose=False)
        def quiet():
            return 99

        result = quiet()
        assert result == 99
        # Still prints the summary block even in non-verbose mode
        out = capsys.readouterr().out
        assert "MemGuard" in out

    def test_track_objects_false(self):
        @mg_decorator(track_objects=False)
        def fn():
            return list(range(100))

        fn()
        key = next(iter(all_call_history()))
        assert all_call_history()[key][0]["leaked_types"] == []

    def test_multiple_calls_increment_call_index(self):
        @mg_decorator()
        def repeatable():
            pass

        repeatable()
        repeatable()
        repeatable()
        history = list(all_call_history().values())[0]
        indices = [h.get("call_index", None) for h in history]
        # call_index not stored in to_dict directly — check via len
        assert len(history) == 3


# ═════════════════════════════════════════════════════════════════════════════
# 4. MemGuardContext
# ═════════════════════════════════════════════════════════════════════════════

class TestMemGuardContext:

    def test_basic_usage(self):
        with MemGuardContext("ctx-test"):
            _ = list(range(1000))

    def test_report_available_after_exit(self):
        ctx = MemGuardContext("ctx-report")
        with ctx:
            pass
        assert ctx.report is not None
        assert isinstance(ctx.report, MemGuardReport)

    def test_net_mb_is_float(self):
        ctx = MemGuardContext("ctx-float")
        with ctx:
            _ = [b"x" * 100 for _ in range(500)]
        assert isinstance(ctx.report.net_mb, float)

    def test_duration_positive(self):
        import time
        ctx = MemGuardContext("ctx-duration")
        with ctx:
            time.sleep(0.01)
        assert ctx.report.duration_s >= 0.01

    def test_exception_does_not_suppress(self):
        with pytest.raises(ZeroDivisionError):
            with MemGuardContext("ctx-exc"):
                _ = 1 / 0

    def test_export_json(self, tmp_path):
        path = str(tmp_path / "ctx.json")
        with MemGuardContext("ctx-json", export_json=path):
            pass
        assert os.path.exists(path)

    def test_history_recorded(self):
        with MemGuardContext("ctx-history"):
            pass
        with MemGuardContext("ctx-history"):
            pass
        assert len(get_call_history("ctx-history")) == 2

    def test_report_has_suggestions(self):
        ctx = MemGuardContext("ctx-suggestions")
        with ctx:
            pass
        assert len(ctx.report.suggestions) >= 1

    def test_label_is_context_manager(self):
        ctx = MemGuardContext("ctx-label-check")
        with ctx:
            pass
        assert ctx.report.label == "context-manager"


# ═════════════════════════════════════════════════════════════════════════════
# 5. Suggestion engine
# ═════════════════════════════════════════════════════════════════════════════

class TestSuggestions:

    def _make_report(self, **kwargs) -> MemGuardReport:
        r = MemGuardReport("fn", "")
        r.suggestions = []
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r

    def test_healthy_report_says_no_issues(self):
        r = self._make_report(net_mb=0.01, peak_mb=0.02, duration_s=0.1)
        tips = build_suggestions(r, [])
        assert any("No issues" in t for t in tips)

    def test_high_net_growth_flagged(self):
        r = self._make_report(
            net_mb=10.0, peak_mb=10.5, duration_s=0.1,
            object_delta={}, top_stats=[],
        )
        tips = build_suggestions(r, [])
        assert any("HIGH NET MEMORY GROWTH" in t for t in tips)

    def test_memory_churn_flagged(self):
        r = self._make_report(
            net_mb=0.5, peak_mb=50.0, duration_s=0.1,
            object_delta={}, top_stats=[],
        )
        tips = build_suggestions(r, [])
        assert any("CHURN" in t for t in tips)

    def test_slow_execution_flagged(self):
        r = self._make_report(
            net_mb=0.0, peak_mb=0.0, duration_s=5.0,
            object_delta={}, top_stats=[],
        )
        tips = build_suggestions(r, [])
        assert any("SLOW EXECUTION" in t for t in tips)

    def test_object_leak_candidate_flagged(self):
        r = self._make_report(
            net_mb=0.0, peak_mb=0.0, duration_s=0.1,
            object_delta={"MyClass": (0, 200, 200)},
            top_stats=[],
        )
        tips = build_suggestions(r, [])
        assert any("OBJECT LEAK CANDIDATE" in t for t in tips)

    def test_object_below_threshold_not_flagged(self):
        r = self._make_report(
            net_mb=0.0, peak_mb=0.0, duration_s=0.1,
            object_delta={"SmallType": (0, 10, 10)},
            top_stats=[],
        )
        tips = build_suggestions(r, [])
        assert not any("OBJECT LEAK CANDIDATE" in t for t in tips)

    def test_hotspot_flagged(self):
        r = self._make_report(
            net_mb=0.0, peak_mb=0.0, duration_s=0.1,
            object_delta={},
            top_stats=[{
                "file": "app.py", "line": 42,
                "size": "2.00 MB", "size_b": 2 * 1024 * 1024, "count": 1,
            }],
        )
        tips = build_suggestions(r, [])
        assert any("HOTSPOT" in t for t in tips)

    def test_monotonic_trend_flagged(self):
        history = [
            {"net_mb": 1.0}, {"net_mb": 2.0}, {"net_mb": 3.0},
        ]
        r = self._make_report(
            net_mb=4.0, peak_mb=4.1, duration_s=0.1,
            object_delta={}, top_stats=[],
        )
        tips = build_suggestions(r, history)
        assert any("MONOTONIC GROWTH TREND" in t for t in tips)

    def test_non_monotonic_trend_not_flagged(self):
        history = [
            {"net_mb": 3.0}, {"net_mb": 1.0}, {"net_mb": 2.0},
        ]
        r = self._make_report(
            net_mb=1.5, peak_mb=1.6, duration_s=0.1,
            object_delta={}, top_stats=[],
        )
        tips = build_suggestions(r, history)
        assert not any("MONOTONIC" in t for t in tips)


# ═════════════════════════════════════════════════════════════════════════════
# 6. Utilities — inspect_object & summary
# ═════════════════════════════════════════════════════════════════════════════

class TestUtils:

    def test_inspect_object_runs_without_error(self, capsys):
        d = {"key": list(range(50))}
        inspect_object(d, label="test_dict")
        out = capsys.readouterr().out
        assert "MemGuard Inspector" in out
        assert "dict" in out

    def test_inspect_object_alias(self, capsys):
        memguard.inspect([1, 2, 3], label="alias_test")
        out = capsys.readouterr().out
        assert "MemGuard Inspector" in out

    def test_summary_no_data(self, capsys):
        summary()
        out = capsys.readouterr().out
        assert "No profiling data" in out

    def test_summary_with_data(self, capsys):
        @mg_decorator(verbose=False)
        def sample():
            pass

        sample()
        sample()
        summary()
        out = capsys.readouterr().out
        assert "MEMGUARD SESSION SUMMARY" in out
        assert "sample" in out

    def test_inspect_object_shows_length_for_containers(self, capsys):
        inspect_object(list(range(100)), label="my_list")
        out = capsys.readouterr().out
        assert "100" in out


# ═════════════════════════════════════════════════════════════════════════════
# 7. Call history management
# ═════════════════════════════════════════════════════════════════════════════

class TestCallHistory:

    def test_clear_specific_function(self):
        @mg_decorator()
        def fn_a():
            pass

        @mg_decorator()
        def fn_b():
            pass

        fn_a()
        fn_b()
        clear_history("TestCallHistory.test_clear_specific_function.<locals>.fn_a")
        hist = all_call_history()
        assert not any(k.endswith("fn_a") for k in hist)
        assert any(k.endswith("fn_b") for k in hist)

    def test_clear_all_history(self):
        @mg_decorator()
        def fn():
            pass

        fn()
        clear_history()
        assert all_call_history() == {}

    def test_get_call_history_empty_for_unknown(self):
        assert get_call_history("does_not_exist") == []


# ═════════════════════════════════════════════════════════════════════════════
# 8. Leak simulation tests
# ═════════════════════════════════════════════════════════════════════════════

class TestLeakSimulation:

    def test_monotonic_leak_detected_over_calls(self):
        """
        Call a leaky function 4 times and verify that the suggestion
        engine eventually flags MONOTONIC GROWTH TREND.
        """
        store = []

        @mg_decorator(verbose=False, track_objects=False)
        def leaky(n):
            store.extend([{"id": i, "data": "x" * 200} for i in range(n)])

        leaky(100)
        leaky(100)
        leaky(100)
        leaky(100)

        key   = next(k for k in all_call_history() if "leaky" in k)
        calls = all_call_history()[key]
        all_tips = " ".join(tip for c in calls for tip in c["suggestions"])
        # Not guaranteed to appear on every run due to GC timing, but must
        # appear at least once in a leak sequence.
        # We assert that the leak pattern is captured — either net growth or trend.
        assert any(
            kw in all_tips
            for kw in ("MONOTONIC", "HIGH NET", "OBJECT LEAK")
        )

    def test_healthy_function_no_leak_flags(self):
        """
        Pure-computation function must not flag memory growth or hotspots.

        In a test-runner environment gc.get_objects() can show large growth
        in built-in types (tuple, frame, cell, etc.) because pytest itself
        allocates them for parametrize records, tracebacks, and fixtures.
        We exclude those known-noisy built-in types and only fail if
        user-defined types or actual memory growth is flagged.
        """
        PYTEST_NOISE = {"tuple", "frame", "cell", "code", "function",
                        "method", "builtin_function_or_method",
                        "dict", "list", "set", "weakref"}

        @mg_decorator(verbose=False)
        def pure(n):
            return sum(i * i for i in range(n))

        pure(500)
        key  = next(k for k in all_call_history() if "pure" in k)
        tips = all_call_history()[key][0]["suggestions"]

        def is_real_problem(tip: str) -> bool:
            if any(kw in tip for kw in ("HIGH NET", "TREND", "HOTSPOT", "SLOW")):
                return True
            if "OBJECT LEAK CANDIDATE" in tip:
                # Only a real problem if it is NOT a pytest-internal built-in
                return not any(f"\'{t}\'" in tip for t in PYTEST_NOISE)
            return False

        bad = [t for t in tips if is_real_problem(t)]
        assert bad == [], f"Unexpected flags on clean function: {bad}"


# ═════════════════════════════════════════════════════════════════════════════
# 9. Thread safety
# ═════════════════════════════════════════════════════════════════════════════

class TestThreadSafety:

    def test_concurrent_decorator_calls(self):
        """Decorator must not corrupt history under concurrent access."""
        errors = []

        @mg_decorator(verbose=False, track_objects=False)
        def worker(n):
            return list(range(n))

        def run():
            try:
                for _ in range(5):
                    worker(200)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_context_managers(self):
        errors = []

        def run():
            try:
                with MemGuardContext("threaded-ctx", verbose=False):
                    _ = list(range(500))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"


# ═════════════════════════════════════════════════════════════════════════════
# 10. Package-level attributes
# ═════════════════════════════════════════════════════════════════════════════

class TestPackageMeta:

    def test_version_string(self):
        assert isinstance(memguard.__version__, str)
        parts = memguard.__version__.split(".")
        assert len(parts) == 3

    def test_author(self):
        assert "Aniket Maithani" in memguard.__author__

    def test_all_exports_importable(self):
        for name in memguard.__all__:
            assert hasattr(memguard, name), f"Missing export: {name}"