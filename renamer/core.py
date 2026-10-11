"""Core logic for batch-renamer: compute new names safely, without touching disk.

The design goal is that rename planning is a *pure function*: it takes a list of
names plus rules and returns a plan. Nothing is written until the caller applies
the plan, which makes preview mode trivial and keeps the tricky parts testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

# Characters Windows forbids in file names, plus control characters.
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

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


def plan_renames(
    paths: Iterable[Path],
    rule: RenameRule,
    *,
    number: bool = False,
    sort: bool = True,
) -> RenamePlan:
    """Build a :class:`RenamePlan` for ``paths`` under ``rule``.

    Nothing on disk is modified. Problems (empty names, illegal characters,
    reserved device names, duplicates) are collected instead of raised, so the
    user can inspect and fix them before committing.
    """
    items = list(paths)
    if sort:
        items.sort(key=lambda p: p.name.lower())

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
    """Yield matching files (never directories) under ``directory``."""
    globber = directory.rglob if recursive else directory.glob
    for entry in globber(pattern):
        if entry.is_file():
            yield entry


def apply_plan(plan: RenamePlan) -> list[tuple[Path, Path]]:
    """Execute a plan on disk. Returns the (old, new) pairs actually renamed.

    Raises:
        ValueError: if the plan contains problems; callers should preview first.
        FileExistsError: if a target already exists and is not part of the rename.
    """
    if plan.problems:
        raise ValueError(
            "refusing to apply an unsafe plan: "
            + "; ".join(f"{p.new_name} ({p.reason})" for p in plan.problems)
        )

    operations = plan.changes
    sources = {op.source for op in operations}

    # Refuse to silently overwrite a file that this plan does not itself move.
    for op in operations:
        target = op.target
        if target.exists() and target not in sources:
            raise FileExistsError(f"target already exists: {target}")

    # Two-phase rename: every source moves to a temp name first, so that plans
    # which swap or rotate names cannot collide with themselves.
    staged: list[tuple[Path, Path, Path]] = []  # (original, temp, target)
    for index, op in enumerate(operations):
        temp = op.source.with_name(f".__renamer_tmp__{index}__{op.source.name}")
        op.source.rename(temp)
        staged.append((op.source, temp, op.target))

    done: list[tuple[Path, Path]] = []
    for original, temp, target in staged:
        temp.rename(target)
        done.append((original, target))

    return done


def format_plan(plan: RenamePlan, *, limit: int | None = None) -> str:
    """Render a plan as human-readable text for the console.

    When the plan has problems we deliberately print *only* the problems: showing
    a rename listing next to "refusing to continue" is contradictory and misleads
    the user into thinking the repairs will be applied.
    """
    lines: list[str] = []
    changes = plan.changes

    if plan.problems:
        lines.append("No changes will be made - the plan has problems:")
        lines.append("")
        for problem in plan.problems:
            lines.append(f"  [!] {problem.new_name}: {problem.reason}")
        return "\n".join(lines)

    if not changes:
        lines.append("Nothing to rename.")
        return "\n".join(lines)

    shown = changes if limit is None else changes[:limit]
    width = max((len(op.source.name) for op in shown), default=0)
    for op in shown:
        lines.append(f"  {op.source.name:<{width}}  ->  {op.new_name}")
    if limit is not None and len(changes) > limit:
        lines.append(f"  ... and {len(changes) - limit} more")

    return "\n".join(lines)
