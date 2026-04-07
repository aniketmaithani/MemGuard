"""
memguard._colors
~~~~~~~~~~~~~~~~
ANSI terminal color helpers. Zero external dependencies.
Falls back to plain text when stdout is not a TTY or FORCE_COLOR=0.
"""

import os
import sys

_USE_COLOR = sys.stdout.isatty() or os.environ.get("FORCE_COLOR", "0") == "1"


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def RED(t: str) -> str:     return _c(t, "31")
def GREEN(t: str) -> str:   return _c(t, "32")
def YELLOW(t: str) -> str:  return _c(t, "33")
def CYAN(t: str) -> str:    return _c(t, "36")
def MAGENTA(t: str) -> str: return _c(t, "35")
def BOLD(t: str) -> str:    return _c(t, "1")
def DIM(t: str) -> str:     return _c(t, "2")