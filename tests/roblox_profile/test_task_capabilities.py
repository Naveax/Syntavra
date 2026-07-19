from __future__ import annotations

import unittest
from dataclasses import replace

from _support import task_mapping
from roblox_studio.capabilities import default_capabilities
from roblox_studio.capability_graph import CapabilityGraph
from roblox_studio.errors import CapabilityError, SchemaError
from roblox_studio.profile import VALIDATORS
from roblox_studio.registries import default_engines
from roblox_studio.task_state import RobloxTaskState, migrate_task_state


class TaskStateTests(unittest.TestCase):
    def test_round_trip_and_hash(self):
        state = RobloxTaskState.from_mapping(task_mapping())
        self.assertEqual(RobloxTaskState.from_mapping(state.to_mapping()), state)
        self.assertEqual(len(state.canonical_hash()), 64)

    def test_unknown_field_rejected(self):
        value=task_mapping(); value["unknown"] = True
        with self.assertRaises(SchemaError): RobloxTaskState.from_mapping(value)

    def test_requested_must_be_authorized(self):
        with self.assertRaises(SchemaError): RobloxTaskState.from_mapping(task_mapping(requested_capabilities=["write_script"]))

    def test_oversized_intent(self):
        with self.assertRaises(SchemaError): RobloxTaskState.from_mapping(task_mapping(intent="x"*5000))

    def test_negative_budget(self):
        with self.assertRaises(SchemaError): RobloxTaskState.from_mapping(task_mapping(token_budget=-1))

    def test_migrate_v1(self):
        value=task_mapping(); value["schema_version"]=1; value.pop("project_id"); value.pop("privacy_class"); value.pop("gpu_budget"); value.pop("rollback_requirements")
        migrated=migrate_task_state(value)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertIn("project_id", migrated)


class CapabilityGraphTests(unittest.TestCase):
    def setUp(self):
        self.specs=default_capabilities(); self.engines=default_engines(self.specs)
        self.graph=CapabilityGraph(self.specs, engines=self.engines.engines, validators=VALIDATORS)

    def test_exactly_33_capabilities(self): self.assertEqual(len(self.specs), 33)
    def test_plan_includes_dependencies(self):
        plan=self.graph.plan(("write_script",), ("inspect_project","read_script","write_script"))
        self.assertEqual(plan[-1], "write_script"); self.assertIn("inspect_project", plan)
    def test_transitive_authorization_violation(self):
        with self.assertRaises(CapabilityError): self.graph.plan(("write_script",), ("write_script",))
    def test_unknown_capability(self):
        with self.assertRaises(CapabilityError): self.graph.plan(("unknown",), ("unknown",))
    def test_cycle_detected(self):
        specs=dict(self.specs); specs["inspect_project"]=replace(specs["inspect_project"], dependencies=("write_script",))
        with self.assertRaises(CapabilityError): CapabilityGraph(specs, engines=self.engines.engines, validators=VALIDATORS)
    def test_unknown_validator_detected(self):
        specs=dict(self.specs); specs["inspect_project"]=replace(specs["inspect_project"], required_validators=("missing",))
        with self.assertRaises(CapabilityError): CapabilityGraph(specs, engines=self.engines.engines, validators=VALIDATORS)


class CapabilityCoverageTests(unittest.TestCase):
    pass


def _positive(capability_id):
    def test(self):
        spec=default_capabilities()[capability_id]
        self.assertTrue(spec.execution_contract)
        self.assertTrue(spec.positive_test)
        self.assertTrue(spec.negative_test)
        self.assertTrue(spec.required_validators)
        self.assertTrue(spec.engine_requirements)
    return test


def _negative(capability_id):
    def test(self):
        specs=default_capabilities(); engines=default_engines(specs); graph=CapabilityGraph(specs, engines=engines.engines, validators=VALIDATORS)
        with self.assertRaises(CapabilityError): graph.plan((capability_id,), ())
    return test

for _capability_id in sorted(default_capabilities()):
    setattr(CapabilityCoverageTests, f"test_{_capability_id}_positive", _positive(_capability_id))
    setattr(CapabilityCoverageTests, f"test_{_capability_id}_negative", _negative(_capability_id))


if __name__ == "__main__": unittest.main()
