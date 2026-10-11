"""Command line interface for batch-renamer.

Preview is the default: you must pass --apply to touch the filesystem.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from renamer import __version__
from renamer.color import COLOR_MODES, color_enabled
from renamer.core import (
    RenameRule,
    UndoError,
    apply_plan,
    format_plan,
    iter_files,
    plan_renames,
    revert_from_log,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="renamer",
        description="Batch rename files. Previews by default; pass --apply to run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  renamer . --find IMG --replace photo\n"
            "  renamer . --prefix 2026_ --suffix _final --apply\n"
            "  renamer . --regex --find '\\d+' --replace N --number --padding 3\n"
            "  renamer ./photos --sort mtime --number --padding 3\n"
            "  renamer ./photos --pattern '*.jpg' --recursive --lowercase\n"
            "  renamer ./photos --undo\n"
        ),
    )
    parser.add_argument("directory", type=Path, help="folder containing the files")
    parser.add_argument("--pattern", default="*", help="glob pattern (default: *)")
    parser.add_argument(
        "-r", "--recursive", action="store_true", help="recurse into subfolders"
    )

    rule = parser.add_argument_group("rename rules")
    rule.add_argument("--find", default="", help="text to search for in the name")
    rule.add_argument("--replace", default="", help="replacement text")
    rule.add_argument("--regex", action="store_true", help="treat --find as a regex")
    rule.add_argument("--prefix", default="", help="text to insert before the name")
    rule.add_argument("--suffix", default="", help="text to append after the name")
    rule.add_argument("--lowercase", action="store_true", help="force lowercase")
    rule.add_argument("--uppercase", action="store_true", help="force uppercase")

    numbering = parser.add_argument_group("numbering and ordering")
    numbering.add_argument(
        "--number", action="store_true", help="append a running index"
    )
    numbering.add_argument(
        "--start", type=int, default=1, help="first index (default: 1)"
    )
    numbering.add_argument(
        "--padding", type=int, default=0, help="zero-pad the index to this width"
    )
    numbering.add_argument(
        "--sort",
        choices=["name", "mtime", "size", "none"],
        default="name",
        help=(
            "order the files are processed in, which decides the numbering: "
            "'name' (default, case-insensitive), 'mtime' (newest first), "
            "'size' (biggest first) or 'none' (as given)"
        ),
    )

    output = parser.add_argument_group("output")
    action = output.add_mutually_exclusive_group()
    action.add_argument(
        "--apply", action="store_true", help="actually rename (default: preview only)"
    )
    action.add_argument(
        "--undo",
        action="store_true",
        help=(
            "reverse the most recent applied batch in the directory and exit; "
            "does not use the rename rules"
        ),
    )
    output.add_argument(
        "--quiet", action="store_true", help="suppress the per-file listing"
    )
    output.add_argument(
        "--color",
        choices=list(COLOR_MODES),
        default="auto",
        help=(
            "colourise the preview: 'auto' (default) only when stdout is a real "
            "terminal and NO_COLOR is unset, 'always' to force it on (e.g. for "
            "'less -R'), 'never' to force it off"
        ),
    )
    output.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    return parser


def run_undo(directory: Path) -> int:
    """Reverse the most recent batch in ``directory`` and report the result."""
    try:
        restored = revert_from_log(directory)
    except UndoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if restored:
        width = max(len(current.name) for current, _ in restored)
        for current, target in restored:
            print(f"  {current.name:<{width}}  ->  {target.name}")
    print(f"\nRestored {len(restored)} file(s) to their previous names.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    directory: Path = args.directory
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 2

    if args.undo:
        return run_undo(directory)

    if args.lowercase and args.uppercase:
        print("error: --lowercase and --uppercase are mutually exclusive", file=sys.stderr)
        return 2

    rule = RenameRule(
        find=args.find,
        replace=args.replace,
        prefix=args.prefix,
        suffix=args.suffix,
        use_regex=args.regex,
        lowercase=args.lowercase,
        uppercase=args.uppercase,
        start_index=args.start,
        number_padding=args.padding,
    )

    paths = list(
        iter_files(directory, pattern=args.pattern, recursive=args.recursive)
    )
    if not paths:
        print(f"No files matched in {directory} (pattern: {args.pattern})")
        return 0

    plan = plan_renames(paths, rule, number=args.number, sort=args.sort)

    # Resolved once, from the real stdout: see renamer.color for the rules.
    use_color = color_enabled(args.color)

    if not args.quiet:
        print(format_plan(plan, color=use_color))

    if plan.problems:
        print(
            "\nRefusing to continue: fix the problems above (or narrow --pattern).",
            file=sys.stderr,
        )
        return 1

    if not args.apply:
        print(f"\nPreview only: {len(plan.changes)} file(s) would be renamed.")
        print("Re-run with --apply to make the change.")
        return 0

    renamed = apply_plan(plan)
    print(f"\nRenamed {len(renamed)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
