"""Tests for the pure planning logic and the on-disk applier."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow running the tests straight from a checkout, without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renamer.core import (  # noqa: E402
    RenameRule,
    apply_plan,
    format_plan,
    iter_files,
    plan_renames,
    sanitize,
)


def make_files(tmp_path: Path, *names: str) -> list[Path]:
    for name in names:
        (tmp_path / name).write_text("x", encoding="utf-8")
    return sorted(tmp_path.iterdir())


# --- pure planning ---------------------------------------------------------


def test_replace_text(tmp_path):
    files = make_files(tmp_path, "IMG_001.png", "IMG_002.png")
    plan = plan_renames(files, RenameRule(find="IMG", replace="photo"))
    assert [op.new_name for op in plan.changes] == ["photo_001.png", "photo_002.png"]
    assert plan.is_safe


def test_prefix_and_suffix(tmp_path):
    files = make_files(tmp_path, "a.txt")
    plan = plan_renames(files, RenameRule(prefix="2026_", suffix="_final"))
    assert plan.changes[0].new_name == "2026_a_final.txt"


def test_regex_mode(tmp_path):
    files = make_files(tmp_path, "shot-042.jpg")
    plan = plan_renames(
        files, RenameRule(find=r"\d+", replace="N", use_regex=True)
    )
    assert plan.changes[0].new_name == "shot-N.jpg"


def test_case_conversion(tmp_path):
    files = make_files(tmp_path, "MixedCase.TXT")
    lower = plan_renames(files, RenameRule(lowercase=True))
    assert lower.changes[0].new_name == "mixedcase.TXT"


def test_numbering_with_padding(tmp_path):
    files = make_files(tmp_path, "b.txt", "a.txt", "c.txt")
    plan = plan_renames(files, RenameRule(number_padding=3), number=True)
    # files are sorted by name first, so numbering is deterministic
    assert [op.new_name for op in plan.changes] == [
        "a_001.txt",
        "b_002.txt",
        "c_003.txt",
    ]


def test_start_index(tmp_path):
    files = make_files(tmp_path, "a.txt")
    plan = plan_renames(files, RenameRule(start_index=10), number=True)
    assert plan.changes[0].new_name == "a_10.txt"


def test_suffix_is_preserved(tmp_path):
    files = make_files(tmp_path, "photo.tar.gz")
    plan = plan_renames(files, RenameRule(prefix="old_"))
    assert plan.changes[0].new_name == "old_photo.tar.gz"


def test_unchanged_files_are_not_changes(tmp_path):
    files = make_files(tmp_path, "same.txt")
    plan = plan_renames(files, RenameRule(find="nothing", replace="x"))
    assert plan.changes == ()
    assert plan.is_safe


# --- safety checks ---------------------------------------------------------


def test_illegal_characters_are_reported_and_sanitized(tmp_path):
    files = make_files(tmp_path, "a.txt")
    plan = plan_renames(files, RenameRule(prefix="bad:name"))
    assert not plan.is_safe
    assert plan.changes[0].new_name == "bad_namea.txt"


def test_duplicate_targets_are_detected(tmp_path):
    # A regex that consumes the whole stem maps both files onto the same name.
    # (.+ rather than .* : .* also matches the empty string at the end, which
    # would make re.sub insert the replacement twice.)
    files = make_files(tmp_path, "draft.txt", "final.txt")
    plan = plan_renames(
        files, RenameRule(find=r".+", replace="same", use_regex=True)
    )
    assert [op.new_name for op in plan.changes] == ["same.txt", "same.txt"]
    assert not plan.is_safe
    assert any("duplicate" in p.reason for p in plan.problems)


def test_empty_result_is_reported(tmp_path):
    files = make_files(tmp_path, "abc.txt")
    plan = plan_renames(files, RenameRule(find="abc", replace=""))
    # stem becomes empty -> flagged, even though sanitize() would produce "_"
    assert not plan.is_safe
    assert any("empty" in p.reason for p in plan.problems)


def test_reserved_windows_name_is_rejected(tmp_path):
    files = make_files(tmp_path, "x.txt")
    plan = plan_renames(files, RenameRule(find="x", replace="CON"))
    assert not plan.is_safe
    assert any("reserved" in p.reason for p in plan.problems)


def test_sanitize_strips_trailing_dots_and_spaces():
    assert sanitize("name...  ") == "name"
    assert sanitize("a<b>c") == "a_b_c"


# --- applying --------------------------------------------------------------


def test_apply_renames_on_disk(tmp_path):
    make_files(tmp_path, "IMG_1.png", "IMG_2.png")
    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(find="IMG", replace="pic"))
    apply_plan(plan)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["pic_1.png", "pic_2.png"]


def test_apply_refuses_unsafe_plan(tmp_path):
    make_files(tmp_path, "a.txt")
    # prefix ends with a colon -> illegal character -> plan is unsafe
    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(prefix="same:"))
    assert not plan.is_safe
    with pytest.raises(ValueError):
        apply_plan(plan)
    # nothing was touched
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_apply_handles_name_swaps(tmp_path):
    """A plan that swaps two names must not clobber either file."""
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")

    rule = RenameRule(find="a", replace="b")
    plan = plan_renames([tmp_path / "a.txt"], rule)
    # 'a.txt' -> 'b.txt' which already exists and is not part of the plan
    with pytest.raises(FileExistsError):
        apply_plan(plan)


def test_apply_reports_old_and_new_paths(tmp_path):
    make_files(tmp_path, "old.txt")
    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(prefix="new_"))
    done = apply_plan(plan)
    assert len(done) == 1
    old, new = done[0]
    assert old.name == "old.txt"
    assert new.name == "new_old.txt"


# --- iter_files and formatting --------------------------------------------


def test_iter_files_skips_directories(tmp_path):
    make_files(tmp_path, "a.txt")
    (tmp_path / "subdir").mkdir()
    names = [p.name for p in iter_files(tmp_path)]
    assert names == ["a.txt"]


def test_iter_files_pattern(tmp_path):
    make_files(tmp_path, "a.txt", "b.jpg")
    names = [p.name for p in iter_files(tmp_path, pattern="*.jpg")]
    assert names == ["b.jpg"]


def test_format_plan_mentions_problems(tmp_path):
    files = make_files(tmp_path, "a.txt")
    plan = plan_renames(files, RenameRule(prefix="bad:name"))
    text = format_plan(plan)
    assert "problems" in text
    # the raw (illegal) result is what the user needs to see...
    assert "bad:namea.txt" in text
    # ...along with the sanitized name we would have written
    assert "bad_namea.txt" in text


def test_format_plan_hides_preview_when_unsafe(tmp_path):
    """An unsafe plan must not print a rename listing: it would never be applied."""
    files = make_files(tmp_path, "a.txt")
    plan = plan_renames(files, RenameRule(prefix="bad:name"))
    text = format_plan(plan)
    assert "->" not in text
    assert "No changes will be made" in text
