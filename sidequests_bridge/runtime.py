"""Runtime bridge for SideQuests-backed services used by ARC_AGI.

This module intentionally avoids importing SideQuests (`mcp_engine`) internals
at module import time. Callers should use the wrapper functions below which
perform lazy imports only when the functionality is actually required. This
keeps production entrypoints from importing Kuzu/schema/loop bootstrap code
during startup.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
	"""Load SideQuests/ARC config lazily.

	This wraps `mcp_engine.config.load_config` but avoids importing it until
	this function is called.
	"""
	from mcp_engine.config import load_config as _load_config

	return _load_config(path) if path is not None else _load_config()


def create_llm_client(config: Dict[str, Any]):
	from mcp_engine.llm.provider import create_llm_client as _create_llm_client

	return _create_llm_client(config)


def KuzuClient(path: str):
	from mcp_engine.graph.kuzu_client import KuzuClient as _KuzuClient

	return _KuzuClient(path)


def init_schema(db: Any, seed_path: str, embedding_model: str):
	from mcp_engine.schema import init_schema as _init_schema

	return _init_schema(db, seed_path, embedding_model)


def embeddings():
	from mcp_engine.graph import embeddings as _emb

	return _emb


def init_loop_queue(queue):
	from mcp_engine.tools import init_loop_queue as _init_loop_queue

	return _init_loop_queue(queue)


def load_centroids(db):
	from mcp_engine.loop.step2_gist import load_centroids as _load_centroids

	return _load_centroids(db)


def load_routing_table(db):
	from mcp_engine.loop.step3_schema_org import load_routing_table as _load_routing_table

	return _load_routing_table(db)


def run_loop(*, message_id, text, role, db, llm_client, config, centroids, session_id, precomputed=None):
	from mcp_engine.loop.orchestrator import run_loop as _run_loop

	return _run_loop(
		message_id=message_id,
		text=text,
		role=role,
		db=db,
		llm_client=llm_client,
		config=config,
		centroids=centroids,
		session_id=session_id,
		precomputed=precomputed,
	)

