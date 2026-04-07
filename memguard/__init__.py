"""
memguard
~~~~~~~~
Zero-dependency Python memory leak detector and analyser.

Public API
----------
``@memguard()``
    Decorator that wraps a function with full memory profiling.

``MemGuardContext``
    Context manager for profiling arbitrary code blocks.

``inspect`` / ``inspect_object``
    Inspect a single live object for size and retention.

``summary``
    Print a cross-function summary of all profiled calls.

``clear_history``
    Clear the in-process call history (useful in tests).

Author : Aniket Maithani
License: MIT
"""

from .decorator import memguard
from .context   import MemGuardContext
from .utils     import inspect_object, summary
from ._profiler import clear_call_history as clear_history
from ._report   import MemGuardReport

# Convenience alias
inspect = inspect_object

__all__ = [
    "memguard",
    "MemGuardContext",
    "MemGuardReport",
    "inspect_object",
    "inspect",
    "summary",
    "clear_history",
]

__version__ = "1.0.0"
__author__  = "Aniket Maithani"