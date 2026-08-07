import unittest
from uuid import UUID

from arena_hero import (
    ChampionBeacon,
    CoreState,
    CoreView,
    Direction,
    PlayerState,
    PlayerStatus,
    TerrainView,
    UnitType,
    UnitView,
)
from arena_hero.actions import MoveAction, SpawnAction
from arena_hero.turn import Turn

import tactic
from dashboard import build_snapshot


class OfficialSdkCompatibilityTests(unittest.TestCase):
    def test_resource_plan_builds_with_official_sdk_models(self):
        core_id = UUID(int=1)
        worker_id = UUID(int=2)
        core = CoreView(
            kind="CORE",
            id=core_id,
            controlled=True,
            owner_username="tester",
            position=(10, 10),
            hp=5,
            shield=5,
            state=CoreState.NORMAL,
        )
        worker = UnitView(
            kind="UNIT",
            id=worker_id,
            controlled=True,
            position=(0, 0),
            hp=2,
            unit_type=UnitType.WORKER,
            cargo=0,
        )
        resources = TerrainView(kind="RESOURCE", positions=((2, 0),))
        state = PlayerState(
            status=PlayerStatus.ACTIVE,
            resources=5,
            population=1,
            champion_beacon=ChampionBeacon(position=(100, 100)),
            objects=(core, worker, resources),
            events=(),
        )
        turn = Turn(tick=1, state=state, submitter=lambda plan, key: None)

        memory = tactic.TacticMemory()
        tactic.choose_actions(turn, memory)

        action = turn.plan.unit_actions[worker_id]
        self.assertIsInstance(action, MoveAction)
        self.assertIs(action.direction, Direction.RIGHT)
        snapshot = build_snapshot(turn, memory)
        self.assertEqual(snapshot["game"]["workers"][0]["action"]["type"], "MOVE")
        self.assertEqual(snapshot["game"]["workers"][0]["resourceTarget"], [2, 0])

    def test_bootstrap_spawn_uses_official_sdk_models(self):
        core_id = UUID(int=11)
        worker_id = UUID(int=12)
        core = CoreView(
            kind="CORE",
            id=core_id,
            controlled=True,
            owner_username="tester",
            position=(0, 0),
            hp=5,
            shield=5,
            state=CoreState.NORMAL,
        )
        worker = UnitView(
            kind="UNIT",
            id=worker_id,
            controlled=True,
            position=(0, 0),
            hp=2,
            unit_type=UnitType.WORKER,
            cargo=0,
        )
        state = PlayerState(
            status=PlayerStatus.ACTIVE,
            resources=5,
            population=1,
            champion_beacon=ChampionBeacon(position=(100, 100)),
            objects=(core, worker),
            events=(),
        )
        turn = Turn(tick=2, state=state, submitter=lambda plan, key: None)

        tactic.choose_actions(turn, tactic.TacticMemory())

        self.assertIsInstance(turn.plan.unit_actions[worker_id], MoveAction)
        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertIs(turn.plan.core_action.unit_type, UnitType.WORKER)


if __name__ == "__main__":
    unittest.main()
