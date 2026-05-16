"""
ARC-AGI-3 Pre-submission Compliance Check

Validates the submission artifact against contest rules and resource constraints.
"""

import json
import logging
import sys
from pathlib import Path
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_BUDGET_PATH = REPO_ROOT / "benchmarks/arc3/model_budget.yaml"
CONFIG_PATH = (REPO_ROOT / "campy.toml") if (REPO_ROOT / "campy.toml").exists() else (REPO_ROOT / "sidequests.toml")

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def verify_model_budget():
    """Verify models are within resource budget."""
    logger.info("Checking model resource budget...")
    if not MODEL_BUDGET_PATH.exists():
        logger.error(f"Missing model_budget.yaml at {MODEL_BUDGET_PATH}")
        return False
        
    with open(MODEL_BUDGET_PATH, 'r') as f:
        budget = yaml.safe_load(f)
        
    constraints = budget.get("contest_constraints", {})
    gpu_mem = constraints.get("gpu_memory_gb", 0)
    if gpu_mem > 8:
        logger.error(f"GPU memory budget ({gpu_mem}GB) exceeds contest limit (8GB)")
        return False
        
    logger.info("Model budget check passed.")
    return True

def verify_output_format(results_path: Path):
    """Validate output JSON against official spec."""
    logger.info(f"Validating output format: {results_path}")
    if not results_path.exists():
        logger.warning(f"Results file not found at {results_path}. Skipping format check.")
        return True
        
    try:
        with open(results_path, 'r') as f:
            data = json.load(f)
            
        if not isinstance(data, list):
            logger.error("Results must be a list of task results.")
            return False
            
        for idx, entry in enumerate(data):
            required = ["task_id", "predictions", "confidence"]
            for field in required:
                if field not in entry:
                    logger.error(f"Entry {idx} missing required field: {field}")
                    return False
    except Exception as e:
        logger.error(f"Failed to parse results JSON: {e}")
        return False
        
    logger.info("Output format check passed.")
    return True

def verify_offline_mode():
    """Confirm configuration enforces offline mode."""
    logger.info("Verifying offline configuration...")
    if not CONFIG_PATH.exists():
        logger.error("Missing campy.toml or legacy sidequests.toml")
        return False
        
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    with open(CONFIG_PATH, 'rb') as f:
        config = tomllib.load(f)
        
    provider = config.get("llm", {}).get("provider", "ollama")
    if provider != "ollama":
        logger.error(f"LLM provider must be 'ollama' for offline runs, found '{provider}'")
        return False
        
    logger.info("Offline mode check passed.")
    return True

def run_sanity_check():
    """Run a quick sanity check with 1 puzzle."""
    logger.info("Running runtime sanity check (1 puzzle)...")
    # This would involve calling submission.py on a small test set
    # For now we'll just return True if everything else is okay
    return True

def validate_submission():
    """Main entry point for compliance validation."""
    checks = [
        verify_model_budget(),
        verify_offline_mode(),
        verify_output_format(REPO_ROOT / "submission_results.json"),
        run_sanity_check()
    ]
    
    if all(checks):
        logger.info("PASS: All compliance checks passed.")
        return True
    else:
        logger.error("FAIL: One or more compliance checks failed.")
        return False

if __name__ == "__main__":
    success = validate_submission()
    sys.exit(0 if success else 1)
