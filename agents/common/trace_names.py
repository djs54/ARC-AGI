"""A111 — Canonical naming and telemetry normalization for smoke artifacts.

Centralized helpers for consistent phase names, task identity, failure payloads,
and click-candidate stream fields across all artifact emission boundaries.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


# Canonical primary phase vocabulary
CANONICAL_PHASES = {
    "setup",
    "perceive",
    "model",
    "plan",
    "act",
    "evaluate",
    "replan",
    "summary",
}

# Legacy aliases to canonical mapping
PHASE_ALIASES = {
    "discover": "model",
    "hypothesize": None,  # Resolved by context, default to model
    "hypothesize_goal": "model",
    "hypothesize_archetype": "model",
    "hypothesize_mechanic": "model",
    "hypothesize_strategy": "plan",
    "hypothesize_action": "plan",
    "route": "plan",
    "execute": "act",
    "unknown": None,  # Resolved by context
}


def canonical_phase(value: str, *, context: Optional[str] = None) -> str:
    """Normalize phase name to canonical vocabulary.
    
    Args:
        value: Original phase name
        context: Optional context hint for disambiguation (e.g., "goal_inference", "action_selection")
    
    Returns:
        Canonical phase name or original if already canonical.
    """
    if not value:
        return "setup"
    
    value_lower = str(value).lower().strip()
    if value_lower in {"none", "null"}:
        return "setup"
    
    # Already canonical
    if value_lower in CANONICAL_PHASES:
        return value_lower
    
    # Try direct alias mapping
    if value_lower in PHASE_ALIASES:
        result = PHASE_ALIASES[value_lower]
        if result:
            return result
        
        # Resolve None aliases based on context
        if context:
            if "goal" in context or "archetype" in context or "mechanic" in context:
                return "model"
            elif "strategy" in context or "action" in context or "probe" in context:
                return "plan"
        
        # Default fallback for None aliases
        if value_lower == "hypothesize":
            return "model"
        elif value_lower == "unknown":
            return "setup"
        
        return "setup"
    
    # Try partial match
    for alias, canonical in PHASE_ALIASES.items():
        if alias in value_lower and alias != value_lower:
            if canonical:
                return canonical
            # For partial matches with None, still try context
            if context:
                if "goal" in context or "archetype" in context or "mechanic" in context:
                    return "model"
                elif "strategy" in context or "action" in context or "probe" in context:
                    return "plan"
    
    # Default fallback
    return value_lower


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _phase_context(payload: Dict[str, Any]) -> Optional[str]:
    text = " ".join(
        str(payload.get(key) or "")
        for key in (
            "operation",
            "event_type",
            "event",
            "phase_question",
            "phase_answer",
            "input_summary",
            "result_summary",
            "what",
        )
    ).lower()
    if any(token in text for token in ("goal", "archetype", "mechanic", "world model", "structure")):
        return "goal_inference"
    if any(token in text for token in ("strategy", "chunk", "action", "probe", "route", "plan")):
        return "action_selection"
    if any(token in text for token in ("changed", "reward", "observed", "evaluate")):
        return "evaluation"
    return None


def normalize_phase_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize top-level and nested phase fields in an artifact row."""
    result = dict(payload)
    context = _phase_context(result)
    for key in ("phase", "current_phase", "from_phase", "to_phase", "target_phase"):
        if key not in result:
            continue
        value = result.get(key)
        if value is None:
            if key in {"phase", "current_phase", "target_phase"}:
                result[key] = "setup"
            continue
        normalized = canonical_phase(str(value), context=context)
        if normalized != str(value).lower().strip():
            legacy_key = f"legacy_{key}"
            result.setdefault(legacy_key, value)
        result[key] = normalized

    tail = result.get("phase_history_tail")
    if isinstance(tail, list):
        normalized_tail = []
        for item in tail:
            if not isinstance(item, dict):
                normalized_tail.append(item)
                continue
            normalized_item = dict(item)
            for key in ("from", "to", "phase", "current_phase", "from_phase", "to_phase"):
                value = normalized_item.get(key)
                if value is not None:
                    normalized_item[key] = canonical_phase(str(value), context=context)
            normalized_tail.append(normalized_item)
        result["phase_history_tail"] = normalized_tail
    return result


def normalize_task_identity(
    payload: Dict[str, Any],
    canonical_task_id: str,
) -> Dict[str, Any]:
    """Normalize task identity fields in a payload.
    
    Preserves ARC task_id as primary, moves other identifiers to separate fields.
    
    Args:
        payload: The payload dict to normalize
        canonical_task_id: The canonical ARC task_id (e.g., "arc_eval_001")
    
    Returns:
        Normalized payload with cleared UUID-like fields from task_id.
    """
    result = dict(payload)
    
    # Ensure primary task_id is set
    result["task_id"] = canonical_task_id
    
    # Extract and move UUID-like identifiers
    # Move sidequest IDs if present
    if "sidequest_quest_id" in result:
        if not result.get("quest_id"):
            result["quest_id"] = result["sidequest_quest_id"]
        del result["sidequest_quest_id"]
    
    if "sidequest_session_id" in result:
        if not result.get("arc_session_id"):
            result["arc_session_id"] = result["sidequest_session_id"]
        del result["sidequest_session_id"]
    
    if "sidequest_run_id" in result:
        if not result.get("run_id"):
            result["run_id"] = result["sidequest_run_id"]
        del result["sidequest_run_id"]
    
    return result


def normalize_artifact_payload(
    payload: Any,
    canonical_task_id: Optional[str] = None,
) -> Any:
    """Recursively normalize smoke artifact payload names and identifiers.

    This is intended for artifact emission boundaries only. It does not mutate
    runtime solver state.
    """
    if isinstance(payload, list):
        return [normalize_artifact_payload(item, canonical_task_id) for item in payload]
    if not isinstance(payload, dict):
        return payload

    result: Dict[str, Any] = {}
    for key, value in payload.items():
        result[key] = normalize_artifact_payload(value, canonical_task_id)

    if canonical_task_id and "task_id" in result:
        original_task_id = result.get("task_id")
        if original_task_id != canonical_task_id and _UUID_RE.match(str(original_task_id or "")):
            result.setdefault("sidequest_task_id", original_task_id)
        result["task_id"] = canonical_task_id

    result = normalize_phase_fields(result)

    if "reason" in result:
        result["reason"] = normalize_action_override_reason(
            result.get("reason"),
            requested_action=result.get("requested_action"),
            selected_action=result.get("selected_action") or result.get("action_id"),
            override_action=result.get("override_action") or result.get("forced_action") or result.get("action_id"),
        )
    for key in ("override_reason", "guard_override_reason", "cheap_probe_reason"):
        if key in result:
            result[key] = normalize_action_override_reason(
                result.get(key),
                requested_action=result.get("requested_action"),
                selected_action=result.get("selected_action") or result.get("action_id"),
                override_action=result.get("override_action") or result.get("action_id"),
            )

    return result


def normalize_failure_payload(error_or_result: Optional[Exception | Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize failure/crash payload to include root-cause details.
    
    Args:
        error_or_result: Exception or result dict with failure info
    
    Returns:
        Normalized failure payload with structured fields.
    """
    payload = {}
    
    if isinstance(error_or_result, Exception):
        payload["failure_class"] = "crash"
        payload["exception_type"] = type(error_or_result).__name__
        payload["exception_message"] = str(error_or_result)
        payload["failure_reason"] = f"{type(error_or_result).__name__}: {str(error_or_result)}"
    elif isinstance(error_or_result, dict):
        payload["failure_class"] = error_or_result.get("failure_class", "unknown")
        payload["failure_reason"] = error_or_result.get("failure_reason", "")
        payload["exception_type"] = error_or_result.get("exception_type")
        payload["exception_message"] = error_or_result.get("exception_message")
        if payload["failure_class"] == "crash":
            payload["exception_type"] = payload["exception_type"] or "UnknownCrash"
            payload["exception_message"] = (
                payload["exception_message"]
                or "Crash root exception was not captured; inspect agent_execution_trace.json and master_timeline.json."
            )
            payload["failure_reason"] = payload["failure_reason"] or payload["exception_message"]
    else:
        payload["failure_class"] = "unknown"
        payload["failure_reason"] = "Unknown failure"
    
    # Add trace artifact pointer if available
    if isinstance(error_or_result, dict):
        if "trace_artifact" in error_or_result:
            payload["trace_artifact"] = error_or_result["trace_artifact"]
    
    return payload


def normalize_run_review(
    primary_artifact_path: str,
    artifact_paths: Optional[Dict[str, str]] = None,
    game_metadata: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize run review links and artifact references.
    
    Args:
        primary_artifact_path: Primary artifact file path
        artifact_paths: Map of artifact names to paths
        game_metadata: Game/puzzle metadata
        result: Result payload
    
    Returns:
        Normalized run review dict with consistent URLs.
    """
    review = {
        "test_results_url": f"file://{primary_artifact_path}" if primary_artifact_path else None,
        "artifact_urls": {},
    }
    
    if artifact_paths:
        for name, path in artifact_paths.items():
            review["artifact_urls"][name] = f"file://{path}" if path else None
    
    if game_metadata:
        review["puzzle_description"] = game_metadata.get("puzzle_description", "")
        review["arc_game_url"] = game_metadata.get("arc_game_url", "")
    
    if result:
        review["orchestration_status"] = result.get("orchestration_status", "unknown")
        review["failure_class"] = result.get("failure_class")
    
    return review


def normalize_orchestration_status(
    failure_class: Optional[str],
    current_status: str = "ok",
) -> str:
    """Normalize orchestration status based on failure class.
    
    Ensures status is not "ok" when failure_class="crash".
    
    Args:
        failure_class: The failure class (if any)
        current_status: Current orchestration status
    
    Returns:
        Corrected orchestration status.
    """
    if failure_class == "crash":
        return "failed"
    
    return current_status


def normalize_action_override_reason(
    reason: Optional[str],
    requested_action: Optional[str],
    selected_action: Optional[str],
    override_action: Optional[str] = None,
) -> str:
    """Normalize action override reason labels.
    
    Replaces action-specific labels like "replan_forced_action6_probe" with
    neutral names like "replan_forced_probe".
    
    Args:
        reason: Original reason label
        requested_action: The originally requested action
        selected_action: The action that was selected
        override_action: The overridden action (if any)
    
    Returns:
        Normalized reason string.
    """
    if not reason:
        return ""
    
    reason_lower = str(reason).lower()
    actual_action = str(override_action or selected_action or "")
    
    # Artifact-facing override reasons should be generic; the structured
    # action fields carry the actual action identity.
    if "action6" in reason_lower:
        reason = reason.replace("_action6_probe", "_probe").replace("_action6", "")
    
    if "action5" in reason_lower and "ACTION5" not in actual_action:
        reason = reason.replace("_action5", "")
    
    # Normalize known patterns
    reason = reason.replace("replan_forced_probe", "replan_forced_probe")
    
    return reason.strip()


def normalize_click_candidate_fields(
    main_row: Dict[str, Any],
    world_model_row: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Align click-candidate stream fields between main and world-model JSONL.
    
    Ensures matching candidate ID, coordinate, rank, source, and provenance.
    
    Args:
        main_row: Main live JSONL step row
        world_model_row: World-model live JSONL step row
    
    Returns:
        Tuple of (normalized_main_row, normalized_world_model_row).
    """
    main = dict(main_row)
    world = dict(world_model_row)
    
    # Normalize main row click candidate fields
    main_cand_id = main.get("selected_click_candidate_id")
    main_cand_rank = main.get("selected_click_candidate_rank", 0)
    main_cand_role = main.get("selected_click_candidate_role")
    
    # Normalize world model row to match main
    world["selected_click_candidate_id"] = main_cand_id
    world["selected_click_candidate_rank"] = main_cand_rank
    world["selected_click_candidate_role"] = main_cand_role
    
    # Ensure coordinates match
    world["clicked_x"] = main.get("clicked_x")
    world["clicked_y"] = main.get("clicked_y")
    
    # Align click outcome flags
    if "click_supported" in main:
        world["click_supported"] = main.get("click_supported")
    if "click_falsified" in main:
        world["click_falsified"] = main.get("click_falsified")
    
    return main, world
