"""Observability bridge for SideQuests helpers consumed by ARC_AGI.

This module provides a very small, dependency-free observability shim so
production ARC code can import `sidequest_mcp_client.observability` without
pulling SideQuests internals into the runtime. It intentionally does not
depend on `mcp_engine` at import time. If richer observability is required
from SideQuests, that should be provided via MCP endpoints or a follow-up
card in the SideQuests repo.
"""

from contextlib import AbstractContextManager
from typing import Any, Mapping, Optional


class _NoopSpan(AbstractContextManager):
    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def set_attribute(self, *args, **kwargs):
        return None

    def add_event(self, *args, **kwargs):
        return None


class Observability:
    """Minimal, dependency-free observability surface used by ARC.

    This intentionally implements only the tiny contract consumers expect: a
    `span()` context manager. It is a noop implementation suitable for
    production usage when a SideQuests-backed observability implementation
    is not available.
    """

    def span(self, name: str, attrs: Optional[Mapping[str, Any]] = None) -> _NoopSpan:
        return _NoopSpan()


REQUIRED_DECISION_FIELDS: list[str] = []
REQUIRED_OUTCOME_FIELDS: list[str] = []


def canonical_span_name(name: str) -> str:
    """Normalize a span name into a canonical dotted form.

    This mirrors the small transformation consumers expect; it deliberately
    does not require SideQuests internals.
    """
    return name.replace(" ", ".")


def ensure_contract_fields(decision: Mapping[str, Any], outcome: Mapping[str, Any]) -> bool:
    """No-op validator used when SideQuests-backed contract checks are
    unavailable. Returns True (contract satisfied) by default.
    """
    return True


def build_observability(config: Optional[dict] = None) -> Observability:
    """Return a production-safe Observability implementation.

    Avoids importing SideQuests internals so production code can import this
    module safely. If a richer implementation is needed, create a SideQuests
    MCP endpoint and add a follow-up card.
    """
    return Observability()
