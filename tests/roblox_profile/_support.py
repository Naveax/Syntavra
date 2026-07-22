from __future__ import annotations

import sys
from pathlib import Path

PROFILE_PARENT = Path(__file__).resolve().parents[2] / "skills" / "syntavra" / "profiles"
if str(PROFILE_PARENT) not in sys.path:
    sys.path.insert(0, str(PROFILE_PARENT))

from roblox_studio.context_knapsack import ContextCandidate
from roblox_studio.task_state import RobloxTaskState


def task_mapping(**overrides):
    value = {
        "schema_version": 2,
        "task_id": "task-12345678",
        "session_id": "session-12345678",
        "place_id": "1001",
        "project_id": "project-1001",
        "project_fingerprint": "sha256:" + "a" * 64,
        "studio_process_id": 4242,
        "intent": "Inspect the project and read the target script",
        "requested_capabilities": ["inspect_project", "read_script"],
        "authorized_capabilities": ["inspect_project", "read_script"],
        "evidence_requirements": ["definition", "implementation"],
        "risk_class": "R1",
        "privacy_class": "PROJECT",
        "execution_constraints": ["simulated_only"],
        "token_budget": 500,
        "request_budget": 20,
        "transfer_budget": 100000,
        "gpu_budget": 0,
        "wall_time_budget": 60,
        "required_validators": ["response_schema", "capability"],
        "rollback_requirements": [],
        "completion_conditions": ["validators_pass"],
    }
    value.update(overrides)
    return value


def task(**overrides):
    return RobloxTaskState.from_mapping(task_mapping(**overrides))


def candidates():
    return (
        ContextCandidate("def", "definition", 50, 1.0, 1.0, 0.0, True, "Workspace contains ServerScriptService.Main", "memory://def"),
        ContextCandidate("impl", "implementation", 70, 1.0, 1.0, 0.0, True, "print('hello')", "memory://impl"),
        ContextCandidate("noise", "implementation", 250, 0.1, 0.2, 0.9, False, "outdated unrelated documentation", "memory://noise"),
    )
