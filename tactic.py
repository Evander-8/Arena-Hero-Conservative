"""Resource-first, low-conflict starter tactic for Arena Hero.

The decision function is intentionally separate from the SDK connection so it
can be tested with small state doubles before a live credential is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import json
import os
import threading
from getpass import getpass
from pathlib import Path
from typing import Any, Iterable

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # Allows offline unit tests before dependencies install.
    load_dotenv = None  # type: ignore[assignment]

from dashboard import (
    DashboardRuntime,
    OperatorStats,
    build_snapshot,
    fetch_lifetime_stats,
    start_dashboard,
)

try:
    from arena_hero import APIError, ArenaHeroClient, Direction, UnitType, unit_cost
except ModuleNotFoundError:  # Allows offline unit tests before dependencies install.
    APIError = None  # type: ignore[assignment,misc]
    ArenaHeroClient = None  # type: ignore[assignment,misc]
    Direction = None  # type: ignore[assignment,misc]
    UnitType = None  # type: ignore[assignment,misc]
    unit_cost = None  # type: ignore[assignment,misc]


MAX_CORE_HP = 5
MAX_WORKER_HP = 2
MAX_VANGUARD_HP = 4
MAX_RANGER_HP = 2
COMBAT_THREAT_RADIUS = 5
WORKER_THREAT_RADIUS = 2
RESOURCE_RESERVE = 5
BOOTSTRAP_WORKER_TARGET = 4
WORKER_TARGET = 14
FINAL_WORKER_TARGET = 14
VANGUARD_TARGET = 2
RANGER_TARGET = 2
CORE_ALERT_TICKS = 8
CORE_GUARD_RADIUS = 2
CORE_VISION_RADIUS = 5
EXPEDITION_FAN_BASE_RADIUS = 26
EXPEDITION_FAN_RADIUS_STEP = 8
EXPEDITION_FAN_SPACING = 4
EXPEDITION_RALLY_RADIUS = 3
RESOURCE_MEMORY_RADIUS = 36
RESOURCE_SCOUT_RADII = (12, 19, 26, 32, 26, 19)
RESOURCE_SCOUT_WAYPOINT_STEP = 7
PATHFINDING_EXPANSIONS = 1500

Position = tuple[int, int]


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _position(obj: Any) -> tuple[int, int]:
    x, y = obj.position
    return int(x), int(y)


def _manhattan(first: tuple[int, int], second: tuple[int, int]) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def _chebyshev(first: Position, second: Position) -> int:
    return max(abs(first[0] - second[0]), abs(first[1] - second[1]))


def _directions() -> tuple[Any, ...]:
    if Direction is None:
        return ()
    return tuple(getattr(Direction, name) for name in ("UP", "DOWN", "LEFT", "RIGHT"))


def _delta(direction: Any) -> tuple[int, int]:
    value = getattr(direction, "delta", (0, 0))
    value = value() if callable(value) else value
    return int(value[0]), int(value[1])


def _direction_between(source: Position, destination: Position) -> Any | None:
    step = (destination[0] - source[0], destination[1] - source[1])
    return next(
        (direction for direction in _directions() if _delta(direction) == step),
        None,
    )


def _axis_steps(start: int, end: int, step: int) -> tuple[int, ...]:
    direction = 1 if end >= start else -1
    values = [start]
    while abs(end - values[-1]) > step:
        values.append(values[-1] + direction * step)
    if values[-1] != end:
        values.append(end)
    return tuple(values)


def _square_ring_waypoints(core: Position, radius: int) -> tuple[Position, ...]:
    left, right = core[0] - radius, core[0] + radius
    top, bottom = core[1] - radius, core[1] + radius
    horizontal = _axis_steps(left, right, RESOURCE_SCOUT_WAYPOINT_STEP)
    vertical = _axis_steps(top, bottom, RESOURCE_SCOUT_WAYPOINT_STEP)
    horizontal_back = _axis_steps(right, left, RESOURCE_SCOUT_WAYPOINT_STEP)
    vertical_back = _axis_steps(bottom, top, RESOURCE_SCOUT_WAYPOINT_STEP)
    return tuple(
        [(x, top) for x in horizontal]
        + [(right, y) for y in vertical[1:]]
        + [(x, bottom) for x in horizontal_back[1:]]
        + [(left, y) for y in vertical_back[1:-1]]
    )


def _first_path_step(
    start: Position,
    goal: Position,
    obstacles: set[Position],
) -> Position | None:
    if start == goal:
        return start

    frontier: list[tuple[int, int, Position]] = [(_manhattan(start, goal), 0, start)]
    parent: dict[Position, Position | None] = {start: None}
    best_cost: dict[Position, int] = {start: 0}
    expansions = 0

    while frontier and expansions < PATHFINDING_EXPANSIONS:
        _, cost, current = heapq.heappop(frontier)
        if cost != best_cost.get(current):
            continue
        expansions += 1
        if current == goal:
            break
        for direction in _directions():
            dx, dy = _delta(direction)
            nxt = (current[0] + dx, current[1] + dy)
            if nxt in obstacles:
                continue
            new_cost = cost + 1
            if new_cost >= best_cost.get(nxt, 2**63 - 1):
                continue
            best_cost[nxt] = new_cost
            parent[nxt] = current
            heapq.heappush(
                frontier,
                (new_cost + _manhattan(nxt, goal), new_cost, nxt),
            )

    if goal not in parent:
        return None
    cursor = goal
    while parent[cursor] != start:
        previous = parent[cursor]
        if previous is None:
            return None
        cursor = previous
    return cursor


def _direction_for_step(
    source: tuple[int, int],
    target: tuple[int, int],
    obstacles: set[tuple[int, int]],
) -> Any | None:
    """Choose a deterministic cardinal step that does not enter an obstacle."""

    dx = target[0] - source[0]
    dy = target[1] - source[1]
    preferred: list[tuple[int, int]] = []
    if abs(dx) >= abs(dy) and dx:
        preferred.append((1 if dx > 0 else -1, 0))
    if dy:
        preferred.append((0, 1 if dy > 0 else -1))
    if dx and abs(dx) < abs(dy):
        preferred.append((1 if dx > 0 else -1, 0))

    candidates = preferred + [_delta(direction) for direction in _directions()]
    seen: set[tuple[int, int]] = set()
    for step in candidates:
        if step in seen or step == (0, 0):
            continue
        seen.add(step)
        destination = (source[0] + step[0], source[1] + step[1])
        if destination in obstacles:
            continue
        for direction in _directions():
            if _delta(direction) == step:
                return direction
    return None


def _queue_move(
    unit: Any,
    direction: Any,
    reserved: set[Position] | None = None,
) -> bool:
    source = _position(unit)
    dx, dy = _delta(direction)
    destination = (source[0] + dx, source[1] + dy)
    if reserved is not None and destination in reserved:
        return False
    unit.move(direction)
    if reserved is not None:
        reserved.add(destination)
    return True


def _move_toward(
    unit: Any,
    target: tuple[int, int],
    obstacles: set[tuple[int, int]],
    reserved: set[Position] | None = None,
) -> bool:
    source = _position(unit)
    active_obstacles = obstacles | (reserved or set())
    destination = _first_path_step(source, target, active_obstacles)
    direction = (
        _direction_between(source, destination)
        if destination is not None and destination != source
        else None
    )
    if direction is None:
        direction = _direction_for_step(source, target, active_obstacles)
    if direction is None:
        return False
    return _queue_move(unit, direction, reserved)


def _explore(
    unit: Any,
    turn: Any,
    obstacles: set[tuple[int, int]],
    reserved: set[Position] | None = None,
) -> bool:
    """Take a deterministic low-commitment step when no resource is visible."""

    directions = _directions()
    if not directions:
        return False
    seed = sum(ord(char) for char in str(getattr(unit, "id", "")))
    phase = int(getattr(turn, "tick", 0)) // 4
    for offset in range(len(directions)):
        direction = directions[(seed + phase + offset) % len(directions)]
        dx, dy = _delta(direction)
        destination = (_position(unit)[0] + dx, _position(unit)[1] + dy)
        if destination not in obstacles and (
            reserved is None or destination not in reserved
        ):
            return _queue_move(unit, direction, reserved)
    return False


def _visible_threats(turn: Any, origin: tuple[int, int], radius: int) -> list[Any]:
    return sorted(
        (
            enemy
            for enemy in turn.visible_enemies
            if _manhattan(_position(enemy), origin) <= radius
        ),
        key=lambda enemy: (
            _manhattan(_position(enemy), origin),
            int(getattr(enemy, "hp", 0)),
            str(getattr(enemy, "id", "")),
        ),
    )


def _unit_type_value(obj: Any) -> str | None:
    value = _enum_value(getattr(obj, "unit_type", None))
    return str(value) if value is not None else None


def _is_enemy_core(obj: Any) -> bool:
    return getattr(obj, "kind", None) == "CORE" or hasattr(obj, "owner_username")


def _can_attack_cell_now(
    enemy: Any,
    target: Position,
    obstacles: set[Position],
) -> bool:
    if _is_enemy_core(enemy):
        return False
    enemy_type = _unit_type_value(enemy)
    enemy_position = _position(enemy)
    if enemy_type == "WORKER":
        return False
    if enemy_type == "VANGUARD":
        return _manhattan(enemy_position, target) == 1
    if enemy_type == "RANGER":
        return _aligned_shot(enemy_position, target, obstacles)
    # Unknown test doubles and future combat types are handled conservatively.
    return _manhattan(enemy_position, target) <= COMBAT_THREAT_RADIUS


def _core_threats(
    turn: Any,
    obstacles: set[Position],
    *,
    immediate_only: bool,
) -> list[Any]:
    core = turn.core
    if core is None:
        return []
    core_position = _position(core)
    threats = []
    for enemy in turn.visible_enemies:
        if _can_attack_cell_now(enemy, core_position, obstacles):
            threats.append(enemy)
            continue
        if immediate_only or _is_enemy_core(enemy):
            continue
        enemy_type = _unit_type_value(enemy)
        distance = _manhattan(_position(enemy), core_position)
        if enemy_type == "WORKER":
            continue
        if enemy_type == "VANGUARD" and distance <= 2:
            threats.append(enemy)
        elif enemy_type == "RANGER" and _chebyshev(_position(enemy), core_position) <= 4:
            threats.append(enemy)
        elif enemy_type is None and distance <= 4:
            threats.append(enemy)
    return sorted(
        threats,
        key=lambda enemy: (
            _manhattan(_position(enemy), core_position),
            int(getattr(enemy, "hp", 0)),
            _object_key(enemy),
        ),
    )


def _move_away(
    unit: Any,
    threats: Iterable[Any],
    obstacles: set[Position],
    core_position: Position | None,
    core_alert: bool,
    reserved: set[Position] | None = None,
) -> bool:
    origin = _position(unit)
    threat_positions = tuple(_position(threat) for threat in threats)
    candidates: list[tuple[tuple[int, int, int], Any]] = []
    for order, direction in enumerate(_directions()):
        dx, dy = _delta(direction)
        destination = (origin[0] + dx, origin[1] + dy)
        if destination in obstacles or (
            reserved is not None and destination in reserved
        ):
            continue
        nearest_threat = min(
            (_manhattan(destination, position) for position in threat_positions),
            default=0,
        )
        core_distance = (
            _manhattan(destination, core_position) if core_position is not None else 0
        )
        core_score = core_distance if core_alert else -core_distance
        candidates.append(((nearest_threat, core_score, -order), direction))
    if not candidates:
        return False
    return _queue_move(
        unit,
        max(candidates, key=lambda item: item[0])[1],
        reserved,
    )


def _supercover_line(start: Position, target: Position) -> tuple[Position, ...]:
    x, y = start
    target_x, target_y = target
    delta_x = abs(target_x - x)
    delta_y = abs(target_y - y)
    step_x = 1 if target_x > x else -1
    step_y = 1 if target_y > y else -1
    covered = [(x, y)]
    progressed_x = 0
    progressed_y = 0

    while progressed_x < delta_x or progressed_y < delta_y:
        horizontal = (1 + 2 * progressed_x) * delta_y
        vertical = (1 + 2 * progressed_y) * delta_x
        if horizontal == vertical:
            previous_x, previous_y = x, y
            x += step_x
            progressed_x += 1
            covered.append((x, y))
            covered.append((previous_x, previous_y + step_y))
            y += step_y
            progressed_y += 1
            covered.append((x, y))
        elif horizontal < vertical:
            x += step_x
            progressed_x += 1
            covered.append((x, y))
        else:
            y += step_y
            progressed_y += 1
            covered.append((x, y))
    return tuple(covered)


def _visible_from(
    source: Position,
    target: Position,
    radius: int,
    obstacles: set[Position],
) -> bool:
    if _manhattan(source, target) > radius:
        return False
    return not any(cell in obstacles for cell in _supercover_line(source, target)[1:-1])


def _friendly_vision_sources(turn: Any) -> tuple[tuple[Position, int], ...]:
    sources: list[tuple[Position, int]] = []
    if turn.core is not None:
        sources.append((_position(turn.core), 5))
    sources.extend((_position(worker), 3) for worker in turn.workers)
    sources.extend((_position(vanguard), 4) for vanguard in turn.vanguards)
    sources.extend((_position(ranger), 5) for ranger in turn.rangers)
    return tuple(sources)


def _object_key(obj: Any) -> str:
    return str(getattr(obj, "id", ""))


def _position_set(value: Any) -> set[Position]:
    if not isinstance(value, list):
        return set()
    positions: set[Position] = set()
    for item in value:
        if (
            isinstance(item, list)
            and len(item) == 2
            and type(item[0]) is int
            and type(item[1]) is int
        ):
            positions.add((item[0], item[1]))
    return positions


def _json_positions(positions: Iterable[Position]) -> list[list[int]]:
    return [[x, y] for x, y in sorted(positions)]


def _nonnegative_int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): count
        for key, count in value.items()
        if type(count) is int and count >= 0
    }


@dataclass
class TacticMemory:
    known_obstacles: set[Position] = field(default_factory=set)
    known_resources: set[Position] = field(default_factory=set)
    explored_cells: set[Position] = field(default_factory=set)
    visible_cells: set[Position] = field(default_factory=set)
    resource_targets: dict[str, Position] = field(default_factory=dict)
    worker_harvests: dict[str, int] = field(default_factory=dict)
    scout_ring_index: dict[str, int] = field(default_factory=dict)
    scout_step: dict[str, int] = field(default_factory=dict)
    scout_goal: dict[str, Position] = field(default_factory=dict)
    scout_distance: dict[str, int] = field(default_factory=dict)
    scout_stall: dict[str, int] = field(default_factory=dict)
    expedition_goal: dict[str, Position] = field(default_factory=dict)
    expedition_distance: dict[str, int] = field(default_factory=dict)
    expedition_stall: dict[str, int] = field(default_factory=dict)
    expedition_wave: int = 0
    expedition_formation: tuple[str, ...] = ()
    last_positions: dict[str, Position] = field(default_factory=dict)
    previous_positions: dict[str, Position] = field(default_factory=dict)
    core_alert_until: int = 0
    last_core_position: Position | None = None

    @classmethod
    def load(cls, path: Path) -> "TacticMemory":
        """Restore durable map knowledge without reviving stale unit assignments."""

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return cls()
            return cls(
                known_obstacles=_position_set(payload.get("knownObstacles")),
                known_resources=_position_set(payload.get("knownResources")),
                explored_cells=_position_set(payload.get("exploredCells")),
                worker_harvests=_nonnegative_int_map(payload.get("workerHarvests")),
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path) -> None:
        """Atomically persist durable exploration state for the next process."""

        payload = {
            "version": 1,
            "knownObstacles": _json_positions(self.known_obstacles),
            "knownResources": _json_positions(self.known_resources),
            "exploredCells": _json_positions(self.explored_cells),
            "workerHarvests": dict(sorted(self.worker_harvests.items())),
        }
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)

    def observe(self, turn: Any) -> None:
        self.known_obstacles.update(tuple(position) for position in turn.obstacle_cells)
        visible_resources = {tuple(position) for position in turn.resource_cells}
        live_worker_ids = {_object_key(worker) for worker in turn.workers}
        live_unit_ids = {_object_key(unit) for unit in turn.units}

        for unit in turn.units:
            unit_id = _object_key(unit)
            position = _position(unit)
            previous = self.last_positions.get(unit_id)
            if previous is not None and previous != position:
                self.previous_positions[unit_id] = previous
            elif previous == position:
                self.previous_positions.pop(unit_id, None)
            self.last_positions[unit_id] = position
        for unit_id in tuple(self.last_positions):
            if unit_id not in live_unit_ids:
                self.last_positions.pop(unit_id, None)
                self.previous_positions.pop(unit_id, None)

        self.visible_cells = set()
        for source, radius in _friendly_vision_sources(turn):
            for x in range(source[0] - radius, source[0] + radius + 1):
                remaining = radius - abs(x - source[0])
                for y in range(source[1] - remaining, source[1] + remaining + 1):
                    target = (x, y)
                    if _visible_from(source, target, radius, self.known_obstacles):
                        self.visible_cells.add(target)
        self.explored_cells.update(self.visible_cells)

        if turn.core is not None:
            core_position = _position(turn.core)
            if (
                self.last_core_position is not None
                and core_position != self.last_core_position
            ):
                self.scout_ring_index.clear()
                self.scout_step.clear()
                self.scout_goal.clear()
                self.scout_distance.clear()
                self.scout_stall.clear()
                self.expedition_goal.clear()
                self.expedition_distance.clear()
                self.expedition_stall.clear()
                self.expedition_wave = 0
                self.expedition_formation = ()
            self.last_core_position = core_position
            self.known_resources = {
                resource
                for resource in self.known_resources
                if _chebyshev(core_position, resource) <= RESOURCE_MEMORY_RADIUS
            }

        for event in getattr(turn, "events", ()):
            event_type = _enum_value(getattr(event, "event_type", ""))
            if event_type == "CORE_DAMAGED":
                self.core_alert_until = max(
                    self.core_alert_until,
                    int(getattr(turn, "tick", 0)) + CORE_ALERT_TICKS,
                )
            if event_type not in {"HARVEST_SUCCEEDED", "HARVEST_FAILED"}:
                continue
            actor_key = str(getattr(event, "actor_id", ""))
            target = self.resource_targets.pop(actor_key, None)
            position = getattr(event, "position", None)
            event_position = tuple(position) if position is not None else target
            if event_type == "HARVEST_SUCCEEDED":
                self.worker_harvests[actor_key] = self.worker_harvests.get(actor_key, 0) + 1
            if event_type == "HARVEST_SUCCEEDED" and event_position is not None:
                self.known_resources.discard(event_position)
            if (
                event_type == "HARVEST_FAILED"
                and _enum_value(getattr(event, "reason_code", ""))
                in {"RESOURCE_DEPLETED", "NOT_RESOURCE_CELL"}
                and event_position is not None
            ):
                self.known_resources.discard(event_position)

        self.known_resources.update(visible_resources)
        sources = _friendly_vision_sources(turn)
        for resource in tuple(self.known_resources - visible_resources):
            if any(
                _visible_from(source, resource, radius, self.known_obstacles)
                for source, radius in sources
            ):
                self.known_resources.discard(resource)

        for worker_id, target in tuple(self.resource_targets.items()):
            if worker_id not in live_worker_ids or target not in self.known_resources:
                self.resource_targets.pop(worker_id, None)

        for worker_id, goal in tuple(self.scout_goal.items()):
            if worker_id not in live_worker_ids:
                self.scout_goal.pop(worker_id, None)
                self.scout_distance.pop(worker_id, None)
                self.scout_stall.pop(worker_id, None)
                continue
            worker = next(
                worker for worker in turn.workers if _object_key(worker) == worker_id
            )
            distance = _manhattan(_position(worker), goal)
            previous_distance = self.scout_distance.get(worker_id)
            stalled = self.scout_stall.get(worker_id, 0)
            if previous_distance is not None and distance >= previous_distance:
                stalled += 1
            else:
                stalled = 0
            if goal in self.visible_cells or stalled >= 3:
                self.scout_goal.pop(worker_id, None)
                self.scout_distance.pop(worker_id, None)
                self.scout_stall.pop(worker_id, None)
            else:
                self.scout_distance[worker_id] = distance
                self.scout_stall[worker_id] = stalled

        for unit_id, goal in tuple(self.expedition_goal.items()):
            if unit_id not in live_unit_ids:
                self.expedition_goal.pop(unit_id, None)
                self.expedition_distance.pop(unit_id, None)
                self.expedition_stall.pop(unit_id, None)
                continue
            unit = next(unit for unit in turn.units if _object_key(unit) == unit_id)
            distance = _manhattan(_position(unit), goal)
            previous_distance = self.expedition_distance.get(unit_id)
            stalled = self.expedition_stall.get(unit_id, 0)
            if previous_distance is not None and distance >= previous_distance:
                stalled += 1
            else:
                stalled = 0
            if goal in self.known_obstacles or distance <= 1 or stalled >= 4:
                self.expedition_goal.pop(unit_id, None)
                self.expedition_distance.pop(unit_id, None)
                self.expedition_stall.pop(unit_id, None)
            else:
                self.expedition_distance[unit_id] = distance
                self.expedition_stall[unit_id] = stalled

    def core_alerted(self, tick: int) -> bool:
        return tick <= self.core_alert_until

    def assign_resources(self, workers: Iterable[Any]) -> dict[str, Position]:
        workers = tuple(sorted(workers, key=_object_key))
        assigned_resources: set[Position] = set()
        assignments: dict[str, Position] = {}

        # A Worker already standing on a resource gets first claim, preventing
        # another Worker from contesting the same cell by UUID resolution.
        for worker in workers:
            worker_id = _object_key(worker)
            position = _position(worker)
            if (
                int(getattr(worker, "cargo", 0) or 0) == 0
                and position in self.known_resources
                and position not in assigned_resources
            ):
                assignments[worker_id] = position
                assigned_resources.add(position)

        # Rebuild the matching every Turn. A newly discovered nearby resource
        # can therefore replace an older, farther target instead of waiting for
        # that sticky assignment to complete.
        idle_workers = [
            worker
            for worker in workers
            if int(getattr(worker, "cargo", 0) or 0) == 0
            and _object_key(worker) not in assignments
        ]
        available_resources = self.known_resources - assigned_resources
        candidates = sorted(
            (
                (_manhattan(_position(worker), resource), _object_key(worker), resource)
                for worker in idle_workers
                for resource in available_resources
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
        claimed_workers: set[str] = set()
        for _, worker_id, resource in candidates:
            if worker_id in claimed_workers or resource in assigned_resources:
                continue
            assignments[worker_id] = resource
            claimed_workers.add(worker_id)
            assigned_resources.add(resource)
        self.resource_targets = assignments
        return assignments

    def scout_goal_for(
        self,
        worker: Any,
        worker_index: int,
        worker_count: int,
        core: Position,
    ) -> Position:
        worker_id = _object_key(worker)
        goal = self.scout_goal.get(worker_id)
        if (
            goal is not None
            and goal not in self.known_obstacles
            and _manhattan(_position(worker), goal) > 1
        ):
            return goal

        # Start workers on different rings so a new fleet fans out from the
        # Core instead of forming one traffic column around the first ring.
        ring_index = self.scout_ring_index.get(
            worker_id,
            worker_index % len(RESOURCE_SCOUT_RADII),
        )
        step = self.scout_step.get(worker_id, 0)
        candidate_count = sum(
            len(_square_ring_waypoints((0, 0), radius))
            for radius in RESOURCE_SCOUT_RADII
        )
        for _ in range(candidate_count):
            radius = RESOURCE_SCOUT_RADII[ring_index % len(RESOURCE_SCOUT_RADII)]
            route = _square_ring_waypoints(core, radius)
            sector_offset = (worker_index * len(route)) // max(1, worker_count)
            candidate = route[(step + sector_offset) % len(route)]
            step += 1
            if step >= len(route):
                step = 0
                ring_index = (ring_index + 1) % len(RESOURCE_SCOUT_RADII)
            if candidate in self.known_obstacles:
                continue
            self.scout_ring_index[worker_id] = ring_index
            self.scout_step[worker_id] = step
            self.scout_goal[worker_id] = candidate
            self.scout_distance[worker_id] = _manhattan(_position(worker), candidate)
            self.scout_stall[worker_id] = 0
            return candidate

        self.scout_goal[worker_id] = core
        self.scout_distance[worker_id] = _manhattan(_position(worker), core)
        self.scout_stall[worker_id] = 0
        return core

    def assign_expedition_goals(
        self,
        units: Iterable[Any],
        core: Position,
    ) -> dict[str, Position]:
        """Assign a gap-free fan front and rebalance it when membership changes."""

        active = tuple(sorted(units, key=_object_key))
        signature = tuple(_object_key(unit) for unit in active)
        if not active:
            self.expedition_goal.clear()
            self.expedition_distance.clear()
            self.expedition_stall.clear()
            self.expedition_formation = ()
            return {}

        formation_changed = signature != self.expedition_formation
        goals_complete = all(unit_id in self.expedition_goal for unit_id in signature)
        wave_complete = goals_complete and all(
            self.expedition_goal[unit_id] in self.visible_cells
            or _manhattan(_position(unit), self.expedition_goal[unit_id]) <= 1
            for unit, unit_id in zip(active, signature)
        )
        if formation_changed or not goals_complete or wave_complete:
            if not formation_changed and (not goals_complete or wave_complete):
                self.expedition_wave += 1
            self.expedition_goal.clear()
            self.expedition_distance.clear()
            self.expedition_stall.clear()
            self.expedition_formation = signature

            directions = (
                (1, 0),
                (1, 1),
                (0, 1),
                (-1, 1),
                (-1, 0),
                (-1, -1),
                (0, -1),
                (1, -1),
            )
            direction = directions[self.expedition_wave % len(directions)]
            perpendicular = (-direction[1], direction[0])
            radius = EXPEDITION_FAN_BASE_RADIUS + (
                self.expedition_wave // len(directions)
            ) * EXPEDITION_FAN_RADIUS_STEP

            for index, unit in enumerate(active):
                unit_id = _object_key(unit)
                lateral = (
                    2 * index - (len(active) - 1)
                ) * (EXPEDITION_FAN_SPACING // 2)
                candidate = (
                    core[0] + direction[0] * radius + perpendicular[0] * lateral,
                    core[1] + direction[1] * radius + perpendicular[1] * lateral,
                )
                if candidate in self.known_obstacles:
                    for shift in (1, -1, 2, -2, 3, -3):
                        shifted = (
                            candidate[0] + perpendicular[0] * shift,
                            candidate[1] + perpendicular[1] * shift,
                        )
                        if shifted not in self.known_obstacles:
                            candidate = shifted
                            break
                self.expedition_goal[unit_id] = candidate
                self.expedition_distance[unit_id] = _manhattan(
                    _position(unit), candidate
                )
                self.expedition_stall[unit_id] = 0

        return {
            unit_id: self.expedition_goal[unit_id]
            for unit_id in signature
            if unit_id in self.expedition_goal
        }

    def expedition_goal_for(self, unit: Any, core: Position) -> Position:
        """Compatibility wrapper for callers assigning one expedition unit."""

        return self.assign_expedition_goals((unit,), core).get(
            _object_key(unit), core
        )


MEMORY = TacticMemory()


def _same_cell(first: Any, second: Any) -> bool:
    return _position(first) == _position(second)


def _core_is_stationary(core: Any) -> bool:
    return _enum_value(getattr(core.view, "state", "NORMAL")) == "NORMAL"


def _beacon_is_ground_at(turn: Any, position: tuple[int, int]) -> bool:
    beacon = getattr(turn, "beacon", None)
    return (
        beacon is not None
        and _enum_value(getattr(beacon, "status", None)) == "GROUND"
        and tuple(beacon.position) == position
    )


def _beacon_is_carried_by(turn: Any, obj: Any) -> bool:
    beacon = getattr(turn, "beacon", None)
    carrier_id = getattr(beacon, "carrier_id", None) if beacon is not None else None
    return (
        _enum_value(getattr(beacon, "status", None)) == "CARRIED"
        and carrier_id is not None
        and str(carrier_id) == str(getattr(obj, "id", ""))
    )


def _aligned_shot(
    origin: tuple[int, int],
    target: tuple[int, int],
    obstacles: set[tuple[int, int]],
) -> bool:
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    distance = max(abs(dx), abs(dy))
    aligned = (dx == 0 or dy == 0 or abs(dx) == abs(dy)) and distance in (1, 2, 3)
    if not aligned:
        return False
    step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
    step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
    return all(
        (origin[0] + step_x * index, origin[1] + step_y * index) not in obstacles
        for index in range(1, distance)
    )


def _beacon_ready(turn: Any) -> bool:
    core = turn.core
    return (
        core is not None
        and len(turn.workers) >= WORKER_TARGET
        and len(turn.vanguards) >= VANGUARD_TARGET
        and len(turn.rangers) >= RANGER_TARGET
        and core.hp == MAX_CORE_HP
        and core.shield >= 5
        and turn.resources >= RESOURCE_RESERVE * 2
        and not turn.visible_enemies
    )


def _queue_beacon_pickup(turn: Any, obj: Any) -> bool:
    if _beacon_ready(turn) and _beacon_is_ground_at(turn, _position(obj)):
        obj.pickup_beacon()
        return True
    return False


def _guard_candidates(core: Position, radius: int) -> tuple[Position, ...]:
    x, y = core
    return (
        (x + radius, y),
        (x, y + radius),
        (x - radius, y),
        (x, y - radius),
        (x + radius, y + radius),
        (x - radius, y + radius),
        (x - radius, y - radius),
        (x + radius, y - radius),
    )


def _guard_goal(
    unit: Any,
    core: Position,
    radius: int,
    blocked: set[Position],
    claimed: set[Position],
    phase: int = 0,
) -> Position:
    candidates = _guard_candidates(core, radius)
    seed = sum(ord(char) for char in _object_key(unit)) + phase
    ordered = candidates[seed % len(candidates) :] + candidates[: seed % len(candidates)]
    for candidate in ordered:
        if candidate not in blocked and candidate not in claimed:
            claimed.add(candidate)
            return candidate
    return _position(unit)


def _patrol_step(
    unit: Any,
    core: Position,
    radius: int,
    obstacles: set[Position],
    phase: int = 0,
    reserved: set[Position] | None = None,
) -> bool:
    """Keep a combat unit moving around a local ring when no enemy is visible."""

    origin = _position(unit)
    candidates = _guard_candidates(core, radius)
    if not candidates:
        return False
    seed = sum(ord(char) for char in _object_key(unit)) + phase
    ordered = candidates[seed % len(candidates) :] + candidates[: seed % len(candidates)]
    if origin in candidates:
        index = candidates.index(origin)
        ordered = candidates[index + 1 :] + candidates[: index + 1]
    for candidate in ordered:
        if candidate == origin or candidate in obstacles:
            continue
        if _move_toward(unit, candidate, obstacles, reserved):
            return True
    # A blocked ring should still produce a legal in-range step when possible.
    for direction in _directions():
        dx, dy = _delta(direction)
        destination = (origin[0] + dx, origin[1] + dy)
        if (
            destination in obstacles
            or (reserved is not None and destination in reserved)
            or _manhattan(destination, core) > radius + 1
        ):
            continue
        return _queue_move(unit, direction, reserved)
    return False


def _combat_units(turn: Any) -> tuple[Any, ...]:
    return tuple(sorted((*turn.vanguards, *turn.rangers), key=_object_key))


def _guard_count(unit_count: int) -> int:
    """Keep one third of each combat type at the Core, rounded up."""

    return 0 if unit_count <= 0 else max(1, (unit_count + 2) // 3)


def _split_guard_force(units: Iterable[Any]) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    ordered = tuple(
        sorted(
            units,
            key=lambda unit: (
                1 if _unit_injured(unit) else 0,
                _object_key(unit),
            ),
        )
    )
    guard_count = _guard_count(len(ordered))
    return ordered[:guard_count], ordered[guard_count:]


def _core_visible_enemies(turn: Any, obstacles: set[Position]) -> list[Any]:
    """Return hostile units inside the Core's five-cell vision envelope."""

    if turn.core is None:
        return []
    core_position = _position(turn.core)
    return sorted(
        (
            enemy
            for enemy in turn.visible_enemies
            if not _is_enemy_core(enemy)
            and _visible_from(core_position, _position(enemy), CORE_VISION_RADIUS, obstacles)
        ),
        key=lambda enemy: (
            _manhattan(_position(enemy), core_position),
            int(getattr(enemy, "hp", 0)),
            _object_key(enemy),
        ),
    )


def _assign_targets(units: tuple[Any, ...], enemies: list[Any]) -> dict[str, Any]:
    """Spread multiple enemies across a group while keeping one target shared."""

    if not units or not enemies:
        return {}
    assignments: dict[str, Any] = {}
    for index, unit in enumerate(units):
        assignments[_object_key(unit)] = enemies[index % len(enemies)]
    return assignments


def _group_visible_enemies(
    turn: Any,
    units: tuple[Any, ...],
    obstacles: set[Position],
    *,
    shared_vision: bool = False,
    include_cores: bool = False,
) -> list[Any]:
    if not units:
        return []
    seen: dict[str, Any] = {
        _object_key(enemy): enemy
        for enemy in turn.visible_enemies
        if (include_cores or not _is_enemy_core(enemy))
        and (
            shared_vision
            or any(
                _visible_from(
                    _position(unit),
                    _position(enemy),
                    4 if _unit_type_value(unit) == "VANGUARD" else 5,
                    obstacles,
                )
                for unit in units
            )
        )
    }
    return sorted(
        seen.values(),
        key=lambda enemy: (
            0 if _is_enemy_core(enemy) else 1,
            min(_manhattan(_position(unit), _position(enemy)) for unit in units),
            int(getattr(enemy, "hp", 0)),
            _object_key(enemy),
        ),
    )


def _expedition_rally_ready(units: tuple[Any, ...], target: Any | None) -> bool:
    if target is None or len(units) <= 1:
        return True
    target_position = _position(target)
    return all(
        _manhattan(_position(unit), target_position) <= EXPEDITION_RALLY_RADIUS
        for unit in units
    )


def _expedition_rally_goal(
    unit: Any,
    target: Any,
    unit_index: int,
    blocked: set[Position],
    claimed: set[Position],
) -> Position:
    current = _position(unit)
    target_position = _position(target)
    if (
        _manhattan(current, target_position) <= EXPEDITION_RALLY_RADIUS
        and current not in claimed
    ):
        claimed.add(current)
        return current

    candidates = (
        _guard_candidates(target_position, 2)
        + _guard_candidates(target_position, EXPEDITION_RALLY_RADIUS)
    )
    offset = unit_index % len(candidates)
    ordered = candidates[offset:] + candidates[:offset]
    for candidate in ordered:
        if candidate in blocked or candidate in claimed:
            continue
        claimed.add(candidate)
        return candidate
    return current


def _unit_can_attack_now(
    unit: Any,
    target: Any,
    terrain_obstacles: set[Position],
) -> bool:
    unit_type = _unit_type_value(unit)
    source = _position(unit)
    destination = _position(target)
    if unit_type == "VANGUARD":
        return _manhattan(source, destination) == 1
    if unit_type == "RANGER":
        return _aligned_shot(source, destination, terrain_obstacles)
    return False


def _expedition_defensive_target(
    turn: Any,
    unit: Any,
    terrain_obstacles: set[Position],
) -> Any | None:
    """Find a visible hostile this unit can hit before it reaches rally range."""

    candidates = [
        enemy
        for enemy in turn.visible_enemies
        if _unit_can_attack_now(unit, enemy, terrain_obstacles)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda enemy: (
            0 if _is_enemy_core(enemy) else 1,
            int(getattr(enemy, "hp", 0)),
            _object_key(enemy),
        ),
    )


def _queue_expedition_rally(
    turn: Any,
    unit: Any,
    goal: Position,
    terrain_obstacles: set[Position],
    movement_obstacles: set[Position],
    reserved: set[Position] | None = None,
) -> bool:
    if _queue_beacon_pickup(turn, unit):
        return False
    defensive_target = _expedition_defensive_target(
        turn,
        unit,
        terrain_obstacles,
    )
    if defensive_target is not None:
        unit_type = _unit_type_value(unit)
        if unit_type == "VANGUARD":
            direction = _direction_between(_position(unit), _position(defensive_target))
            if direction is not None:
                unit.sweep(direction)
                return False
        elif unit_type == "RANGER":
            if _is_enemy_core(defensive_target):
                unit.shoot(defensive_target)
            else:
                shoot_cell = getattr(unit, "shoot_cell", None)
                if callable(shoot_cell):
                    shoot_cell(_position(defensive_target))
                else:
                    unit.shoot(defensive_target)
            return False
    if _position(unit) == goal:
        return False
    return _move_toward(unit, goal, movement_obstacles, reserved)


def _unit_max_hp(unit: Any) -> int:
    maximum = {
        "VANGUARD": MAX_VANGUARD_HP,
        "RANGER": MAX_RANGER_HP,
    }.get(_unit_type_value(unit))
    if maximum is not None:
        return maximum
    return int(getattr(unit, "hp", MAX_VANGUARD_HP))


def _unit_injured(unit: Any) -> bool:
    return int(getattr(unit, "hp", _unit_max_hp(unit))) < _unit_max_hp(unit)


def _recovery_candidate(units: Iterable[Any], core: Any) -> Any | None:
    core_position = _position(core)
    injured = tuple(unit for unit in units if _unit_injured(unit))
    if not injured:
        return None
    return min(
        injured,
        key=lambda unit: (
            0 if _position(unit) == core_position else 1,
            int(getattr(unit, "hp", 0)) / _unit_max_hp(unit),
            _manhattan(_position(unit), core_position),
            _object_key(unit),
        ),
    )


def _recovery_staging_goal(
    unit: Any,
    core: Position,
    blocked: set[Position],
    claimed: set[Position],
    phase: int,
    occupancy: dict[Position, int] | None = None,
) -> Position:
    origin = _position(unit)
    if (
        1 <= _manhattan(origin, core) <= 2
        and origin not in claimed
        and (occupancy is None or occupancy.get(origin, 0) <= 1)
    ):
        claimed.add(origin)
        return origin

    candidates = _guard_candidates(core, 1) + _guard_candidates(core, 2)
    seed = sum(ord(char) for char in _object_key(unit)) + phase
    ordered = candidates[seed % len(candidates) :] + candidates[: seed % len(candidates)]
    for candidate in ordered:
        if candidate not in blocked and candidate not in claimed:
            claimed.add(candidate)
            return candidate
    return origin


def _queue_combat_action(
    unit: Any,
    target: Any | None,
    terrain_obstacles: set[Position],
    movement_obstacles: set[Position],
    core: Any | None,
    guard_goal: Position,
    retreat: bool,
    combat_type: str,
    patrol_phase: int = 0,
    reserved: set[Position] | None = None,
) -> bool:
    position = _position(unit)
    if retreat:
        if position != guard_goal:
            return _move_toward(
                unit,
                guard_goal,
                movement_obstacles,
                reserved,
            )
        return False
    if target is not None:
        enemy_position = _position(target)
        if combat_type == "VANGUARD" and _manhattan(position, enemy_position) == 1:
            direction = _direction_between(position, enemy_position)
            if direction is not None:
                unit.sweep(direction)
                return False
        if combat_type == "RANGER" and _aligned_shot(
            position,
            enemy_position,
            terrain_obstacles,
        ):
            if _is_enemy_core(target):
                unit.shoot(target)
                return False
            # Target-free cell fire retargets whichever hostile occupies the
            # cell after movement. Precision fire carries target_id and must
            # miss when that enemy moves before combat resolves.
            shoot_cell = getattr(unit, "shoot_cell", None)
            if callable(shoot_cell):
                shoot_cell(enemy_position)
            else:
                unit.shoot(target)
            return False
        if combat_type == "VANGUARD":
            attack_goals = tuple(
                (enemy_position[0] + dx, enemy_position[1] + dy)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            )
        else:
            attack_goals = tuple(
                candidate
                for candidate in (
                    (enemy_position[0] - 1, enemy_position[1]),
                    (enemy_position[0] + 1, enemy_position[1]),
                    (enemy_position[0], enemy_position[1] - 1),
                    (enemy_position[0], enemy_position[1] + 1),
                    (enemy_position[0] - 2, enemy_position[1]),
                    (enemy_position[0] + 2, enemy_position[1]),
                    (enemy_position[0], enemy_position[1] - 2),
                    (enemy_position[0], enemy_position[1] + 2),
                )
                if _aligned_shot(candidate, enemy_position, terrain_obstacles)
            )
        for attack_goal in attack_goals:
            if attack_goal not in movement_obstacles and _move_toward(
                unit,
                attack_goal,
                movement_obstacles,
                reserved,
            ):
                return True
    if core is not None and position != guard_goal:
        if _move_toward(unit, guard_goal, movement_obstacles, reserved):
            return True
    if core is not None:
        return _patrol_step(
            unit,
            _position(core),
            max(1, _manhattan(_position(core), guard_goal)),
            movement_obstacles,
            patrol_phase,
            reserved,
        )
    return False


def _queue_worker_action(
    turn: Any,
    worker: Any,
    obstacles: set[Position],
    memory: TacticMemory,
    assigned_resource: Position | None,
    worker_index: int,
    worker_count: int,
    core_alert: bool,
    reserved: set[Position] | None = None,
) -> bool:
    core = turn.core
    worker_position = _position(worker)

    if (
        getattr(worker, "hp", MAX_WORKER_HP) < MAX_WORKER_HP
        and turn.resources > 0
        and core is not None
        and _core_is_stationary(core)
        and _same_cell(worker, core)
    ):
        worker.heal()
        return False
    if _queue_beacon_pickup(turn, worker):
        return False

    threats = _visible_threats(turn, worker_position, WORKER_THREAT_RADIUS)
    if threats:
        memory.resource_targets.pop(_object_key(worker), None)
        return _move_away(
            worker,
            threats,
            obstacles,
            _position(core) if core is not None else None,
            core_alert,
            reserved,
        )

    cargo = int(getattr(worker, "cargo", 0) or 0)
    if cargo:
        if (
            core is not None
            and _same_cell(worker, core)
            and _core_is_stationary(core)
            and turn.resource_space > 0
        ):
            worker.deposit()
            return False
        elif core is not None and _same_cell(worker, core):
            # A full Core cannot spawn while a Worker occupies its second slot.
            return _explore(worker, turn, obstacles, reserved)
        elif core is not None:
            return _move_toward(worker, _position(core), obstacles, reserved)
        return False

    if worker_position in turn.resource_cells and assigned_resource == worker_position:
        worker.harvest()
        return False

    if assigned_resource is not None:
        if _move_toward(worker, assigned_resource, obstacles, reserved):
            return True
        memory.resource_targets.pop(_object_key(worker), None)

    if core is not None:
        scout_goal = memory.scout_goal_for(
            worker,
            worker_index,
            worker_count,
            _position(core),
        )
        previous = memory.previous_positions.get(_object_key(worker))
        scout_obstacles = obstacles | ({previous} if previous is not None else set())
        if _move_toward(worker, scout_goal, scout_obstacles, reserved):
            return True
        return _move_toward(worker, scout_goal, obstacles, reserved)
    return False


def _queue_vanguard_action(
    turn: Any,
    vanguard: Any,
    terrain_obstacles: set[Position],
    movement_obstacles: set[Position],
    guard_goal: Position,
    target: Any | None = None,
    retreat: bool = False,
    patrol_phase: int = 0,
    reserved: set[Position] | None = None,
) -> bool:
    core = turn.core
    if (
        getattr(vanguard, "hp", MAX_VANGUARD_HP) < MAX_VANGUARD_HP
        and turn.resources > 0
        and core is not None
        and _core_is_stationary(core)
        and _same_cell(vanguard, core)
    ):
        vanguard.heal()
        return False
    if _queue_beacon_pickup(turn, vanguard):
        return False
    return _queue_combat_action(
        vanguard,
        target,
        terrain_obstacles,
        movement_obstacles,
        core,
        guard_goal,
        retreat,
        "VANGUARD",
        patrol_phase,
        reserved,
    )


def _queue_ranger_action(
    turn: Any,
    ranger: Any,
    terrain_obstacles: set[Position],
    movement_obstacles: set[Position],
    guard_goal: Position,
    target: Any | None = None,
    retreat: bool = False,
    patrol_phase: int = 0,
    reserved: set[Position] | None = None,
) -> bool:
    core = turn.core
    if (
        getattr(ranger, "hp", MAX_RANGER_HP) < MAX_RANGER_HP
        and turn.resources > 0
        and core is not None
        and _core_is_stationary(core)
        and _same_cell(ranger, core)
    ):
        ranger.heal()
        return False
    if _queue_beacon_pickup(turn, ranger):
        return False
    return _queue_combat_action(
        ranger,
        target,
        terrain_obstacles,
        movement_obstacles,
        core,
        guard_goal,
        retreat,
        "RANGER",
        patrol_phase,
        reserved,
    )


def _spawn_choice(turn: Any, core_alert: bool) -> Any:
    if UnitType is None:
        return None
    population = int(getattr(turn.state, "population", len(turn.units)))

    # Establish the initial mixed force, then keep growing without a local
    # population ceiling. Dynamic server pricing and available resources remain
    # the authoritative production constraints.
    workers = len(turn.workers)
    if workers < BOOTSTRAP_WORKER_TARGET:
        return UnitType.WORKER
    missing_vanguard = max(0, VANGUARD_TARGET - len(turn.vanguards))
    missing_ranger = max(0, RANGER_TARGET - len(turn.rangers))
    if core_alert and missing_vanguard:
        return UnitType.VANGUARD
    if core_alert and missing_ranger:
        return UnitType.RANGER

    # Catch the economy up before adding another combat unit. This prevents a
    # large combat burst from starving the Worker fleet and Core income.
    desired_workers = min(
        WORKER_TARGET,
        max(BOOTSTRAP_WORKER_TARGET, population // 2),
    )
    if not core_alert and workers < desired_workers:
        return UnitType.WORKER

    if missing_vanguard:
        return UnitType.VANGUARD
    if missing_ranger:
        return UnitType.RANGER

    # Build enough workers to sustain the economy, then alternate combat types
    # so each new pair can form a Vanguard/Ranger expedition team.
    if len(turn.rangers) <= len(turn.vanguards):
        return UnitType.RANGER
    if workers < FINAL_WORKER_TARGET:
        return UnitType.WORKER
    return UnitType.VANGUARD


def _production_reserve(turn: Any, choice: Any, core_alert: bool) -> int:
    if choice in {
        getattr(UnitType, "VANGUARD", None),
        getattr(UnitType, "RANGER", None),
    } and (
        len(turn.vanguards) < VANGUARD_TARGET
        or len(turn.rangers) < RANGER_TARGET
    ):
        return 0
    if choice == getattr(UnitType, "WORKER", None) and len(turn.workers) < BOOTSTRAP_WORKER_TARGET:
        return 0
    if core_alert:
        return 2
    return RESOURCE_RESERVE


def _queue_core_action(
    turn: Any,
    departing_core_ids: set[str],
    core_alert: bool,
) -> None:
    core = turn.core
    if core is None or not _core_is_stationary(core):
        return
    if _queue_beacon_pickup(turn, core):
        return

    core_position = _position(core)
    if core.hp < MAX_CORE_HP:
        core.heal()
        return

    beacon_carrier = _beacon_is_carried_by(turn, core) or any(
        _beacon_is_carried_by(turn, unit) for unit in turn.units
    )
    shield_cap = 10 if beacon_carrier else 5
    if core.shield < shield_cap and (turn.resources > 0 or core_alert):
        core.repair_shield()
        return

    occupants_after_movement = sum(
        1
        for unit in turn.units
        if _position(unit) == core_position and _object_key(unit) not in departing_core_ids
    )
    choice = _spawn_choice(turn, core_alert)
    if choice is None or occupants_after_movement:
        return
    cost = unit_cost(choice, turn.state.population) if unit_cost is not None else 0
    reserve = _production_reserve(turn, choice, core_alert)
    if turn.resources >= cost + reserve:
        core.spawn(choice)


def choose_actions(turn: Any, memory: TacticMemory | None = None) -> None:
    """Queue one complete, current-Turn plan using a resource-first policy."""

    memory = memory or MEMORY
    memory.observe(turn)
    if turn.core is None:
        return
    terrain_obstacles = set(memory.known_obstacles)
    if _core_threats(turn, terrain_obstacles, immediate_only=False):
        memory.core_alert_until = max(
            memory.core_alert_until,
            int(getattr(turn, "tick", 0)) + CORE_ALERT_TICKS,
        )
    core_alert = memory.core_alerted(int(getattr(turn, "tick", 0)))
    core_position = _position(turn.core)
    patrol_phase = int(getattr(turn, "tick", 0)) // 2
    traffic_obstacles = (
        terrain_obstacles
        | {_position(enemy) for enemy in turn.visible_enemies}
    )
    friendly_positions = {_position(unit) for unit in turn.units}
    occupancy: dict[Position, int] = {}
    for unit in turn.units:
        position = _position(unit)
        occupancy[position] = occupancy.get(position, 0) + 1
    reserved_destinations: set[Position] = set()
    departing_core_ids: set[str] = set()

    guard_vanguards, expedition_vanguards = _split_guard_force(turn.vanguards)
    guard_rangers, expedition_rangers = _split_guard_force(turn.rangers)
    guard_units = guard_vanguards + guard_rangers
    expedition_units = expedition_vanguards + expedition_rangers
    combat_units = guard_units + expedition_units
    injured_ids = {
        _object_key(unit) for unit in combat_units if _unit_injured(unit)
    }
    recovery_unit = (
        _recovery_candidate(combat_units, turn.core)
        if _core_is_stationary(turn.core)
        else None
    )
    recovery_id = _object_key(recovery_unit) if recovery_unit is not None else None

    guard_blocked = (
        traffic_obstacles
        | {tuple(position) for position in turn.resource_cells}
        | friendly_positions
    )
    claimed_guard_cells: set[Position] = set()

    # Give the most urgent injury first access to the one Unit slot on the Core.
    # Other injuries wait on separate nearby cells instead of forming a traffic jam.
    if recovery_unit is not None:
        core_blockers = tuple(
            unit
            for unit in turn.units
            if _position(unit) == core_position
            and _object_key(unit) != recovery_id
        )
        can_use_healing_slot = turn.resources > 0 and not core_blockers
        recovery_goal = (
            core_position
            if can_use_healing_slot
            else _recovery_staging_goal(
                recovery_unit,
                core_position,
                guard_blocked | reserved_destinations,
                claimed_guard_cells,
                patrol_phase,
                occupancy,
            )
        )
        queue_recovery = (
            _queue_vanguard_action
            if _unit_type_value(recovery_unit) == "VANGUARD"
            else _queue_ranger_action
        )
        moved = queue_recovery(
            turn,
            recovery_unit,
            terrain_obstacles,
            traffic_obstacles,
            recovery_goal,
            None,
            True,
            patrol_phase,
            reserved_destinations,
        )
        if moved and _position(recovery_unit) == core_position:
            departing_core_ids.add(recovery_id)

    assignments = memory.assign_resources(turn.workers)
    workers = tuple(sorted(turn.workers, key=_object_key))
    for worker_index, worker in enumerate(workers):
        worker_obstacles = traffic_obstacles | (
            friendly_positions - {_position(worker)}
        )
        moved = _queue_worker_action(
            turn,
            worker,
            worker_obstacles,
            memory,
            assignments.get(_object_key(worker)),
            worker_index,
            len(workers),
            core_alert,
            reserved_destinations,
        )
        if moved and _position(worker) == core_position:
            departing_core_ids.add(_object_key(worker))

    active_guards = tuple(
        unit for unit in guard_units if _object_key(unit) not in injured_ids
    )
    active_expedition = tuple(
        unit for unit in expedition_units if _object_key(unit) not in injured_ids
    )
    expedition_goals = memory.assign_expedition_goals(
        active_expedition,
        core_position,
    )
    expedition_indexes = {
        _object_key(unit): index for index, unit in enumerate(active_expedition)
    }
    guard_enemies = _core_visible_enemies(turn, terrain_obstacles)
    if not guard_enemies:
        guard_enemies = _group_visible_enemies(turn, active_guards, terrain_obstacles)
    expedition_enemies = _group_visible_enemies(
        turn,
        active_expedition,
        terrain_obstacles,
        include_cores=True,
    )
    guard_targets = _assign_targets(active_guards, guard_enemies)
    primary_expedition_target = (
        expedition_enemies[0] if expedition_enemies else None
    )
    rally_ready = _expedition_rally_ready(
        active_expedition,
        primary_expedition_target,
    )
    enemy_core = next(
        (enemy for enemy in expedition_enemies if _is_enemy_core(enemy)),
        None,
    )
    expedition_targets = (
        {
            _object_key(unit): enemy_core
            for unit in active_expedition
        }
        if enemy_core is not None
        else _assign_targets(active_expedition, expedition_enemies)
    )
    expedition_retreat = bool(active_expedition) and (
        len(expedition_enemies) > len(active_expedition) and core_alert
    )
    for vanguard in guard_vanguards:
        unit_id = _object_key(vanguard)
        if unit_id == recovery_id:
            continue
        injured = unit_id in injured_ids
        goal = (
            _recovery_staging_goal(
                vanguard,
                core_position,
                guard_blocked | reserved_destinations,
                claimed_guard_cells,
                patrol_phase,
                occupancy,
            )
            if injured
            else _guard_goal(
                vanguard,
                core_position,
                CORE_GUARD_RADIUS,
                guard_blocked | reserved_destinations,
                claimed_guard_cells,
                patrol_phase,
            )
        )
        moved = _queue_vanguard_action(
            turn,
            vanguard,
            terrain_obstacles,
            traffic_obstacles,
            goal,
            None if injured else guard_targets.get(unit_id),
            injured,
            patrol_phase,
            reserved_destinations,
        )
        if moved and _position(vanguard) == core_position:
            departing_core_ids.add(unit_id)

    for ranger in guard_rangers:
        unit_id = _object_key(ranger)
        if unit_id == recovery_id:
            continue
        injured = unit_id in injured_ids
        goal = (
            _recovery_staging_goal(
                ranger,
                core_position,
                guard_blocked | reserved_destinations,
                claimed_guard_cells,
                patrol_phase,
            )
            if injured
            else _guard_goal(
                ranger,
                core_position,
                CORE_GUARD_RADIUS,
                guard_blocked | reserved_destinations,
                claimed_guard_cells,
                patrol_phase,
            )
        )
        moved = _queue_ranger_action(
            turn,
            ranger,
            terrain_obstacles,
            traffic_obstacles,
            goal,
            None if injured else guard_targets.get(unit_id),
            injured,
            patrol_phase,
            reserved_destinations,
        )
        if moved and _position(ranger) == core_position:
            departing_core_ids.add(unit_id)

    expedition_claimed: set[Position] = set(claimed_guard_cells)
    rally_claimed: set[Position] = set()
    for unit in expedition_vanguards:
        unit_id = _object_key(unit)
        if unit_id == recovery_id:
            continue
        injured = unit_id in injured_ids
        retreat = injured or expedition_retreat
        if injured:
            expedition_goal = _recovery_staging_goal(
                unit,
                core_position,
                guard_blocked | reserved_destinations,
                expedition_claimed,
                patrol_phase,
                occupancy,
            )
        elif expedition_retreat:
            expedition_goal = _guard_goal(
                unit,
                core_position,
                CORE_GUARD_RADIUS + 1,
                guard_blocked | reserved_destinations,
                expedition_claimed,
                patrol_phase,
                occupancy,
            )
        elif primary_expedition_target is not None and not rally_ready:
            expedition_goal = _expedition_rally_goal(
                unit,
                primary_expedition_target,
                expedition_indexes.get(unit_id, 0),
                traffic_obstacles | reserved_destinations,
                rally_claimed,
            )
        else:
            expedition_goal = expedition_goals.get(unit_id, core_position)
            beacon = getattr(turn, "beacon", None)
            beacon_status = _enum_value(getattr(beacon, "status", None))
            if (
                expedition_rangers
                and unit is expedition_rangers[0]
                and _beacon_ready(turn)
                and beacon_status == "GROUND"
                and getattr(beacon, "position", None) is not None
            ):
                expedition_goal = tuple(beacon.position)
        if primary_expedition_target is not None and not rally_ready and not retreat:
            moved = _queue_expedition_rally(
                turn,
                unit,
                expedition_goal,
                terrain_obstacles,
                traffic_obstacles,
                reserved_destinations,
            )
        else:
            moved = _queue_vanguard_action(
                turn,
                unit,
                terrain_obstacles,
                traffic_obstacles,
                expedition_goal,
                None if retreat else expedition_targets.get(unit_id),
                retreat,
                patrol_phase,
                reserved_destinations,
            )
        if moved and _position(unit) == core_position:
            departing_core_ids.add(unit_id)

    for unit in expedition_rangers:
        unit_id = _object_key(unit)
        if unit_id == recovery_id:
            continue
        injured = unit_id in injured_ids
        retreat = injured or expedition_retreat
        if injured:
            expedition_goal = _recovery_staging_goal(
                unit,
                core_position,
                guard_blocked | reserved_destinations,
                expedition_claimed,
                patrol_phase,
            )
        elif expedition_retreat:
            expedition_goal = _guard_goal(
                unit,
                core_position,
                CORE_GUARD_RADIUS + 1,
                guard_blocked | reserved_destinations,
                expedition_claimed,
                patrol_phase,
            )
        elif primary_expedition_target is not None and not rally_ready:
            expedition_goal = _expedition_rally_goal(
                unit,
                primary_expedition_target,
                expedition_indexes.get(unit_id, 0),
                traffic_obstacles | reserved_destinations,
                rally_claimed,
            )
        else:
            expedition_goal = expedition_goals.get(unit_id, core_position)
            beacon = getattr(turn, "beacon", None)
            beacon_status = _enum_value(getattr(beacon, "status", None))
            if (
                expedition_rangers
                and unit is expedition_rangers[0]
                and _beacon_ready(turn)
                and beacon_status == "GROUND"
                and getattr(beacon, "position", None) is not None
            ):
                expedition_goal = tuple(beacon.position)
        if primary_expedition_target is not None and not rally_ready and not retreat:
            moved = _queue_expedition_rally(
                turn,
                unit,
                expedition_goal,
                terrain_obstacles,
                traffic_obstacles,
                reserved_destinations,
            )
        else:
            moved = _queue_ranger_action(
                turn,
                unit,
                terrain_obstacles,
                traffic_obstacles,
                expedition_goal,
                None if retreat else expedition_targets.get(unit_id),
                retreat,
                patrol_phase,
                reserved_destinations,
            )
        if moved and _position(unit) == core_position:
            departing_core_ids.add(unit_id)

    _queue_core_action(
        turn,
        departing_core_ids,
        core_alert,
    )


def _state_directory() -> Path:
    configured = os.environ.get("ARENA_HERO_STATE_DIR")
    directory = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parent
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _start_configured_dashboard(runtime: DashboardRuntime) -> Any:
    port = int(os.environ.get("ARENA_HERO_DASHBOARD_PORT", "8765"))
    try:
        server, _ = start_dashboard(runtime, port=port)
    except OSError as exc:
        raise SystemExit(
            f"Dashboard port {port} is unavailable. Stop the process using it "
            "or set ARENA_HERO_DASHBOARD_PORT to another fixed port."
        ) from exc
    print(f"dashboard=http://127.0.0.1:{server.server_port}")
    return server


def _run_game(api_key: str, runtime: DashboardRuntime) -> None:
    """Run one authenticated game session in the background."""

    global MEMORY
    state_directory = _state_directory()
    stats_path = state_directory / ".arena-hero-dashboard-stats.json"
    map_path = state_directory / ".arena-hero-dashboard-map.json"
    MEMORY = TacticMemory.load(map_path)
    operator_stats = fetch_lifetime_stats(api_key) or OperatorStats.load(stats_path)
    print(f"state_directory={state_directory}")
    print(f"statistics={operator_stats.source}")
    print(f"explored_cells={len(MEMORY.explored_cells)}")
    try:
        with ArenaHeroClient(api_key=api_key) as game:
            for turn in game.turns():
                choose_actions(turn)
                try:
                    MEMORY.save(map_path)
                except OSError:
                    pass
                operator_stats.observe(turn)
                try:
                    operator_stats.save(stats_path)
                except OSError:
                    pass
                runtime.publish(build_snapshot(turn, MEMORY, operator_stats))
                try:
                    accepted = turn.submit()
                except APIError as exc:
                    if exc.status_code == 409 and exc.error == "TICK_MISMATCH":
                        runtime.record_submission(False)
                        print(f"tick={turn.tick} skipped=TICK_MISMATCH")
                        continue
                    raise
                runtime.record_submission(bool(accepted.accepted))
                print(f"tick={accepted.tick} accepted={accepted.accepted}")
    except KeyboardInterrupt:
        print("stopped")
    except Exception as exc:
        runtime.record_error(exc, api_key)
        print(f"strategy_error={runtime.current()[1]['runtime']['lastError']}")


def play() -> None:
    """Start the Dashboard and wait for a Key submitted from its page."""

    if ArenaHeroClient is None:
        raise RuntimeError("Install dependencies first: python -m pip install -r requirements.txt")

    runtime = DashboardRuntime()
    dashboard_enabled = os.environ.get("ARENA_HERO_DASHBOARD", "1").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if not dashboard_enabled:
        raise SystemExit(
            "Dashboard must be enabled because the Arena Hero API Key is entered on its page."
        )

    worker_lock = threading.Lock()
    game_thread: threading.Thread | None = None

    def submit_key(raw_key: str) -> tuple[bool, str | None]:
        nonlocal game_thread
        api_key = raw_key.strip()
        validation_error = _api_key_validation_error(api_key)
        if validation_error:
            return False, validation_error
        with worker_lock:
            if game_thread is not None and game_thread.is_alive():
                return False, "策略已经在运行；关闭或刷新网页不会停止它。"
            runtime.begin_connecting()
            game_thread = threading.Thread(
                target=_run_game,
                args=(api_key, runtime),
                name="arena-hero-tactic",
                daemon=True,
            )
            game_thread.start()
        return True, None

    runtime.set_key_submitter(submit_key)
    dashboard_server = _start_configured_dashboard(runtime)
    stop_event = threading.Event()
    try:
        print("Enter the Arena Hero API Key at the Dashboard page to start.")
        while not stop_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("stopped")
    finally:
        stop_event.set()
        runtime.set_key_submitter(None)
        runtime.stop()
        dashboard_server.shutdown()
        dashboard_server.server_close()


def _api_key_validation_error(api_key: str) -> str | None:
    if not api_key:
        return "Arena Hero API key is required."

    invalid = [
        (index, ord(character))
        for index, character in enumerate(api_key, start=1)
        if ord(character) < 0x21 or ord(character) > 0x7E
    ]
    if not invalid:
        return None

    details = ", ".join(
        f"position {position}=U+{code_point:04X}"
        for position, code_point in invalid[:8]
    )
    if len(invalid) > 8:
        details += f", plus {len(invalid) - 8} more"
    return (
        "Arena Hero API key must contain visible ASCII only; "
        f"invalid characters: {details}."
    )


def _load_api_key() -> str:
    configured = os.environ.get("ARENA_HERO_API_KEY")
    if configured is not None:
        api_key = configured.strip()
        error = _api_key_validation_error(api_key)
        if error:
            raise SystemExit(error)
        return api_key

    while True:
        api_key = getpass("Arena Hero API key: ").strip()
        error = _api_key_validation_error(api_key)
        if error is None:
            return api_key
        print(error)
        print("Please copy the raw key without labels, quotes, or formatting.")


def _load_local_environment() -> None:
    # systemd supplies ARENA_HERO_API_KEY through EnvironmentFile. dotenv is
    # only an optional convenience for local runs and must not prevent a
    # server deployment from starting with an older compatible virtualenv.
    if load_dotenv is not None:
        load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)


def main() -> None:
    play()


if __name__ == "__main__":
    main()
