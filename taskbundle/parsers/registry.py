"""Parser registry: name -> Parser. Built-ins register themselves; task.json names one."""

from __future__ import annotations

from taskbundle.errors import BundleValidationError
from taskbundle.parsers.base import Parser

_REGISTRY: dict[str, Parser] = {}


def register(parser: Parser) -> Parser:
    """Register a parser under its `name`. Returns it (usable as a decorator)."""
    _REGISTRY[parser.name] = parser
    return parser


def get_parser(name: str) -> Parser:
    """Resolve a parser by name. Raises BundleValidationError with the known names if absent."""
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise BundleValidationError(f"unknown parser {name!r}; known parsers: {known}") from None


def available() -> list[str]:
    """Names of all registered parsers."""
    return sorted(_REGISTRY)
