"""
Minimal orchestrator.run_loop stub used by some tests.
"""

import asyncio

async def run_loop(*args, **kwargs):
    """Default no-op run_loop; tests can monkeypatch this."""
    await asyncio.sleep(0)
    return True
