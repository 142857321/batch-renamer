"""Package marker for batch-renamer."""

from renamer.color import (
    COLOR_MODES,
    Palette,
    color_enabled,
    strip_ansi,
)
from renamer.core import (
    UNDO_LOG_NAME,
    Problem,
    RenameOperation,
    RenamePlan,
    RenameRule,
    UndoEntry,
    UndoError,
    UndoStep,
    apply_plan,
    format_plan,
    iter_files,
    plan_renames,
    read_undo_log,
    revert_from_log,
    sanitize,
    undo_log_path,
)

__version__ = "0.1.0"

__all__ = [
    "COLOR_MODES",
    "Palette",
    "Problem",
    "RenameOperation",
    "RenamePlan",
    "RenameRule",
    "UNDO_LOG_NAME",
    "UndoEntry",
    "UndoError",
    "UndoStep",
    "apply_plan",
    "color_enabled",
    "format_plan",
    "iter_files",
    "plan_renames",
    "read_undo_log",
    "revert_from_log",
    "sanitize",
    "strip_ansi",
    "undo_log_path",
    "__version__",
]
