"""
Minimal provider shim for mcp_engine.llm.provider used in tests.
"""

def create_llm_client(cfg=None):
    """Return None by default; tests may monkeypatch this function."""
    return None
