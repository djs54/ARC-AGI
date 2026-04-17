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
from sidequests_bridge.mcp_brain_client import MCPBrainClient
from agents.arc3.runner import DurableARCRunner
from benchmarks.arc3.harness import ARC3Harness, load_tasks_from_manifest
from benchmarks.harness import BenchmarkConfig
from sidequests_bridge.runtime import (
    create_llm_client,
    load_config,
    run_loop,
)
from sidequests_bridge.readiness import check_mcp_readiness, ReadinessError

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
        
        # Production startup: verify SideQuests MCP readiness rather than
        # bootstrapping local Kuzu/schema/loop internals.
        required_tools = [
            "notify_turn",
            "current_truth",
            "register_plan",
            "report_outcome",
            "recall_plans",
        ]
        try:
            check_mcp_readiness(required_tools=required_tools)
        except ReadinessError as exc:
            raise RuntimeError(str(exc))

        # In production, ARC does not bootstrap Kuzu/schema here; the harness
        # below will initialize client-facing pieces (LLM client) and use an
        # MCP-backed brain client to talk to SideQuests.
        
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
    brain_client = MCPBrainClient(runner.db, runner.config)
    durable = DurableARCRunner(runner.harness, brain_client, runner.config)
    runner.results = await durable.run(runner.tasks, card_id)
    runner.export_results()

if __name__ == "__main__":
    asyncio.run(main())
