"""Resource-first, low-conflict starter tactic for Arena Hero.

The decision function is intentionally separate from the SDK connection so it
can be tested with small state doubles before a live credential is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import json
import os
from getpass import getpass
from pathlib import Path
from typing import Any, Iterable

from dashboard import (
    DashboardRuntime,
    OperatorStats,
    build_snapshot,
    fetch_lifetime_stats,
    start_dashboard,
)

try:
    from arena_hero import ArenaHeroClient, Direction, UnitType, unit_cost
except ModuleNotFoundError:  # Allows offline unit tests before dependencies install.
    ArenaHeroClient = None  # type: ignore[assignment,misc]
    Direction = None  # type: ignore[assignment,misc]
    UnitType = None  # type: ignore[assignment,misc]
    unit_cost = None  # type: ignore[assignment,misc]


MAX_CORE_HP = 5
MAX_WORKER_HP = 2
MAX_VANGUARD_HP = 4
MAX_RANGER_HP = 2
COMBAT_THREAT_RADIUS = 3
WORKER_THREAT_RADIUS = 2
RESOURCE_RESERVE = 5
BOOTSTRAP_WORKER_TARGET = 4
WORKER_TARGET = 6
FINAL_WORKER_TARGET = 8
VANGUARD_TARGET = 1
RANGER_TARGET = 2
POPULATION_TARGET = 11
CORE_ALERT_TICKS = 8
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


def _move_toward(unit: Any, target: tuple[int, int], obstacles: set[tuple[int, int]]) -> bool:
    source = _position(unit)
    destination = _first_path_step(source, target, obstacles)
    direction = (
        _direction_between(source, destination)
        if destination is not None and destination != source
        else None
    )
    if direction is None:
        direction = _direction_for_step(source, target, obstacles)
    if direction is None:
        return False
    unit.move(direction)
    return True


def _explore(unit: Any, turn: Any, obstacles: set[tuple[int, int]]) -> bool:
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
        if destination not in obstacles:
            unit.move(direction)
            return True
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
) -> bool:
    origin = _position(unit)
    threat_positions = tuple(_position(threat) for threat in threats)
    candidates: list[tuple[tuple[int, int, int], Any]] = []
    for order, direction in enumerate(_directions()):
        dx, dy = _delta(direction)
        destination = (origin[0] + dx, origin[1] + dy)
        if destination in obstacles:
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
    unit.move(max(candidates, key=lambda item: item[0])[1])
    return True


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

        ring_index = self.scout_ring_index.get(worker_id, 0)
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
) -> Position:
    candidates = _guard_candidates(core, radius)
    seed = sum(ord(char) for char in _object_key(unit))
    ordered = candidates[seed % len(candidates) :] + candidates[: seed % len(candidates)]
    for candidate in ordered:
        if candidate not in blocked and candidate not in claimed:
            claimed.add(candidate)
            return candidate
    return core


def _queue_worker_action(
    turn: Any,
    worker: Any,
    obstacles: set[Position],
    memory: TacticMemory,
    assigned_resource: Position | None,
    worker_index: int,
    worker_count: int,
    core_alert: bool,
) -> bool:
    core = turn.core
    worker_position = _position(worker)

    if (
        getattr(worker, "hp", MAX_WORKER_HP) < MAX_WORKER_HP
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
            return _explore(worker, turn, obstacles)
        elif core is not None:
            return _move_toward(worker, _position(core), obstacles)
        return False

    if worker_position in turn.resource_cells and assigned_resource == worker_position:
        worker.harvest()
        return False

    if assigned_resource is not None:
        if _move_toward(worker, assigned_resource, obstacles):
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
        if _move_toward(worker, scout_goal, scout_obstacles):
            return True
        return _move_toward(worker, scout_goal, obstacles)
    return False


def _queue_vanguard_action(
    turn: Any,
    vanguard: Any,
    obstacles: set[Position],
    guard_goal: Position,
    core_alert: bool,
) -> bool:
    core = turn.core
    position = _position(vanguard)
    if (
        getattr(vanguard, "hp", MAX_VANGUARD_HP) < MAX_VANGUARD_HP
        and core is not None
        and _core_is_stationary(core)
        and _same_cell(vanguard, core)
    ):
        vanguard.heal()
        return False
    if _queue_beacon_pickup(turn, vanguard):
        return False

    threats = _visible_threats(turn, position, COMBAT_THREAT_RADIUS)
    adjacent = [
        enemy
        for enemy in threats
        if _manhattan(_position(enemy), position) == 1
        and _unit_type_value(enemy) != "WORKER"
    ]
    if adjacent:
        direction = _direction_for_step(position, _position(adjacent[0]), obstacles)
        if direction is not None:
            vanguard.sweep(direction)
        return False
    if core_alert and threats:
        return _move_toward(vanguard, _position(threats[0]), obstacles)
    if core is not None and position != guard_goal:
        return _move_toward(vanguard, guard_goal, obstacles)
    return False


def _queue_ranger_action(
    turn: Any,
    ranger: Any,
    obstacles: set[Position],
    guard_goal: Position,
    core_alert: bool,
) -> bool:
    core = turn.core
    position = _position(ranger)
    if (
        getattr(ranger, "hp", MAX_RANGER_HP) < MAX_RANGER_HP
        and core is not None
        and _core_is_stationary(core)
        and _same_cell(ranger, core)
    ):
        ranger.heal()
        return False
    if _queue_beacon_pickup(turn, ranger):
        return False

    # A diagonal range-3 shot is Manhattan distance 6, so use all currently
    # visible enemies for Ranger target selection rather than a radius filter.
    threats = sorted(
        turn.visible_enemies,
        key=lambda enemy: (
            _manhattan(_position(enemy), position),
            int(getattr(enemy, "hp", 0)),
            str(getattr(enemy, "id", "")),
        ),
    )
    for enemy in threats:
        if not _aligned_shot(position, _position(enemy), obstacles):
            continue
        enemy_type = _unit_type_value(enemy)
        threatens_ranger = _can_attack_cell_now(enemy, position, obstacles)
        threatens_core = (
            core is not None
            and _can_attack_cell_now(enemy, _position(core), obstacles)
        )
        if enemy_type != "WORKER" and (core_alert or threatens_ranger or threatens_core):
            ranger.shoot(enemy)
            return False
    if core is not None and position != guard_goal:
        return _move_toward(ranger, guard_goal, obstacles)
    return False


def _spawn_choice(turn: Any, core_alert: bool) -> Any:
    if UnitType is None:
        return None
    if core_alert:
        defensive_choice = None
        if len(turn.vanguards) < VANGUARD_TARGET:
            defensive_choice = UnitType.VANGUARD
        elif len(turn.rangers) < RANGER_TARGET:
            defensive_choice = UnitType.RANGER
        if defensive_choice is not None:
            cost = (
                unit_cost(defensive_choice, turn.state.population)
                if unit_cost is not None
                else 0
            )
            if turn.resources >= cost + 2:
                return defensive_choice
            if len(turn.workers) >= BOOTSTRAP_WORKER_TARGET:
                return defensive_choice
    if len(turn.workers) < BOOTSTRAP_WORKER_TARGET:
        return UnitType.WORKER
    if len(turn.vanguards) < VANGUARD_TARGET:
        return UnitType.VANGUARD
    if len(turn.workers) < WORKER_TARGET:
        return UnitType.WORKER
    if len(turn.rangers) < RANGER_TARGET:
        if len(turn.rangers) == 0:
            return UnitType.RANGER
    if len(turn.workers) < FINAL_WORKER_TARGET:
        return UnitType.WORKER
    if len(turn.rangers) < RANGER_TARGET:
        return UnitType.RANGER
    return None


def _production_reserve(turn: Any, choice: Any, core_alert: bool) -> int:
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
    if (
        turn.resources >= cost + reserve
        and turn.state.population < POPULATION_TARGET
    ):
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
    worker_obstacles = terrain_obstacles | {
        _position(enemy) for enemy in turn.visible_enemies
    }
    assignments = memory.assign_resources(turn.workers)
    workers = tuple(sorted(turn.workers, key=_object_key))
    core_position = _position(turn.core)
    departing_core_ids: set[str] = set()
    for worker_index, worker in enumerate(workers):
        moved = _queue_worker_action(
            turn,
            worker,
            worker_obstacles,
            memory,
            assignments.get(_object_key(worker)),
            worker_index,
            len(workers),
            core_alert,
        )
        if moved and _position(worker) == core_position:
            departing_core_ids.add(_object_key(worker))

    guard_blocked = (
        terrain_obstacles
        | {tuple(position) for position in turn.resource_cells}
        | {_position(enemy) for enemy in turn.visible_enemies}
    )
    claimed_guard_cells: set[Position] = set()
    for vanguard in sorted(turn.vanguards, key=_object_key):
        goal = _guard_goal(
            vanguard,
            core_position,
            1,
            guard_blocked,
            claimed_guard_cells,
        )
        moved = _queue_vanguard_action(
            turn,
            vanguard,
            terrain_obstacles,
            goal,
            core_alert,
        )
        if moved and _position(vanguard) == core_position:
            departing_core_ids.add(_object_key(vanguard))

    for ranger_index, ranger in enumerate(sorted(turn.rangers, key=_object_key)):
        radius = 2 if core_alert or ranger_index == 0 else 5
        goal = _guard_goal(
            ranger,
            core_position,
            radius,
            guard_blocked,
            claimed_guard_cells,
        )
        moved = _queue_ranger_action(
            turn,
            ranger,
            terrain_obstacles,
            goal,
            core_alert,
        )
        if moved and _position(ranger) == core_position:
            departing_core_ids.add(_object_key(ranger))

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


def play(api_key: str) -> None:
    """Run the tactic continuously with the official synchronous SDK."""

    global MEMORY

    if ArenaHeroClient is None:
        raise RuntimeError("Install dependencies first: python -m pip install -r requirements.txt")

    runtime = DashboardRuntime()
    state_directory = _state_directory()
    stats_path = state_directory / ".arena-hero-dashboard-stats.json"
    map_path = state_directory / ".arena-hero-dashboard-map.json"
    MEMORY = TacticMemory.load(map_path)
    operator_stats = fetch_lifetime_stats(api_key) or OperatorStats.load(stats_path)
    print(f"state_directory={state_directory}")
    print(f"statistics={operator_stats.source}")
    print(f"explored_cells={len(MEMORY.explored_cells)}")
    dashboard_server = None
    dashboard_enabled = os.environ.get("ARENA_HERO_DASHBOARD", "1").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if dashboard_enabled:
        requested_port = int(os.environ.get("ARENA_HERO_DASHBOARD_PORT", "8765"))
        for port in range(requested_port, requested_port + 10):
            try:
                dashboard_server, _ = start_dashboard(runtime, port=port)
                print(f"dashboard=http://127.0.0.1:{dashboard_server.server_port}")
                break
            except OSError:
                continue
        if dashboard_server is None:
            print("dashboard unavailable: ports are already in use; tactic will continue")

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
                accepted = turn.submit()
                runtime.record_submission(bool(accepted.accepted))
                print(f"tick={accepted.tick} accepted={accepted.accepted}")
    except KeyboardInterrupt:
        print("stopped")
    except Exception as exc:
        runtime.record_error(exc)
        raise
    finally:
        runtime.stop()
        if dashboard_server is not None:
            dashboard_server.shutdown()
            dashboard_server.server_close()


def main() -> None:
    api_key = os.environ.get("ARENA_HERO_API_KEY") or getpass("Arena Hero API key: ")
    if not api_key:
        raise SystemExit("ARENA_HERO_API_KEY is required.")
    play(api_key)


if __name__ == "__main__":
    main()
