"""Tool-handler bridge for SideQuests memory operations used by ARC_AGI."""


def load_tool_handlers():
    from mcp_engine.tools import (
        notify_turn,
        current_truth,
        register_plan,
        report_outcome,
        recall_procedures,
        recall_plans,
        recall_relevant_lessons,
        analogical_search,
        branch_quest,
        register_task_graph,
        get_ready_tasks,
        advance_task,
        fail_task,
        get_task_graph,
        upsert_lesson,
        get_knowledge_gaps,
    )

    return {
        "notify_turn": notify_turn,
        "current_truth": current_truth,
        "register_plan": register_plan,
        "report_outcome": report_outcome,
        "recall_procedures": recall_procedures,
        "recall_plans": recall_plans,
        "recall_relevant_lessons": recall_relevant_lessons,
        "analogical_search": analogical_search,
        "branch_quest": branch_quest,
        "register_task_graph": register_task_graph,
        "get_ready_tasks": get_ready_tasks,
        "advance_task": advance_task,
        "fail_task": fail_task,
        "get_task_graph": get_task_graph,
        "upsert_lesson": upsert_lesson,
        "get_knowledge_gaps": get_knowledge_gaps,
    }
