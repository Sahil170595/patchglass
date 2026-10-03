"""Built-in parser registration. `load_builtins()` guarantees every shipped parser is registered.

Idempotent: registering an already-known name simply re-points it (registry is a dict), so calling
this repeatedly is safe.
"""

from __future__ import annotations

from typing import Final

from taskbundle.parsers import (
    ansible_parser,
    django_parser,
    gotest_parser,
    jest_parser,
    mocha_parser,
    pytest_parser,
    scale_parser,
    secb_parser,
)
from taskbundle.parsers.base import Parser
from taskbundle.parsers.registry import register

# All built-in parser instances, in a deterministic order.
_BUILTINS: Final[tuple[Parser, ...]] = (
    pytest_parser.parser,
    django_parser.parser,
    gotest_parser.parser,
    jest_parser.parser,
    mocha_parser.parser,
    ansible_parser.parser,
    scale_parser.parser,
    secb_parser.parser,
)

_loaded = False


def load_builtins() -> None:
    """Register every built-in parser exactly once (idempotent)."""
    global _loaded
    if _loaded:
        return
    for parser in _BUILTINS:
        register(parser)
    _loaded = True
