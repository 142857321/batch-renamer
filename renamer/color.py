"""Minimal ANSI colour support, standard library only.

Two independent concerns live here:

* :func:`color_enabled` decides *whether* the CLI may colourise, following the
  usual terminal conventions (``--color``, ``NO_COLOR``, ``TERM=dumb``, TTY).
* :class:`Palette` *applies* the codes. It degrades to plain text when disabled,
  so callers never need to branch on colour themselves.

Everything is opt-in: the default :class:`Palette` performs no string changes at
all, which keeps the pure planning output byte-for-byte identical to before.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Mapping, TextIO

# SGR (Select Graphic Rendition) sequences. Keep them simple and widely
# supported: no 256-colour or truecolor codes, so basic terminals work too.
RESET = "\x1b[0m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"

# Matches the escape sequences this module emits (and any other SGR sequence).
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Valid values for the CLI --color option.
COLOR_MODES = ("auto", "always", "never")


def strip_ansi(text: str) -> str:
    """Return ``text`` with every ANSI SGR escape sequence removed."""
    return ANSI_RE.sub("", text)


def color_enabled(
    mode: str = "auto",
    *,
    stream: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Decide whether colour should be used for ``mode``.

    Args:
        mode: ``"auto"``, ``"always"`` or ``"never"``.
        stream: Stream the output goes to; defaults to ``sys.stdout``. Resolved
            at call time (not at import time) so redirecting stdout works.
        environ: Environment mapping; defaults to ``os.environ``.

    Returns:
        True if the caller should emit colour.

    ``"always"`` wins over every heuristic (handy for ``... | less -R``).
    ``"never"`` disables colour unconditionally. ``"auto"`` colours only when
    the stream is a real terminal, ``NO_COLOR`` is absent and ``TERM`` is not
    ``dumb``.

    Note: the ``NO_COLOR`` convention is applied on *presence*, so even an empty
    value disables colour under ``"auto"``.
    """
    if mode == "never":
        return False
    if mode == "always":
        return True
    if mode != "auto":
        raise ValueError(f"unknown colour mode: {mode!r}")

    env = os.environ if environ is None else environ
    if "NO_COLOR" in env:
        return False
    if env.get("TERM", "").lower() == "dumb":
        return False

    target = sys.stdout if stream is None else stream
    isatty = getattr(target, "isatty", None)
    return bool(isatty()) if callable(isatty) else False


@dataclass(frozen=True)
class Palette:
    """Wraps text in SGR codes, or returns it untouched when disabled.

    Every coloured span ends with :data:`RESET`, so a caller can never leak a
    colour into output that follows - including when the text spans newlines.
    """

    enabled: bool = False

    def paint(self, text: str, code: str) -> str:
        """Colour ``text`` with ``code``; no-op when the palette is disabled."""
        if not self.enabled or not text:
            return text
        return f"{code}{text}{RESET}"

    def dim(self, text: str) -> str:
        """Dim text, used for the *old* name (present but de-emphasised)."""
        return self.paint(text, DIM)

    def green(self, text: str) -> str:
        """Green text, used for the arrow and the *new* name."""
        return self.paint(text, GREEN)

    def red(self, text: str) -> str:
        """Red text, used for problem lines."""
        return self.paint(text, RED)

    def yellow(self, text: str) -> str:
        """Yellow text, used for the informational notices."""
        return self.paint(text, YELLOW)
