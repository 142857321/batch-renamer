"""Core logic for batch-renamer: compute new names safely, without touching disk.

The design goal is that rename planning is a *pure function*: it takes a list of
names plus rules and returns a plan. Nothing is written until the caller applies
the plan, which makes preview mode trivial and keeps the tricky parts testable.

Applying a plan also records what happened in a hidden JSON undo log, so the same
batch can be reversed later with :func:`revert_from_log`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Sequence

from renamer.color import Palette

# Characters Windows forbids in file names, plus control characters.
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Hidden JSON file that records every applied batch, stored inside the directory
# that was renamed. Module level so callers and tests can find it.
UNDO_LOG_NAME = ".renamer-undo.json"

# Bumped when the on-disk log layout changes in an incompatible way.
UNDO_LOG_VERSION = 1

# Accepted values for plan_renames(sort=...).
#   "name"  - by file name, case-insensitive (the long-standing default)
#   "mtime" - newest first
#   "size"  - biggest first
#   "none"  - keep the caller's input order
SortMode = Literal["name", "mtime", "size", "none"]
SORT_MODES: tuple[SortMode, ...] = ("name", "mtime", "size", "none")

# Reserved device names on Windows (case-insensitive, with or without extension).
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class RenameRule:
    """A description of how to rewrite a single name (stem + suffix)."""

    find: str = ""
    replace: str = ""
    prefix: str = ""
    suffix: str = ""
    use_regex: bool = False
    lowercase: bool = False
    uppercase: bool = False
    start_index: int = 1
    number_padding: int = 0

    def apply(self, stem: str) -> str:
        """Return the new stem after applying every configured transformation."""
        result = stem

        if self.find:
            if self.use_regex:
                result = re.sub(self.find, self.replace, result)
            else:
                result = result.replace(self.find, self.replace)

        if self.lowercase:
            result = result.lower()
        if self.uppercase:
            result = result.upper()

        return f"{self.prefix}{result}{self.suffix}"


@dataclass(frozen=True)
class RenameOperation:
    """One planned rename, from an existing path to a new file name."""

    source: Path
    new_name: str

    @property
    def target(self) -> Path:
        return self.source.with_name(self.new_name)

    @property
    def is_change(self) -> bool:
        return self.source.name != self.new_name


@dataclass(frozen=True)
class Problem:
    """A reason a plan cannot be applied as-is."""

    new_name: str
    reason: str


@dataclass(frozen=True)
class RenamePlan:
    """The full result of planning: what to do, and what is wrong with it."""

    operations: tuple[RenameOperation, ...]
    problems: tuple[Problem, ...]

    @property
    def changes(self) -> tuple[RenameOperation, ...]:
        """Only the operations that actually rename something."""
        return tuple(op for op in self.operations if op.is_change)

    @property
    def is_safe(self) -> bool:
        return not self.problems


def sanitize(name: str) -> str:
    """Strip characters that are illegal in Windows file names."""
    cleaned = INVALID_CHARS.sub("_", name).rstrip(" .")
    return cleaned or "_"


def is_reserved(stem: str) -> bool:
    """True if the name collides with a reserved Windows device name."""
    return stem.upper() in RESERVED_NAMES


def _numbered(stem: str, index: int, padding: int) -> str:
    if padding > 0:
        return f"{stem}_{index:0{padding}d}"
    return f"{stem}_{index}"


def _normalize_sort(sort: bool | SortMode) -> SortMode:
    """Translate the public ``sort`` argument into one of :data:`SORT_MODES`.

    ``bool`` is still accepted for backwards compatibility with the original
    signature, where a switch meant "sort by name" (``True``) or "keep the
    caller's order" (``False``).
    """
    if sort is True:
        return "name"
    if sort is False:
        return "none"
    if sort not in SORT_MODES:
        allowed = ", ".join(f"'{mode}'" for mode in SORT_MODES)
        raise ValueError(f"unknown sort mode: {sort!r} (expected one of {allowed})")
    return sort


def _ordered(items: list[Path], sort: SortMode) -> list[Path]:
    """Return ``items`` in the order requested by ``sort``.

    ``"mtime"`` and ``"size"`` sort *descending* (newest / biggest first); ties
    fall back to the name so that a plan is reproducible.
    """
    if sort == "none":
        return list(items)
    if sort == "name":
        return sorted(items, key=lambda path: path.name.lower())

    attribute = "st_mtime" if sort == "mtime" else "st_size"
    # rank: 0 = we know the value, 1 = unreadable. Compared ascending, so the
    # unreadable entries end up last, and -value makes the known ones descending.
    ranked: list[tuple[int, float, str, Path]] = []
    for item in items:
        try:
            value = float(getattr(item.stat(), attribute))
        except OSError:
            # The file vanished, is unreadable, or is a broken symlink. There is
            # no value to compare, so treat it as the smallest one (last place)
            # and let the caller's preview/report deal with it - the planner must
            # never crash on a file it cannot stat.
            ranked.append((1, 0.0, item.name.lower(), item))
        else:
            ranked.append((0, -value, item.name.lower(), item))

    ranked.sort(key=lambda entry: entry[:3])
    return [entry[3] for entry in ranked]


def plan_renames(
    paths: Iterable[Path],
    rule: RenameRule,
    *,
    number: bool = False,
    sort: bool | SortMode = "name",
) -> RenamePlan:
    """Build a :class:`RenamePlan` for ``paths`` under ``rule``.

    Nothing on disk is modified. Problems (empty names, illegal characters,
    reserved device names, duplicates) are collected instead of raised, so the
    user can inspect and fix them before committing.

    Args:
        paths: the files to rename, in whatever order the caller collected them.
        rule: the transformations to apply to each name.
        number: append a running index (the order below decides the numbering).
        sort: which order to process the files in. One of:

            * ``"name"`` (default) - by file name, case-insensitive.
            * ``"mtime"`` - by modification time, **newest first**.
            * ``"size"`` - by file size, **biggest first**.
            * ``"none"`` - keep the caller's input order.

            The two boolean values of the old signature are still accepted, and
            they keep their old meaning: ``True`` behaves like ``"name"`` and
            ``False`` like ``"none"`` (i.e. "do not sort"). Passing ``sort=False``
            therefore still preserves the input order instead of silently
            switching to name ordering.

            ``"mtime"`` and ``"size"`` are descending because that is what users
            almost always want for photos and downloads (newest/biggest first).
            Files whose metadata cannot be read fall to the end of either order.

    Raises:
        ValueError: if ``sort`` is neither a known mode nor a bool.
    """
    items = _ordered(list(paths), _normalize_sort(sort))

    operations: list[RenameOperation] = []
    problems: list[Problem] = []
    seen: dict[str, Path] = {}

    for offset, path in enumerate(items):
        stem = rule.apply(path.stem)
        if number:
            stem = _numbered(stem, rule.start_index + offset, rule.number_padding)

        if not stem:
            problems.append(Problem(path.name, "rule produced an empty name"))

        # Report illegal characters on the raw result, not the sanitized one.
        # Otherwise the preview would show a "repaired" name that we then refuse
        # to write, which is confusing.
        if INVALID_CHARS.search(stem):
            problems.append(
                Problem(
                    stem + path.suffix,
                    "name contains illegal characters "
                    f"(would become '{sanitize(stem) + path.suffix}')",
                )
            )

        new_name = sanitize(stem) + path.suffix
        if is_reserved(Path(new_name).stem):
            problems.append(Problem(new_name, f"'{new_name}' is a reserved Windows name"))
        if len(new_name) > 255:
            problems.append(Problem(new_name, f"name is too long ({len(new_name)} chars)"))

        key = new_name.lower()
        if key in seen:
            problems.append(
                Problem(new_name, f"duplicate: also produced by '{seen[key].name}'")
            )
        else:
            seen[key] = path

        operations.append(RenameOperation(source=path, new_name=new_name))

    return RenamePlan(operations=tuple(operations), problems=tuple(problems))


def iter_files(
    directory: Path,
    *,
    pattern: str = "*",
    recursive: bool = False,
) -> Iterator[Path]:
    """Yield matching files (never directories) under ``directory``.

    The undo log (and its temporary sibling while it is being written) is skipped:
    it is bookkeeping for :func:`revert_from_log`, not a file to rename.
    """
    globber = directory.rglob if recursive else directory.glob
    for entry in globber(pattern):
        if entry.name.startswith(UNDO_LOG_NAME):
            continue
        if entry.is_file():
            yield entry


def _staged_rename(pairs: Sequence[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """Rename every ``(source, target)`` pair through a temporary name.

    Two phases mean a plan (or an undo) that swaps or rotates names cannot clobber
    itself: no source moves to a final name until every source has already moved
    out of the way. Returns the pairs that were renamed, in input order.
    """
    staged: list[tuple[Path, Path, Path]] = []  # (original, temp, target)
    for index, (source, target) in enumerate(pairs):
        temp = source.with_name(f".__renamer_tmp__{index}__{source.name}")
        source.rename(temp)
        staged.append((source, temp, target))

    done: list[tuple[Path, Path]] = []
    for original, temp, target in staged:
        temp.rename(target)
        done.append((original, target))

    return done


def apply_plan(
    plan: RenamePlan,
    *,
    log_dir: Path | None = None,
) -> list[tuple[Path, Path]]:
    """Execute a plan on disk. Returns the (old, new) pairs actually renamed.

    Args:
        plan: the plan to execute.
        log_dir: folder that keeps the undo log. Defaults to the folder holding
            the renamed files (their common ancestor when a plan spans several
            folders, e.g. a recursive one).

    On success the batch is appended to the undo log, so it can be reversed later
    with :func:`revert_from_log`.

    Raises:
        ValueError: if the plan contains problems; callers should preview first.
        FileExistsError: if a target already exists and is not part of the rename.
        UndoError: if an existing undo log cannot be read - nothing is renamed
            then - or if the new entry cannot be written after the renames.
    """
    if plan.problems:
        raise ValueError(
            "refusing to apply an unsafe plan: "
            + "; ".join(f"{p.new_name} ({p.reason})" for p in plan.problems)
        )

    operations = plan.changes
    if not operations:
        return []

    sources = {op.source for op in operations}

    # Refuse to silently overwrite a file that this plan does not itself move.
    for op in operations:
        target = op.target
        if target.exists() and target not in sources:
            raise FileExistsError(f"target already exists: {target}")

    directory = Path(log_dir) if log_dir is not None else _infer_log_dir(operations)
    # Read the stack *before* touching the filesystem: a corrupt log must never
    # cost the user the record of the batches that are still undoable.
    entries = list(read_undo_log(directory))

    done = _staged_rename([(op.source, op.target) for op in operations])

    entries.append(
        UndoEntry(
            timestamp=_timestamp(),
            steps=tuple(
                UndoStep(
                    original=_record_name(old, directory),
                    final=_record_name(new, directory),
                )
                for old, new in done
            ),
        )
    )
    _write_undo_log(directory, entries)
    return done


# --- undo ------------------------------------------------------------------


class UndoError(Exception):
    """Raised when a batch cannot be undone safely.

    Messages are written for the person at the keyboard: what is wrong, and what
    to do about it. The CLI prints them as a one-line error, never a traceback.
    """


@dataclass(frozen=True)
class UndoStep:
    """One recorded rename: the name before and after a successful apply."""

    original: str
    final: str


@dataclass(frozen=True)
class UndoEntry:
    """One successful apply, reversed as a unit by :func:`revert_from_log`."""

    timestamp: str
    steps: tuple[UndoStep, ...]


def undo_log_path(directory: Path) -> Path:
    """Return the path of the undo log belonging to ``directory``."""
    return Path(directory) / UNDO_LOG_NAME


def _timestamp() -> str:
    """UTC timestamp with second precision, so log entries are readable by eye."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_name(path: Path, directory: Path) -> str:
    """Name to store for ``path``: relative to ``directory`` when possible.

    Relative names keep the log valid when the whole folder is moved or renamed
    later - and mean a log copied somewhere else cannot silently point at files
    outside its own folder.
    """
    try:
        return os.path.relpath(path, directory)
    except ValueError:
        # Windows: the path lives on another drive, so it has no relative form.
        return str(path)


def _resolve_name(directory: Path, name: str) -> Path:
    """Turn a stored name back into a path, relative to the log's directory."""
    candidate = Path(name)
    return candidate if candidate.is_absolute() else directory / candidate


def _infer_log_dir(operations: Sequence[RenameOperation]) -> Path:
    """Pick the log folder when the caller did not name one.

    Normally every file sits in one folder. A recursive plan can span several, so
    fall back to their common ancestor: one ``--undo`` then reverses the batch as
    a whole.
    """
    parents = [op.source.parent for op in operations]
    if len({str(parent) for parent in parents}) == 1:
        return parents[0]
    try:
        return Path(os.path.commonpath([str(parent) for parent in parents]))
    except ValueError:
        # Different drives on Windows: keep the log next to the first file.
        return parents[0]


def _json_entry(entry: UndoEntry) -> dict[str, Any]:
    """Serialise one entry; the order (original, final) is part of the format."""
    return {
        "timestamp": entry.timestamp,
        "operations": [
            {"original": step.original, "final": step.final} for step in entry.steps
        ],
    }


def _write_undo_log(directory: Path, entries: Sequence[UndoEntry]) -> None:
    """Persist ``entries``, or delete the log once the stack is empty.

    The document is written to a temporary sibling and then moved into place, so
    an interrupted write cannot leave half a JSON file behind.
    """
    path = undo_log_path(directory)
    if not entries:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise UndoError(f"cannot remove the used-up undo log {path}: {exc}") from exc
        return

    payload = {
        "version": UNDO_LOG_VERSION,
        "directory": str(directory),
        "entries": [_json_entry(entry) for entry in entries],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    temp = path.with_name(f"{path.name}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise UndoError(f"cannot write the undo log {path}: {exc}") from exc


def _parse_entry(log_path: Path, index: int, raw: Any) -> UndoEntry:
    """Validate and convert one raw entry from the JSON document."""
    where = f"entry {index + 1} of {log_path}"
    if not isinstance(raw, dict):
        raise UndoError(f"undo log is malformed: {where} is not a JSON object")

    timestamp = raw.get("timestamp")
    raw_operations = raw.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise UndoError(f"undo log is malformed: {where} has no operations")

    steps: list[UndoStep] = []
    for op_index, raw_operation in enumerate(raw_operations):
        if not isinstance(raw_operation, dict):
            raise UndoError(
                f"undo log is malformed: {where}, operation {op_index + 1} "
                "is not a JSON object"
            )
        original = raw_operation.get("original")
        final = raw_operation.get("final")
        if (
            not isinstance(original, str)
            or not isinstance(final, str)
            or not original
            or not final
        ):
            raise UndoError(
                f"undo log is malformed: {where}, operation {op_index + 1} needs "
                "non-empty string 'original' and 'final' names"
            )
        steps.append(UndoStep(original=original, final=final))

    return UndoEntry(
        timestamp=timestamp if isinstance(timestamp, str) else "", steps=tuple(steps)
    )


def read_undo_log(directory: Path) -> tuple[UndoEntry, ...]:
    """Read the undo stack of ``directory``, oldest entry first.

    Returns an empty tuple when there is no log yet (nothing was ever applied, or
    everything has been undone). A log that exists but cannot be read or parsed
    raises :class:`UndoError` instead of a raw traceback.
    """
    path = undo_log_path(directory)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise UndoError(f"cannot read the undo log {path}: {exc}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UndoError(
            f"undo log {path} is not valid JSON ({exc}); "
            "delete the file to start a fresh undo history"
        ) from exc

    if not isinstance(payload, dict):
        raise UndoError(
            f"undo log {path} is malformed: expected a JSON object at the top level; "
            "delete the file to start a fresh undo history"
        )

    version = payload.get("version")
    if version != UNDO_LOG_VERSION:
        raise UndoError(
            f"undo log {path} has unsupported version {version!r} "
            f"(expected {UNDO_LOG_VERSION}); "
            "delete the file to start a fresh undo history"
        )

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise UndoError(
            f"undo log {path} is malformed: 'entries' must be a list; "
            "delete the file to start a fresh undo history"
        )

    return tuple(_parse_entry(path, index, raw) for index, raw in enumerate(raw_entries))


def revert_from_log(directory: Path) -> list[tuple[Path, Path]]:
    """Reverse the most recent applied batch recorded in ``directory``.

    The log is a stack: every successful :func:`apply_plan` pushes one entry and
    this function pops the newest one, so successive undos unwind the history in
    reverse order.

    Returns:
        The ``(current, restored)`` pairs that were renamed, in log order.

    Raises:
        UndoError: when there is no log, it cannot be understood, or reversing it
            would be unsafe - a renamed file is missing, or an original name is
            taken by a file that is not part of this batch. Nothing is changed in
            any of those cases.
    """
    target = Path(directory)
    log_path = undo_log_path(target)
    entries = list(read_undo_log(target))
    if not entries:
        raise UndoError(
            f"no undo log at {log_path}: there is nothing to undo in {target}"
        )

    entry = entries[-1]
    pairs: list[tuple[Path, Path]] = [
        (_resolve_name(target, step.final), _resolve_name(target, step.original))
        for step in entry.steps
    ]

    # The log is a plain text file, so it can be edited by hand. Refuse a batch
    # whose stored names are not unique: replaying it could not be correct.
    finals = [os.path.normcase(str(current)) for current, _ in pairs]
    originals = [os.path.normcase(str(restored)) for _, restored in pairs]
    if len(set(finals)) != len(finals) or len(set(originals)) != len(originals):
        raise UndoError(
            f"undo log {log_path} is malformed: the newest batch reuses a file name; "
            "delete the file to start a fresh undo history"
        )

    # Safety rule 1: every renamed file must still be where the log left it.
    for current, _restored in pairs:
        if not current.exists():
            raise UndoError(
                f"cannot undo: {current} is missing (renamed or deleted since it was "
                f"logged); put it back, or delete {log_path} to discard this record"
            )

    # Safety rule 2: no original name may be taken, except by a file this very
    # batch moves out of the way (a swap or a rotation).
    moved = {current for current, _restored in pairs}
    for _current, restored in pairs:
        if restored.exists() and restored not in moved:
            raise UndoError(
                f"cannot undo: {restored} already exists and is not part of this batch; "
                f"move it aside, or delete {log_path} to discard this record"
            )

    done = _staged_rename(pairs)
    # Pop the entry only after the renames succeeded; an empty stack removes the
    # file entirely, so a finished history leaves no clutter behind.
    _write_undo_log(target, entries[:-1])
    return done


def format_plan(
    plan: RenamePlan,
    *,
    limit: int | None = None,
    color: bool = False,
) -> str:
    """Render a plan as human-readable text for the console.

    When the plan has problems we deliberately print *only* the problems: showing
    a rename listing next to "refusing to continue" is contradictory and misleads
    the user into thinking the repairs will be applied.

    Args:
        plan: the plan to render.
        limit: show at most this many renames, then a "... and N more" line.
        color: wrap the parts of the output in ANSI colour codes. ``False``
            (the default) produces plain text with no escape sequences at all,
            which is what callers that capture or parse the output want. Every
            coloured span is closed with a reset, so colour never leaks into
            the text that follows.
    """
    palette = Palette(enabled=bool(color))
    lines: list[str] = []
    changes = plan.changes

    if plan.problems:
        lines.append(
            palette.yellow("No changes will be made - the plan has problems:")
        )
        lines.append("")
        for problem in plan.problems:
            lines.append(palette.red(f"  [!] {problem.new_name}: {problem.reason}"))
        return "\n".join(lines)

    if not changes:
        lines.append(palette.yellow("Nothing to rename."))
        return "\n".join(lines)

    shown = changes if limit is None else changes[:limit]
    width = max((len(op.source.name) for op in shown), default=0)
    for op in shown:
        # The old name stays plain (just dimmed) and the new name is the part
        # that stands out, so the eye lands on the result of the rename.
        padding = " " * (width - len(op.source.name))
        old = palette.dim(op.source.name)
        arrow = palette.green("->")
        new = palette.green(op.new_name)
        lines.append(f"  {old}{padding}  {arrow}  {new}")
    if limit is not None and len(changes) > limit:
        lines.append(f"  ... and {len(changes) - limit} more")

    return "\n".join(lines)
