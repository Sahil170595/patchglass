"""Typed error hierarchy. User-facing messages are actionable, not raw tracebacks."""

from __future__ import annotations


class TaskBundleError(Exception):
    """Base for all task-bundle errors. Carries an operator-actionable message."""


class BundleValidationError(TaskBundleError):
    """task.json (or bundle layout) failed schema/invariant validation."""


class BundleNotFoundError(TaskBundleError):
    """A bundle directory or a required bundle file is missing."""


class ImageError(TaskBundleError):
    """Failed to obtain/resolve the environment image (pull or build)."""


class ContainerError(TaskBundleError):
    """A container operation (create/exec/copy) failed."""


class PatchApplyError(TaskBundleError):
    """The solver/golden patch did not apply under any strategy."""


class SolverError(TaskBundleError):
    """The solver could not produce a usable patch."""


class GradeError(TaskBundleError):
    """Grading could not be completed (e.g. unparseable/empty test output)."""


class GuardrailError(TaskBundleError):
    """`task validate` invariant violated (e.g. a fail2pass passes on baseline)."""


class IsolationError(TaskBundleError):
    """A required isolation guarantee could not be enforced on this host."""
