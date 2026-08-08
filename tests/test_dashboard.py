import json
from pathlib import Path
import tempfile
import types
import unittest
from uuid import UUID
from urllib.request import urlopen

from dashboard import DashboardRuntime, OperatorStats, build_snapshot, start_dashboard
import tactic


class FakeUnit:
    def __init__(self, position, identifier="worker-1", cargo=0):
        self.position = position
        self.id = identifier
        self.hp = 2
        self.cargo = cargo
        self.actions = [("move", "RIGHT")]


class FakeCore:
    def __init__(self, position=(0, 0)):
        self.position = position
        self.id = "core-1"
        self.hp = 5
        self.shield = 5
        self.view = types.SimpleNamespace(state="NORMAL")


class FakeTurn:
    def __init__(self, worker_position=(0, 0), core_position=(0, 0), tick=1):
        self.workers = (FakeUnit(worker_position),)
        self.vanguards = ()
        self.rangers = ()
        self.units = self.workers
        self.visible_enemies = ()
        self.resource_cells = frozenset({(2, 0)})
        self.obstacle_cells = frozenset({(1, 1)})
        self.resources = 6
        self.resource_space = 4
        self.resource_capacity = 10
        self.core = FakeCore(core_position)
        self.tick = tick
        self.events = ()
        self.state = types.SimpleNamespace(population=1, status="ACTIVE")
        self.beacon = types.SimpleNamespace(
            status=None,
            position=(20, 20),
            carrier_id=None,
        )


class DashboardTests(unittest.TestCase):
    def test_explored_cells_accumulate_between_turns(self):
        memory = tactic.TacticMemory()
        memory.observe(FakeTurn(worker_position=(0, 0)))
        first_explored = set(memory.explored_cells)

        memory.observe(FakeTurn(worker_position=(10, 0), tick=2))

        self.assertTrue(first_explored < memory.explored_cells)
        self.assertIn((10, 0), memory.visible_cells)
        self.assertIn((0, 0), memory.explored_cells)

    def test_snapshot_is_json_safe_and_contains_no_credentials(self):
        memory = tactic.TacticMemory()
        turn = FakeTurn()
        memory.observe(turn)
        memory.resource_targets["worker-1"] = (2, 0)
        stats = OperatorStats.from_values(
            {"resources_harvested": 12},
            source="local",
        )

        payload = build_snapshot(turn, memory, stats)
        encoded = json.dumps(payload)

        self.assertEqual(payload["game"]["workers"][0]["resourceTarget"], [2, 0])
        self.assertEqual(payload["game"]["resourceCapacity"], 10)
        self.assertEqual(payload["stats"]["values"]["resources_harvested"], 12)
        self.assertNotIn("ARENA_HERO_API_KEY", encoded)
        self.assertNotIn("ah_live_", encoded)

    def test_runtime_redacts_arena_api_keys_from_errors(self):
        runtime = DashboardRuntime()
        runtime.record_error(RuntimeError("bad ah_live_SECRET123 credential"))

        _, payload = runtime.current()

        self.assertNotIn("ah_live_", payload["runtime"]["lastError"])
        self.assertIn("[redacted]", payload["runtime"]["lastError"])

    def test_dashboard_serves_state_and_static_page(self):
        runtime = DashboardRuntime()
        memory = tactic.TacticMemory()
        turn = FakeTurn()
        memory.observe(turn)
        runtime.publish(build_snapshot(turn, memory))
        server, thread = start_dashboard(runtime, port=0)
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/api/state", timeout=2) as response:
                payload = json.load(response)
            with urlopen(f"{base_url}/api/runtime", timeout=2) as response:
                runtime_payload = json.load(response)
            with urlopen(base_url, timeout=2) as response:
                page = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(payload["game"]["tick"], 1)
        self.assertEqual(payload["runtime"]["status"], "connected")
        self.assertEqual(runtime_payload["runtime"]["status"], "connected")
        self.assertIsInstance(runtime_payload["serverTime"], int)
        self.assertIn("已探索地图", page)
        self.assertIn("operator-stats-section", page)
        self.assertEqual(page.count("data-stat="), 15)
        self.assertNotIn("<dialog", page)
        self.assertNotIn("statsButton", page)
        self.assertLess(page.index("operator-stats-section"), page.index("resources-section"))

    def test_dashboard_serializes_uuid_values_in_state_and_runtime(self):
        runtime = DashboardRuntime()
        runtime.publish({"event": {"actorId": UUID("12345678-1234-5678-1234-567812345678")}})
        server, thread = start_dashboard(runtime, port=0)
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/api/state", timeout=2) as response:
                payload = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(payload["event"]["actorId"], "12345678-1234-5678-1234-567812345678")

    def test_operator_stats_accumulate_events_without_double_counting(self):
        stats = OperatorStats()
        turn = FakeTurn(tick=10)
        turn.beacon.carrier_id = "worker-1"
        turn.events = (
            types.SimpleNamespace(event_id="e1", event_type="SHOT_HIT", reason_code=None, values={"damage": 1}),
            types.SimpleNamespace(event_id="e2", event_type="SWEEP_RESOLVED", reason_code=None, values={"targets_hit": 2}),
            types.SimpleNamespace(event_id="e3", event_type="UNIT_DAMAGED", reason_code="ATTACK", values={"damage": 1, "hp": 1}),
            types.SimpleNamespace(event_id="e4", event_type="HARVEST_SUCCEEDED", reason_code=None, values={"amount": 2, "source": "RESOURCE_NODE"}),
            types.SimpleNamespace(event_id="e5", event_type="DEPOSIT_SUCCEEDED", reason_code=None, values={"amount": 2}),
            types.SimpleNamespace(event_id="e6", event_type="DESTRUCTION_PARTICIPATION", reason_code="UNIT", values=None),
            types.SimpleNamespace(event_id="e7", event_type="UNIT_HEAL_SUCCEEDED", reason_code=None, values={"amount": 1}),
        )

        stats.observe(turn)
        stats.observe(turn)

        self.assertEqual(stats.values["damage_dealt"], 3)
        self.assertEqual(stats.values["damage_received"], 1)
        self.assertEqual(stats.values["resources_harvested"], 2)
        self.assertEqual(stats.values["resources_deposited"], 2)
        self.assertEqual(stats.values["unit_destruction_participations"], 1)
        self.assertEqual(stats.values["unit_hp_recovered"], 1)
        self.assertEqual(stats.values["beacon_ticks_held"], 1)
        self.assertEqual(stats.values["core_survival_ticks"], 1)

        next_turn = FakeTurn(tick=11)
        next_turn.workers = ()
        next_turn.units = ()
        next_turn.events = ()
        stats.observe(next_turn)
        self.assertEqual(stats.values["units_lost"], 1)
        self.assertEqual(stats.values["core_survival_ticks"], 2)

    def test_operator_stats_persist_between_runs(self):
        stats = OperatorStats.from_values(
            {"core_survival_ticks": 42, "resources_harvested": 9},
            source="local",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.json"
            stats.save(path)
            restored = OperatorStats.load(path)

        self.assertEqual(restored.values["core_survival_ticks"], 42)
        self.assertEqual(restored.values["resources_harvested"], 9)


if __name__ == "__main__":
    unittest.main()
