"""Tests for the pure planning logic and the on-disk applier."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Allow running the tests straight from a checkout, without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renamer.core import (  # noqa: E402
    UNDO_LOG_NAME,
    RenameRule,
    UndoError,
    apply_plan,
    format_plan,
    iter_files,
    plan_renames,
    read_undo_log,
    revert_from_log,
    sanitize,
    undo_log_path,
)


def make_files(tmp_path: Path, *names: str) -> list[Path]:
    for name in names:
        (tmp_path / name).write_text("x", encoding="utf-8")
    return sorted(tmp_path.iterdir())


def make_ordered_files(tmp_path: Path, *specs: tuple[str, int, float]) -> list[Path]:
    """Create ``(name, size, mtime)`` files and return them in creation order.

    Sizes and mtimes are set explicitly (``os.utime``) instead of relying on how
    fast the test happens to run, so the ordering assertions are deterministic.
    """
    paths: list[Path] = []
    for name, size, mtime in specs:
        path = tmp_path / name
        path.write_bytes(b"x" * size)
        os.utime(path, (mtime, mtime))
        paths.append(path)
    return paths


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


# --- ordering --------------------------------------------------------------


def test_default_sort_is_still_name_order(tmp_path):
    """Without an explicit sort, files are processed by name, case-insensitive."""
    files = make_ordered_files(
        tmp_path,
        ("b.txt", 30, 300.0),
        ("A.txt", 10, 100.0),
        ("c.txt", 20, 200.0),
    )
    plan = plan_renames(files, RenameRule(number_padding=2), number=True)
    assert [op.source.name for op in plan.operations] == ["A.txt", "b.txt", "c.txt"]
    assert [op.new_name for op in plan.changes] == ["A_01.txt", "b_02.txt", "c_03.txt"]


def test_sort_mtime_is_newest_first(tmp_path):
    files = make_ordered_files(
        tmp_path,
        ("old.txt", 10, 1_000_000.0),
        ("newest.txt", 10, 3_000_000.0),
        ("middle.txt", 10, 2_000_000.0),
    )
    plan = plan_renames(
        files, RenameRule(number_padding=2), number=True, sort="mtime"
    )
    assert [op.source.name for op in plan.operations] == [
        "newest.txt",
        "middle.txt",
        "old.txt",
    ]
    assert [op.new_name for op in plan.changes] == [
        "newest_01.txt",
        "middle_02.txt",
        "old_03.txt",
    ]


def test_sort_size_is_biggest_first(tmp_path):
    files = make_ordered_files(
        tmp_path,
        ("small.txt", 1, 1_000.0),
        ("big.txt", 300, 2_000.0),
        ("medium.txt", 50, 3_000.0),
    )
    plan = plan_renames(files, RenameRule(number_padding=2), number=True, sort="size")
    assert [op.source.name for op in plan.operations] == [
        "big.txt",
        "medium.txt",
        "small.txt",
    ]
    assert [op.new_name for op in plan.changes] == [
        "big_01.txt",
        "medium_02.txt",
        "small_03.txt",
    ]


def test_sort_none_preserves_input_order(tmp_path):
    files = make_ordered_files(
        tmp_path,
        ("c.txt", 10, 1_000.0),
        ("a.txt", 20, 2_000.0),
        ("b.txt", 30, 3_000.0),
    )
    # deliberately neither name order nor size/mtime order
    given = [files[2], files[0], files[1]]
    plan = plan_renames(given, RenameRule(number_padding=2), number=True, sort="none")
    assert [op.source.name for op in plan.operations] == ["b.txt", "c.txt", "a.txt"]
    assert [op.new_name for op in plan.changes] == ["b_01.txt", "c_02.txt", "a_03.txt"]


def test_bool_sort_argument_is_still_supported(tmp_path):
    """The old bool switch keeps its meaning: True = by name, False = as given."""
    files = make_ordered_files(
        tmp_path,
        ("b.txt", 10, 1_000.0),
        ("a.txt", 20, 2_000.0),
    )
    legacy_true = plan_renames(files, RenameRule(), sort=True)
    legacy_false = plan_renames(files, RenameRule(), sort=False)
    assert [op.source.name for op in legacy_true.operations] == ["a.txt", "b.txt"]
    assert [op.source.name for op in legacy_false.operations] == ["b.txt", "a.txt"]

    # ...and agree with the string spelling they now map onto
    by_name = plan_renames(files, RenameRule(), sort="name")
    by_none = plan_renames(files, RenameRule(), sort="none")
    assert [op.source.name for op in by_name.operations] == ["a.txt", "b.txt"]
    assert [op.source.name for op in by_none.operations] == ["b.txt", "a.txt"]


def test_files_that_cannot_be_statted_sort_last(tmp_path):
    """A missing file has no mtime/size: it must not crash the planner."""
    files = make_ordered_files(
        tmp_path,
        ("old.txt", 10, 1_000.0),
        ("big.txt", 900, 500.0),
    )
    missing = tmp_path / "gone.txt"  # never created -> stat() raises FileNotFoundError

    by_mtime = plan_renames([*files, missing], RenameRule(), sort="mtime")
    assert [op.source.name for op in by_mtime.operations] == [
        "old.txt",
        "big.txt",
        "gone.txt",
    ]

    by_size = plan_renames([*files, missing], RenameRule(), sort="size")
    assert [op.source.name for op in by_size.operations] == [
        "big.txt",
        "old.txt",
        "gone.txt",
    ]


def test_unknown_sort_mode_is_rejected(tmp_path):
    files = make_ordered_files(tmp_path, ("a.txt", 1, 1_000.0))
    with pytest.raises(ValueError):
        plan_renames(files, RenameRule(), sort="newest")


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

    # apply_plan also writes the undo log into the directory; ignore it here.
    names = sorted(p.name for p in tmp_path.iterdir() if p.name != UNDO_LOG_NAME)
    assert names == ["pic_1.png", "pic_2.png"]


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


# --- undo ------------------------------------------------------------------


def test_apply_then_undo_restores_original_names(tmp_path):
    make_files(tmp_path, "IMG_1.png", "IMG_2.png")
    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(find="IMG", replace="pic"))
    apply_plan(plan)

    assert sorted(p.name for p in tmp_path.iterdir() if p.name != UNDO_LOG_NAME) == [
        "pic_1.png",
        "pic_2.png",
    ]

    restored = revert_from_log(tmp_path)
    assert len(restored) == 2
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["IMG_1.png", "IMG_2.png"]
    # the log is popped, and an empty stack leaves no file behind
    assert not undo_log_path(tmp_path).exists()


def test_apply_writes_an_undo_entry(tmp_path):
    make_files(tmp_path, "a.txt")
    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(prefix="new_"))
    apply_plan(plan)

    entries = read_undo_log(tmp_path)
    assert len(entries) == 1
    steps = entries[-1].steps
    assert len(steps) == 1
    assert steps[0].original == "a.txt"
    assert steps[0].final == "new_a.txt"


def test_undo_without_a_log_errors_cleanly(tmp_path):
    make_files(tmp_path, "a.txt")
    with pytest.raises(UndoError):
        revert_from_log(tmp_path)
    # nothing changed
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_undo_refuses_when_a_renamed_file_is_missing(tmp_path):
    make_files(tmp_path, "a.txt")
    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(prefix="new_"))
    apply_plan(plan)

    (tmp_path / "new_a.txt").unlink()

    with pytest.raises(UndoError):
        revert_from_log(tmp_path)
    assert not (tmp_path / "a.txt").exists()


def test_undo_refuses_when_the_original_name_is_taken(tmp_path):
    make_files(tmp_path, "a.txt")
    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(prefix="new_"))
    apply_plan(plan)

    # something else now occupies the name we would restore
    (tmp_path / "a.txt").write_text("intruder", encoding="utf-8")

    with pytest.raises(UndoError):
        revert_from_log(tmp_path)
    assert (tmp_path / "new_a.txt").exists()
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "intruder"


def test_undo_unwinds_two_batches_in_reverse_order(tmp_path):
    make_files(tmp_path, "a.txt")

    first = plan_renames(list(tmp_path.iterdir()), RenameRule(prefix="one_"))
    apply_plan(first)
    # Exclude the undo log: apply_plan just wrote it, and renaming it would make
    # the second batch (and therefore the undo stack) meaningless.
    remaining = [p for p in tmp_path.iterdir() if p.name != UNDO_LOG_NAME]
    second = plan_renames(remaining, RenameRule(prefix="two_"))
    apply_plan(second)

    assert sorted(p.name for p in tmp_path.iterdir() if p.name != UNDO_LOG_NAME) == [
        "two_one_a.txt"
    ]

    revert_from_log(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != UNDO_LOG_NAME) == [
        "one_a.txt"
    ]
    revert_from_log(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]
    assert not undo_log_path(tmp_path).exists()


def test_undo_log_is_not_treated_as_a_file_to_rename(tmp_path):
    make_files(tmp_path, "a.txt")
    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(prefix="new_"))
    apply_plan(plan)

    # iter_files must skip the bookkeeping file, otherwise a second run would
    # try to rename the undo log itself.
    names = [p.name for p in iter_files(tmp_path)]
    assert names == ["new_a.txt"]


def test_apply_rejects_a_corrupt_undo_log_before_renaming(tmp_path):
    make_files(tmp_path, "a.txt")
    undo_log_path(tmp_path).write_text("{not json", encoding="utf-8")

    plan = plan_renames(list(tmp_path.iterdir()), RenameRule(prefix="new_"))
    with pytest.raises(UndoError):
        apply_plan(plan)
    # a corrupt log must not cost the user the batch: nothing was renamed
    assert (tmp_path / "a.txt").exists()
    assert not (tmp_path / "new_a.txt").exists()
