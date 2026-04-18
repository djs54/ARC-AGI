"""
ARC-AGI-3 Submission Runner

This script serves as the main entry point for the ARC-AGI-3 contest evaluation.
It initializes the SideQuest Brain, runs the memory-augmented agent on the tasks,
and exports results in the required format.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any, List

import yaml

from benchmarks.arc3.adapter import LocalBrainClient
from agents.arc3.runner import DurableARCRunner
from benchmarks.arc3.harness import ARC3Harness, load_tasks_from_manifest
from benchmarks.harness import BenchmarkConfig
from mcp_engine.config import load_config
from mcp_engine.graph.kuzu_client import KuzuClient
from mcp_engine.schema import init_schema
from mcp_engine.graph import embeddings as emb
from mcp_engine.tools import init_loop_queue
from mcp_engine.loop.step2_gist import load_centroids
from mcp_engine.loop.step3_schema_org import load_routing_table

# Configuration paths
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "sidequests.toml"
MANIFEST_PATH = REPO_ROOT / "benchmarks/arc3/tasks_manifest.json"
OUTPUT_PATH = REPO_ROOT / "submission_results.json"
DB_PATH = Path.home() / ".sidequests" / "brain.db"
SEED_PATH = REPO_ROOT / "InvertorsDocs" / "GistSeedExamples.md"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SubmissionRunner:
    def __init__(self):
        self.config = load_config()
        self.db = None
        self.harness = None
        self.loop_queue = asyncio.Queue()
        self.tasks = []
        self.results = []

    async def initialize(self):
        logger.info("Initializing Submission Runner...")
        
        # 1. Initialize Database
        DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = KuzuClient(str(DB_PATH))
        
        # 2. Pre-warm Embedder
        embedding_model = self.config.get("embeddings", {}).get(
            "model", "sentence-transformers/all-MiniLM-L6-v2"
        )
        emb.prewarm(embedding_model)
        
        # 3. Initialize Schema
        init_schema(self.db, str(SEED_PATH), embedding_model)
        
        # 4. Load Loop State
        centroids = load_centroids(self.db)
        load_routing_table(self.db)
        init_loop_queue(self.loop_queue)
        
        # 5. Start Background Loop Worker
        asyncio.create_task(self._loop_worker(centroids))
        
        # 6. Initialize Harness
        # Convert dict config to BenchmarkConfig dataclass
        benchmark_config = BenchmarkConfig(
            name="ARC-AGI-3",
            description="A/B evaluation: Baseline vs SideQuests-augmented agent",
            timeout=3600,
            memory_limit_gb=8.0,
            cpu_limit_percent=80.0,
            parameters=self.config.get("benchmark", {})
        )
        self.harness = ARC3Harness(benchmark_config, db=self.db)
        await self.harness.setup()
        
        # 7. Load Tasks
        if MANIFEST_PATH.exists():
            self.tasks = load_tasks_from_manifest(str(MANIFEST_PATH))
            logger.info(f"Loaded {len(self.tasks)} tasks from manifest.")
        else:
            logger.warning(f"Manifest not found at {MANIFEST_PATH}. Running with empty task set.")

    async def _loop_worker(self, centroids):
        """Minimal loop worker for submission."""
        from mcp_engine.loop.orchestrator import run_loop
        from mcp_engine.llm.provider import create_llm_client
        
        llm_client = create_llm_client(self.config)
        
        while True:
            message_id, text, role, session_id = await self.loop_queue.get()
            try:
                await run_loop(
                    message_id=message_id,
                    text=text,
                    role=role,
                    db=self.db,
                    llm_client=llm_client,
                    config=self.config,
                    centroids=centroids,
                    session_id=session_id,
                )
            except Exception as e:
                logger.error(f"Loop worker error: {e}")
                continue
            finally:
                self.loop_queue.task_done()

    def export_results(self):
        logger.info(f"Exporting results to {OUTPUT_PATH}")
        with open(OUTPUT_PATH, 'w') as f:
            json.dump(self.results, f, indent=2)

async def main():
    runner = SubmissionRunner()
    await runner.initialize()

    card_id = runner.config.get("benchmark", {}).get("card_id") or "local"
    brain_client = LocalBrainClient(runner.db, runner.config)
    durable = DurableARCRunner(runner.harness, brain_client, runner.config)
    runner.results = await durable.run(runner.tasks, card_id)
    runner.export_results()

if __name__ == "__main__":
    asyncio.run(main())
