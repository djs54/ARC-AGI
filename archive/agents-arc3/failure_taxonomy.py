"""Compatibility shim for failure taxonomy symbols moved to agents.common."""

from agents.common.failure_taxonomy import *  # noqa: F401,F403
from agents.common.failure_taxonomy import FailureClass, FailureTaxonomy, classify_failure

__all__ = ["FailureTaxonomy", "FailureClass", "classify_failure"]
