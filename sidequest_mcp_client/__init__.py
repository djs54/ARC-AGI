"""Bridge package that isolates ARC_AGI from HippoCampy/Campy internals.

This package exposes the MCP-facing transport and client helpers that
production ARC code may consume. Non-MCP compatibility helpers should be
placed behind explicit test-only modules and must not be imported by
production paths.
"""

__all__ = [
	"mcp_session",
	"mcp_brain_client",
	"readiness",
	"observability",
]
