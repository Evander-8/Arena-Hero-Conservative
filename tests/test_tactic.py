import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("arena_tactic", ROOT / "tactic.py")
tactic = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tactic
spec.loader.exec_module(tactic)


class FakeDirection:
    UP = types.SimpleNamespace(delta=(0, -1), name="UP")
    DOWN = types.SimpleNamespace(delta=(0, 1), name="DOWN")
    LEFT = types.SimpleNamespace(delta=(-1, 0), name="LEFT")
    RIGHT = types.SimpleNamespace(delta=(1, 0), name="RIGHT")


class FakeUnitType:
    WORKER = "WORKER"
    VANGUARD = "VANGUARD"
    RANGER = "RANGER"


tactic.Direction = FakeDirection
tactic.UnitType = FakeUnitType
tactic.unit_cost = lambda unit_type, population: {
    "WORKER": 5,
    "VANGUARD": 10,
    "RANGER": 12,
}[unit_type]


class FakeUnit:
    def __init__(
        self,
        position,
        hp=2,
        cargo=0,
        identifier="unit",
        unit_type=None,
        controlled=True,
    ):
        self.kind = "UNIT"
        self.position = position
        self.hp = hp
        self.cargo = cargo
        self.id = identifier
        self.unit_type = unit_type
        self.controlled = controlled
        self.actions = []

    def move(self, direction):
        self.actions.append(("move", direction))

    def harvest(self):
        self.actions.append(("harvest",))

    def deposit(self):
        self.actions.append(("deposit",))

    def heal(self):
        self.actions.append(("heal",))

    def pickup_beacon(self):
        self.actions.append(("pickup",))

    def sweep(self, direction):
        self.actions.append(("sweep", direction))

    def shoot(self, target):
        self.actions.append(("shoot", target))


class FakeCore:
    def __init__(self, position=(0, 0), hp=5, shield=5):
        self.kind = "CORE"
        self.position = position
        self.hp = hp
        self.shield = shield
        self.owner_username = "owner"
        self.view = types.SimpleNamespace(state="NORMAL")
        self.actions = []

    def spawn(self, unit_type):
        self.actions.append(("spawn", unit_type))

    def heal(self):
        self.actions.append(("heal",))

    def repair_shield(self):
        self.actions.append(("repair",))

    def pickup_beacon(self):
        self.actions.append(("pickup",))


class FakeEvent:
    def __init__(self, event_type, actor_id=None, position=None, reason_code=None):
        self.event_type = event_type
        self.actor_id = actor_id
        self.position = position
        self.reason_code = reason_code


class FakeTurn:
    def __init__(
        self,
        workers=(),
        vanguards=(),
        rangers=(),
        enemies=(),
        resource_cells=(),
        obstacle_cells=(),
        resources=10,
        resource_space=10,
        core=None,
        tick=100,
        events=(),
    ):
        self.workers = tuple(workers)
        self.vanguards = tuple(vanguards)
        self.rangers = tuple(rangers)
        self.units = self.workers + self.vanguards + self.rangers
        self.visible_enemies = tuple(enemies)
        self.resource_cells = frozenset(resource_cells)
        self.obstacle_cells = frozenset(obstacle_cells)
        self.resources = resources
        self.resource_space = resource_space
        self.state = types.SimpleNamespace(population=len(self.units))
        self.core = core
        self.tick = tick
        self.events = tuple(events)
        self.beacon = types.SimpleNamespace(status=None, position=(100, 100), carrier_id=None)


class ResourceTacticTests(unittest.TestCase):
    def setUp(self):
        tactic.MEMORY = tactic.TacticMemory()

    def test_worker_harvests_visible_resource(self):
        worker = FakeUnit((1, 1), cargo=0)
        turn = FakeTurn(workers=(worker,), resource_cells={(1, 1)}, core=FakeCore((0, 0)))
        tactic.choose_actions(turn)
        self.assertEqual(worker.actions, [("harvest",)])

    def test_map_memory_persists_and_merges_without_ephemeral_assignments(self):
        memory = tactic.TacticMemory(
            known_resources={(30, 30)},
            resource_targets={"w1": (30, 30)},
            worker_harvests={"w1": 4},
            scout_ring_index={"w1": 2},
            scout_step={"w1": 7},
            scout_goal={"w1": (12, 12)},
        )
        memory.observe(
            FakeTurn(
                workers=(FakeUnit((0, 0), identifier="w1"),),
                obstacle_cells={(1, 1)},
                core=FakeCore((0, 0)),
            )
        )
        first_explored = set(memory.explored_cells)

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "map.json"
            memory.save(path)
            restored = tactic.TacticMemory.load(path)

            self.assertEqual(restored.explored_cells, first_explored)
            self.assertIn((1, 1), restored.known_obstacles)
            self.assertIn((30, 30), restored.known_resources)
            self.assertEqual(restored.worker_harvests, {"w1": 4})
            self.assertEqual(restored.resource_targets, {})
            self.assertEqual(restored.scout_ring_index, {})
            self.assertEqual(restored.scout_step, {})
            self.assertEqual(restored.scout_goal, {})

            restored.observe(
                FakeTurn(
                    workers=(FakeUnit((20, 0), identifier="w2"),),
                    obstacle_cells={(21, 1)},
                    core=FakeCore((20, 0)),
                    tick=101,
                )
            )

        self.assertTrue(first_explored < restored.explored_cells)
        self.assertIn((0, 0), restored.explored_cells)
        self.assertIn((20, 0), restored.explored_cells)
        self.assertIn((21, 1), restored.known_obstacles)

    def test_worker_deposits_cargo_at_core(self):
        worker = FakeUnit((0, 0), cargo=2)
        turn = FakeTurn(workers=(worker,), resource_space=4, core=FakeCore((0, 0)))
        tactic.choose_actions(turn)
        self.assertEqual(worker.actions, [("deposit",)])

    def test_vanguard_sweeps_adjacent_enemy_cell(self):
        vanguard = FakeUnit((0, 0), hp=4, identifier="v")
        enemy = FakeUnit((1, 0), hp=2, identifier="e")
        turn = FakeTurn(vanguards=(vanguard,), enemies=(enemy,), core=FakeCore((0, 0)))
        tactic.choose_actions(turn)
        self.assertEqual(vanguard.actions, [("sweep", FakeDirection.RIGHT)])

    def test_vanguard_retreats_toward_core_instead_of_chasing(self):
        vanguard = FakeUnit((3, 0), hp=4, identifier="v")
        enemy = FakeUnit((1, 0), hp=2, identifier="e")
        turn = FakeTurn(vanguards=(vanguard,), enemies=(enemy,), core=FakeCore((0, 0)))
        tactic.choose_actions(turn)
        self.assertEqual(vanguard.actions, [("move", FakeDirection.LEFT)])

    def test_core_heals_before_spawning(self):
        worker = FakeUnit((0, 0), cargo=0)
        core = FakeCore((0, 0), hp=4)
        turn = FakeTurn(workers=(worker,), resources=10, core=core)
        tactic.choose_actions(turn)
        self.assertEqual(core.actions, [("heal",)])

    def test_ranger_shoots_aligned_visible_enemy(self):
        ranger = FakeUnit((0, 0), hp=2, identifier="r")
        enemy = FakeUnit(
            (3, 3),
            hp=1,
            identifier="e",
            unit_type=FakeUnitType.RANGER,
            controlled=False,
        )
        turn = FakeTurn(rangers=(ranger,), enemies=(enemy,), core=FakeCore((0, 0)))
        tactic.choose_actions(turn)
        self.assertEqual(ranger.actions, [("shoot", enemy)])

    def test_respawning_turn_queues_no_actions(self):
        worker = FakeUnit((0, 0), cargo=0)
        turn = FakeTurn(workers=(worker,), resource_cells={(0, 0)}, core=None)
        tactic.choose_actions(turn)
        self.assertEqual(worker.actions, [])

    def test_full_core_moves_cargo_worker_away_to_free_spawn_slot(self):
        worker = FakeUnit((0, 0), cargo=2)
        turn = FakeTurn(workers=(worker,), resources=10, resource_space=0, core=FakeCore((0, 0)))
        tactic.choose_actions(turn)
        self.assertEqual(len(worker.actions), 1)
        self.assertEqual(worker.actions[0][0], "move")

    def test_core_prioritizes_worker_population_growth(self):
        workers = (
            FakeUnit((1, 0), cargo=0, identifier="w1"),
            FakeUnit((2, 0), cargo=0, identifier="w2"),
        )
        core = FakeCore((0, 0))
        turn = FakeTurn(workers=workers, resources=10, core=core)
        tactic.choose_actions(turn)
        self.assertEqual(core.actions, [("spawn", FakeUnitType.WORKER)])

    def test_bootstrap_spends_starting_resources_on_second_worker(self):
        worker = FakeUnit((0, 0), identifier="w1")
        core = FakeCore((0, 0))
        turn = FakeTurn(workers=(worker,), resources=5, core=core)

        tactic.choose_actions(turn)

        self.assertEqual(worker.actions[0][0], "move")
        self.assertEqual(core.actions, [("spawn", FakeUnitType.WORKER)])

    def test_four_workers_add_vanguard_before_more_workers(self):
        workers = tuple(
            FakeUnit((index + 1, 0), identifier=f"w{index}")
            for index in range(4)
        )
        core = FakeCore((0, 0))
        turn = FakeTurn(workers=workers, resources=15, core=core)

        tactic.choose_actions(turn)

        self.assertEqual(core.actions, [("spawn", FakeUnitType.VANGUARD)])

    def test_normal_production_sequence_balances_economy_and_defense(self):
        cases = (
            (4, 1, 0, FakeUnitType.WORKER, 10),
            (6, 1, 0, FakeUnitType.RANGER, 17),
            (6, 1, 1, FakeUnitType.WORKER, 10),
            (8, 1, 1, FakeUnitType.RANGER, 17),
        )
        for worker_count, vanguard_count, ranger_count, expected, resources in cases:
            with self.subTest(
                workers=worker_count,
                vanguards=vanguard_count,
                rangers=ranger_count,
            ):
                tactic.MEMORY = tactic.TacticMemory()
                workers = tuple(
                    FakeUnit((index + 10, 0), identifier=f"w{index}")
                    for index in range(worker_count)
                )
                vanguards = tuple(
                    FakeUnit(
                        (index + 10, 2),
                        hp=4,
                        identifier=f"v{index}",
                        unit_type=FakeUnitType.VANGUARD,
                    )
                    for index in range(vanguard_count)
                )
                rangers = tuple(
                    FakeUnit(
                        (index + 10, 4),
                        identifier=f"r{index}",
                        unit_type=FakeUnitType.RANGER,
                    )
                    for index in range(ranger_count)
                )
                core = FakeCore((0, 0))
                turn = FakeTurn(
                    workers=workers,
                    vanguards=vanguards,
                    rangers=rangers,
                    resources=resources,
                    core=core,
                )

                tactic.choose_actions(turn)

                self.assertEqual(core.actions, [("spawn", expected)])

    def test_guard_leaves_core_cell_so_production_can_continue(self):
        workers = tuple(
            FakeUnit((index + 2, 0), identifier=f"w{index}")
            for index in range(4)
        )
        vanguard = FakeUnit(
            (0, 0),
            hp=4,
            identifier="v1",
            unit_type=FakeUnitType.VANGUARD,
        )
        core = FakeCore((0, 0))
        turn = FakeTurn(
            workers=workers,
            vanguards=(vanguard,),
            resources=10,
            core=core,
        )

        tactic.choose_actions(turn)

        self.assertEqual(vanguard.actions[0][0], "move")
        self.assertEqual(core.actions, [("spawn", FakeUnitType.WORKER)])

    def test_core_damage_alert_repairs_shield_first(self):
        core = FakeCore((0, 0), shield=4)
        event = FakeEvent("CORE_DAMAGED", position=(0, 0))
        turn = FakeTurn(resources=1, core=core, events=(event,), tick=200)

        tactic.choose_actions(turn)

        self.assertEqual(core.actions, [("repair",)])
        self.assertGreaterEqual(tactic.MEMORY.core_alert_until, 208)

    def test_core_alert_buys_missing_ranger_with_small_reserve(self):
        workers = tuple(
            FakeUnit((index + 2, 0), identifier=f"w{index}")
            for index in range(4)
        )
        vanguard = FakeUnit(
            (1, 0),
            hp=4,
            identifier="v1",
            unit_type=FakeUnitType.VANGUARD,
        )
        core = FakeCore((0, 0))
        event = FakeEvent("CORE_DAMAGED", position=(0, 0))
        turn = FakeTurn(
            workers=workers,
            vanguards=(vanguard,),
            resources=14,
            core=core,
            events=(event,),
            tick=300,
        )

        tactic.choose_actions(turn)

        self.assertEqual(core.actions, [("spawn", FakeUnitType.RANGER)])

    def test_post_attack_recovery_buys_worker_when_defense_is_unaffordable(self):
        worker = FakeUnit((0, 0), identifier="w1")
        core = FakeCore((0, 0))
        event = FakeEvent("CORE_DAMAGED", position=(0, 0))
        turn = FakeTurn(
            workers=(worker,),
            resources=5,
            core=core,
            events=(event,),
            tick=400,
        )

        tactic.choose_actions(turn)

        self.assertEqual(core.actions, [("spawn", FakeUnitType.WORKER)])

    def test_worker_flees_away_from_core_during_alert(self):
        worker = FakeUnit((1, 0), identifier="w1")
        enemy = FakeUnit(
            (2, 0),
            hp=4,
            identifier="enemy-v",
            unit_type=FakeUnitType.VANGUARD,
            controlled=False,
        )
        turn = FakeTurn(
            workers=(worker,),
            enemies=(enemy,),
            core=FakeCore((0, 0)),
        )

        tactic.choose_actions(turn)

        self.assertEqual(worker.actions[0][0], "move")
        self.assertNotEqual(worker.actions[0][1], FakeDirection.LEFT)

    def test_early_worker_does_not_pick_up_beacon(self):
        worker = FakeUnit((0, 0), identifier="w1")
        turn = FakeTurn(workers=(worker,), resources=5, core=FakeCore((1, 0)))
        turn.beacon = types.SimpleNamespace(
            status="GROUND",
            position=(0, 0),
            carrier_id=None,
        )

        tactic.choose_actions(turn)

        self.assertNotIn(("pickup",), worker.actions)

    def test_workers_receive_unique_resource_targets(self):
        workers = (
            FakeUnit((0, 0), identifier="w1"),
            FakeUnit((0, 1), identifier="w2"),
        )
        memory = tactic.TacticMemory()
        turn = FakeTurn(
            workers=workers,
            resource_cells={(3, 0), (3, 1)},
            core=FakeCore((10, 10)),
        )
        tactic.choose_actions(turn, memory)
        self.assertEqual(len(memory.resource_targets), 2)
        self.assertEqual(len(set(memory.resource_targets.values())), 2)

    def test_worker_pathfinding_routes_around_known_obstacle(self):
        worker = FakeUnit((0, 0), identifier="w1")
        memory = tactic.TacticMemory()
        turn = FakeTurn(
            workers=(worker,),
            resource_cells={(2, 0)},
            obstacle_cells={(1, 0)},
            core=FakeCore((10, 10)),
        )
        tactic.choose_actions(turn, memory)
        self.assertEqual(worker.actions[0][0], "move")
        self.assertNotEqual(worker.actions[0][1], FakeDirection.RIGHT)

    def test_workers_use_different_scout_sectors(self):
        workers = (
            FakeUnit((0, 0), identifier="w1"),
            FakeUnit((0, 1), identifier="w2"),
        )
        memory = tactic.TacticMemory()
        turn = FakeTurn(workers=workers, core=FakeCore((0, 0)))
        tactic.choose_actions(turn, memory)
        self.assertEqual(len(memory.scout_goal), 2)
        self.assertEqual(len(set(memory.scout_goal.values())), 2)

    def test_scout_does_not_immediately_step_back_to_previous_cell(self):
        worker = FakeUnit((1, 0), identifier="w1")
        memory = tactic.TacticMemory(
            scout_goal={"w1": (-10, 0)},
            scout_distance={"w1": 11},
            last_positions={"w1": (0, 0)},
        )
        turn = FakeTurn(workers=(worker,), core=FakeCore((20, 20)))

        tactic.choose_actions(turn, memory)

        self.assertEqual(worker.actions[0][0], "move")
        self.assertNotEqual(worker.actions[0][1], FakeDirection.LEFT)

    def test_resource_memory_persists_outside_current_vision(self):
        worker = FakeUnit((0, 0), identifier="w1")
        memory = tactic.TacticMemory(known_resources={(5, 0)})
        turn = FakeTurn(workers=(worker,), core=FakeCore((20, 0)))
        tactic.choose_actions(turn, memory)
        self.assertIn((5, 0), memory.known_resources)
        self.assertEqual(memory.resource_targets["w1"], (5, 0))

    def test_resource_memory_is_removed_when_visible_cell_is_empty(self):
        memory = tactic.TacticMemory(known_resources={(5, 0)})
        turn = FakeTurn(core=FakeCore((0, 0)))
        tactic.choose_actions(turn, memory)
        self.assertNotIn((5, 0), memory.known_resources)

    def test_worker_on_resource_overrides_older_target(self):
        worker = FakeUnit((0, 0), identifier="w1")
        memory = tactic.TacticMemory(
            known_resources={(5, 0)},
            resource_targets={"w1": (5, 0)},
        )
        turn = FakeTurn(
            workers=(worker,),
            resource_cells={(0, 0)},
            core=FakeCore((20, 0)),
        )
        tactic.choose_actions(turn, memory)
        self.assertEqual(memory.resource_targets["w1"], (0, 0))
        self.assertEqual(worker.actions, [("harvest",)])

    def test_new_resource_seen_by_returning_worker_reassigns_idle_worker(self):
        returning = FakeUnit((0, 0), cargo=2, identifier="returning")
        idle = FakeUnit((5, 0), cargo=0, identifier="idle")
        memory = tactic.TacticMemory(
            known_resources={(20, 0)},
            resource_targets={"idle": (20, 0)},
        )
        turn = FakeTurn(
            workers=(returning, idle),
            resource_cells={(6, 0)},
            core=FakeCore((0, 0)),
        )

        tactic.choose_actions(turn, memory)

        self.assertEqual(memory.resource_targets, {"idle": (6, 0)})
        self.assertEqual(idle.actions, [("move", FakeDirection.RIGHT)])


if __name__ == "__main__":
    unittest.main()
