from __future__ import annotations

import time
import unittest

from _support import PROFILE_PARENT
from roblox_studio.errors import CapabilityError
from roblox_studio.registries import ModelRegistry, ModelSpec
from roblox_studio.scheduler import select_route


def model(model_id, *, local=False, failure=0.01, latency=10, multimodal=False, privacy=True):
    return ModelSpec(model_id,"local" if local else "provider",8192,True,multimodal,True,latency,failure,0.0 if local else 0.001,0.0 if local else 0.002,0.0,"test",int(time.time()),"HEALTHY",100,("code",),local)


class SchedulerTests(unittest.TestCase):
    def test_deterministic_path(self):
        decision=select_route(deterministic=True,privacy_class="PROJECT",task_family="code",registry=ModelRegistry([]))
        self.assertEqual(decision.route,"DETERMINISTIC")
    def test_local_only(self):
        registry=ModelRegistry([model("cloud"),model("local",local=True,latency=30)])
        self.assertEqual(select_route(deterministic=False,privacy_class="LOCAL_ONLY",task_family="code",registry=registry).model_id,"local")
    def test_health_adjusted_selection(self):
        registry=ModelRegistry([model("fast-bad",failure=.3,latency=1),model("safe",failure=.01,latency=20)])
        self.assertEqual(select_route(deterministic=False,privacy_class="PROJECT",task_family="code",registry=registry).model_id,"safe")
    def test_circuit_breaker(self):
        registry=ModelRegistry([model("m")])
        registry.record_failure("m"); registry.record_failure("m"); registry.record_failure("m")
        with self.assertRaises(CapabilityError): select_route(deterministic=False,privacy_class="PROJECT",task_family="code",registry=registry)
    def test_multimodal_requirement(self):
        registry=ModelRegistry([model("text"),model("vision",multimodal=True,latency=20)])
        self.assertEqual(select_route(deterministic=False,privacy_class="PROJECT",task_family="code",registry=registry,require_multimodal=True).model_id,"vision")
    def test_stale_pricing(self):
        item=model("cloud"); item.price_retrieved_at=0
        self.assertTrue(item.pricing_is_stale(now=31*86400))


if __name__ == "__main__": unittest.main()
