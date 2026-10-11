"""Tests for the optional ANSI colour support.

The colour code itself is pure string handling, so most tests here build plans
from made-up paths and never touch the disk. Only the two end-to-end CLI tests
need real files, and they use the project-local ``tmp_path`` fixture.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path

import pytest

# Allow running the tests straight from a checkout, without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renamer.cli import build_parser, main  # noqa: E402
from renamer.color import (  # noqa: E402
    ANSI_RE,
    GREEN,
    RED,
    RESET,
    YELLOW,
    Palette,
    color_enabled,
    strip_ansi,
)
from renamer.core import (  # noqa: E402
    Problem,
    RenameOperation,
    RenamePlan,
    format_plan,
)


# --- helpers ---------------------------------------------------------------


def make_plan(*names: str) -> RenamePlan:
    """A safe plan that renames ``name`` to ``new_<name>``, without any disk IO."""
    root = Path("/photos")
    operations = tuple(
        RenameOperation(source=root / name, new_name=f"new_{name}") for name in names
    )
    return RenamePlan(operations=operations, problems=())


def make_problem_plan() -> RenamePlan:
    """A plan with one problem, which format_plan renders as an error listing."""
    operation = RenameOperation(source=Path("/photos/a.txt"), new_name="bad_namea.txt")
    problem = Problem(
        new_name="bad:namea.txt",
        reason="name contains illegal characters (would become 'bad_namea.txt')",
    )
    return RenamePlan(operations=(operation,), problems=(problem,))


class FakeTty:
    """Stand-in for stdout; only isatty() matters to the chooser."""

    def __init__(self, is_a_tty: bool) -> None:
        self._is_a_tty = is_a_tty

    def isatty(self) -> bool:
        return self._is_a_tty


class TextTty(io.StringIO):
    """A capturable stream that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def codes_in(text: str) -> list[str]:
    return ANSI_RE.findall(text)


# --- format_plan: plain by default -----------------------------------------


def test_default_output_has_no_escape_sequences():
    for plan in (make_plan("a.txt", "b.txt"), make_problem_plan(), make_plan()):
        text = format_plan(plan)
        assert "\x1b" not in text
        assert codes_in(text) == []


def test_explicit_color_false_is_the_same_as_the_default():
    plan = make_plan("a.txt", "b.txt")
    assert format_plan(plan, color=False) == format_plan(plan)
    assert format_plan(plan, limit=1, color=False) == format_plan(plan, limit=1)


def test_plain_preview_text_is_unchanged_by_the_colour_feature():
    """The colour work must not alter the visible layout of the preview."""
    text = format_plan(make_plan("a.txt", "longer_name.txt"))
    assert text == "  a.txt            ->  new_a.txt\n  longer_name.txt  ->  new_longer_name.txt"


# --- format_plan: coloured --------------------------------------------------


def test_colored_output_contains_escape_sequences():
    text = format_plan(make_plan("a.txt", "b.txt"), color=True)
    assert "\x1b[" in text
    assert len(codes_in(text)) > 0


def test_stripping_colour_returns_the_plain_text():
    for plan in (make_plan("a.txt", "b.txt"), make_problem_plan(), make_plan()):
        assert strip_ansi(format_plan(plan, color=True)) == format_plan(plan)
        assert strip_ansi(format_plan(plan, limit=1, color=True)) == format_plan(
            plan, limit=1
        )


def test_old_name_is_dim_arrow_and_new_name_are_green():
    text = format_plan(make_plan("a.txt"), color=True)
    assert "\x1b[2ma.txt\x1b[0m" in text
    assert f"{GREEN}->{RESET}" in text
    assert f"{GREEN}new_a.txt{RESET}" in text


def test_padding_keeps_the_columns_aligned_when_colored():
    text = format_plan(make_plan("a.txt", "longer_name.txt"), color=True)
    plain_lines = strip_ansi(text).splitlines()
    assert plain_lines[0].index("->") == plain_lines[1].index("->")


def test_problem_lines_are_red():
    text = format_plan(make_problem_plan(), color=True)
    assert RED in text
    problem_line = text.splitlines()[2]  # line 0: notice, line 1: blank
    assert problem_line.startswith(RED)
    assert problem_line.endswith(RESET)
    assert "bad:namea.txt" in strip_ansi(problem_line)


def test_notices_are_yellow():
    unsafe = format_plan(make_problem_plan(), color=True)
    assert f"{YELLOW}No changes will be made - the plan has problems:{RESET}" in unsafe

    empty = format_plan(make_plan(), color=True)
    assert empty == f"{YELLOW}Nothing to rename.{RESET}"


def test_problems_are_still_not_rendered_as_a_preview():
    text = format_plan(make_problem_plan(), color=True)
    assert "->" not in strip_ansi(text)


def test_every_colour_code_is_reset_line_by_line():
    """Including multi-line output: no line may leak colour into the next."""
    for plan in (make_plan("a.txt", "b.txt", "c.txt"), make_problem_plan(), make_plan()):
        text = format_plan(plan, color=True)
        for line in text.splitlines():
            codes = codes_in(line)
            if "\x1b[" in line:
                assert line.endswith(RESET), repr(line)
            # every opening code is immediately closed by a reset
            for index, code in enumerate(codes):
                if code != RESET:
                    assert index + 1 < len(codes) and codes[index + 1] == RESET, repr(
                        line
                    )


def test_opening_and_closing_codes_are_balanced():
    for plan in (make_plan("a.txt", "b.txt"), make_problem_plan(), make_plan()):
        text = format_plan(plan, color=True)
        all_codes = codes_in(text)
        resets = [code for code in all_codes if code == RESET]
        opens = [code for code in all_codes if code != RESET]
        assert len(opens) == len(resets)
        assert resets, "coloured output must contain resets"


def test_palette_is_a_noop_when_disabled():
    palette = Palette(enabled=False)
    assert palette.dim("a") == "a"
    assert palette.green("a") == "a"
    assert palette.red("a") == "a"
    assert palette.yellow("a") == "a"

    enabled = Palette(enabled=True)
    assert enabled.red("a") == f"{RED}a{RESET}"
    assert enabled.red("") == ""  # nothing to colour, nothing to reset


def test_strip_ansi_removes_only_escapes():
    assert strip_ansi("\x1b[31mred\x1b[0m and plain") == "red and plain"
    assert strip_ansi("no codes here") == "no codes here"


# --- the chooser ------------------------------------------------------------


def test_never_disables_colour_even_on_a_terminal():
    assert color_enabled("never", stream=FakeTty(True), environ={}) is False
    assert color_enabled("never", stream=FakeTty(False), environ={}) is False


def test_always_forces_colour_even_off_a_terminal():
    assert color_enabled("always", stream=FakeTty(False), environ={}) is True
    # ...and --color always wins over the NO_COLOR convention
    assert (
        color_enabled("always", stream=FakeTty(False), environ={"NO_COLOR": "1"})
        is True
    )
    assert (
        color_enabled("always", stream=FakeTty(False), environ={"TERM": "dumb"})
        is True
    )


def test_auto_colours_only_on_a_real_terminal():
    assert color_enabled("auto", stream=FakeTty(True), environ={}) is True
    assert color_enabled("auto", stream=FakeTty(False), environ={}) is False
    # a stream without isatty() (a file, a pipe wrapper) counts as not a terminal
    assert color_enabled("auto", stream=io.StringIO(), environ={}) is False


def test_auto_respects_no_color():
    assert color_enabled("auto", stream=FakeTty(True), environ={"NO_COLOR": "1"}) is False
    # the convention is applied on presence, so an empty value counts as set
    assert color_enabled("auto", stream=FakeTty(True), environ={"NO_COLOR": ""}) is False


def test_auto_respects_a_dumb_terminal():
    assert color_enabled("auto", stream=FakeTty(True), environ={"TERM": "dumb"}) is False
    assert color_enabled("auto", stream=FakeTty(True), environ={"TERM": "xterm-256color"}) is True


def test_auto_defaults_to_the_real_stdout():
    """With no stream given, the decision follows sys.stdout (here: captured)."""
    assert color_enabled("auto", environ={}) == sys.stdout.isatty()


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        color_enabled("sometimes", stream=FakeTty(True), environ={})


# --- the CLI wiring ---------------------------------------------------------


def test_cli_color_option_defaults_to_auto():
    args = build_parser().parse_args(["some-dir"])
    assert args.color == "auto"


def test_cli_color_option_accepts_the_three_modes():
    for mode in ("auto", "always", "never"):
        assert build_parser().parse_args(["some-dir", "--color", mode]).color == mode

    with pytest.raises(SystemExit):
        build_parser().parse_args(["some-dir", "--color", "rainbow"])


def _run_cli(argv: list[str]) -> str:
    out = TextTty()
    err = io.StringIO()
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        main(argv)
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
    return out.getvalue()


@contextlib.contextmanager
def no_color_unset():
    """Temporarily remove NO_COLOR so an 'auto' assertion is meaningful.

    NO_COLOR is an ambient, machine-wide convention (and some CI images and
    sandboxes set it). Without this, the assertion below would pass or fail
    depending on the developer's environment rather than on our logic.
    """
    previous = os.environ.pop("NO_COLOR", None)
    try:
        yield
    finally:
        if previous is not None:
            os.environ["NO_COLOR"] = previous


def test_cli_color_always_on_a_pipe_emits_escapes(tmp_path):
    (tmp_path / "IMG_1.png").write_text("x", encoding="utf-8")

    forced = _run_cli([str(tmp_path), "--find", "IMG", "--replace", "pic", "--color", "always"])
    assert "->" in strip_ansi(forced)
    assert "\x1b[" in forced

    # A terminal (TextTty) plus the default 'auto' also colours, as long as
    # NO_COLOR is not in force. Fresh files are needed because a preview with
    # nothing to change prints no filename listing at all.
    (tmp_path / "IMG_2.png").write_text("x", encoding="utf-8")
    with no_color_unset():
        auto = _run_cli([str(tmp_path), "--find", "IMG", "--replace", "pic"])
    assert "\x1b[" in auto


def test_cli_color_never_and_no_color_emit_no_escapes(tmp_path):
    (tmp_path / "IMG_1.png").write_text("x", encoding="utf-8")

    never = _run_cli([str(tmp_path), "--find", "IMG", "--replace", "pic", "--color", "never"])
    assert "\x1b" not in never
    assert "->" in never

    # NO_COLOR turns the default 'auto' off, even on a terminal.
    (tmp_path / "IMG_2.png").write_text("x", encoding="utf-8")
    os.environ["NO_COLOR"] = "1"
    try:
        guarded = _run_cli([str(tmp_path), "--find", "IMG", "--replace", "pic"])
    finally:
        os.environ.pop("NO_COLOR", None)
    assert "\x1b" not in guarded

    # ...and with NO_COLOR gone, the same command on a terminal does colour.
    # This guards against the assertion above passing for the wrong reason.
    (tmp_path / "IMG_3.png").write_text("x", encoding="utf-8")
    with no_color_unset():
        unguarded = _run_cli([str(tmp_path), "--find", "IMG", "--replace", "pic"])
    assert "\x1b[" in unguarded
