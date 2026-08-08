"""Local real-time dashboard server and Arena Hero snapshot helpers."""

from __future__ import annotations

from copy import deepcopy
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any

import httpx


STATIC_DIR = Path(__file__).with_name("dashboard")
STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

PLAYER_STAT_FIELDS = (
    "damage_dealt",
    "damage_received",
    "unit_destruction_participations",
    "core_destruction_participations",
    "resources_harvested",
    "resources_deposited",
    "beacon_pickups",
    "beacon_ticks_held",
    "beacon_bonus_resources_harvested",
    "units_spawned",
    "units_lost",
    "unit_hp_recovered",
    "core_hp_recovered",
    "core_survival_ticks",
    "respawn_count",
)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _position(obj: Any) -> list[int]:
    position = getattr(obj, "position", (0, 0))
    return [int(position[0]), int(position[1])]


def _object_id(obj: Any) -> str:
    return str(getattr(obj, "id", ""))


def _view(obj: Any) -> Any:
    return getattr(obj, "view", obj)


def _action_payload(turn: Any, obj: Any) -> dict[str, Any] | None:
    plan = getattr(turn, "plan", None)
    if plan is not None:
        action = getattr(plan, "unit_actions", {}).get(getattr(obj, "id", None))
        if action is not None:
            payload = {"type": str(getattr(action, "type", action.__class__.__name__))}
            for name in ("direction", "unit_type", "position", "target_id"):
                value = getattr(action, name, None)
                if value is not None:
                    payload[name] = _enum_value(value)
            return payload

    actions = getattr(obj, "actions", ())
    if actions:
        action = actions[-1]
        payload = {"type": str(action[0]).upper()}
        if len(action) > 1:
            payload["detail"] = str(_enum_value(action[1]))
        return payload
    return None


def _unit_payload(turn: Any, unit: Any, memory: Any) -> dict[str, Any]:
    view = _view(unit)
    unit_id = _object_id(unit)
    unit_type = _enum_value(getattr(view, "unit_type", unit.__class__.__name__))
    return {
        "id": unit_id,
        "type": str(unit_type).upper(),
        "position": _position(unit),
        "hp": int(getattr(unit, "hp", getattr(view, "hp", 0)) or 0),
        "cargo": int(getattr(unit, "cargo", getattr(view, "cargo", 0)) or 0),
        "resourceTarget": _optional_position(memory.resource_targets.get(unit_id)),
        "scoutGoal": _optional_position(memory.scout_goal.get(unit_id)),
        "harvests": int(memory.worker_harvests.get(unit_id, 0)),
        "action": _action_payload(turn, unit),
    }


def _enemy_payload(enemy: Any) -> dict[str, Any]:
    view = _view(enemy)
    kind = str(getattr(view, "kind", enemy.__class__.__name__)).upper()
    unit_type = _enum_value(getattr(view, "unit_type", kind))
    return {
        "id": _object_id(enemy),
        "kind": kind,
        "type": str(unit_type).upper(),
        "position": _position(enemy),
        "hp": int(getattr(enemy, "hp", getattr(view, "hp", 0)) or 0),
        "shield": int(getattr(enemy, "shield", getattr(view, "shield", 0)) or 0),
    }


def _optional_position(position: Any) -> list[int] | None:
    if position is None:
        return None
    return [int(position[0]), int(position[1])]


def _positions(positions: Any) -> list[list[int]]:
    return [[int(x), int(y)] for x, y in sorted(positions)]


def _event_payload(event: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": str(_enum_value(getattr(event, "event_type", "EVENT"))),
    }
    for source, target in (
        ("reason_code", "reason"),
        ("actor_id", "actorId"),
        ("target_id", "targetId"),
    ):
        value = getattr(event, source, None)
        if value is not None:
            result[target] = str(_enum_value(value))
    position = getattr(event, "position", None)
    if position is not None:
        result["position"] = _optional_position(position)
    return result


def _event_values(event: Any) -> dict[str, Any]:
    values = getattr(event, "values", None)
    return values if isinstance(values, dict) else {}


def _integer_value(values: dict[str, Any], name: str) -> int:
    value = values.get(name, 0)
    return value if type(value) is int and value >= 0 else 0


@dataclass
class OperatorStats:
    """Lifetime baseline plus locally observed private resolution events."""

    values: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name in PLAYER_STAT_FIELDS}
    )
    source: str = "local"
    tracked_since: int = field(default_factory=lambda: int(time.time() * 1000))
    last_observed_tick: int | None = None
    previous_unit_ids: set[str] | None = None
    _recent_event_ids: deque[str] = field(default_factory=lambda: deque(maxlen=512))
    _recent_event_id_set: set[str] = field(default_factory=set)

    @classmethod
    def from_values(
        cls,
        values: dict[str, Any],
        *,
        source: str,
        tracked_since: int | None = None,
    ) -> "OperatorStats":
        normalized = {
            name: max(0, int(values.get(name, 0)))
            if type(values.get(name, 0)) is int
            else 0
            for name in PLAYER_STAT_FIELDS
        }
        return cls(
            values=normalized,
            source=source,
            tracked_since=tracked_since or int(time.time() * 1000),
        )

    @classmethod
    def load(cls, path: Path) -> "OperatorStats":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            stats = cls.from_values(
                payload.get("values", {}),
                source=str(payload.get("source", "local")),
                tracked_since=int(payload.get("trackedSince", 0)) or None,
            )
            recent_ids = [str(item) for item in payload.get("recentEventIds", ())][-512:]
            stats._recent_event_ids.extend(recent_ids)
            stats._recent_event_id_set.update(recent_ids)
            return stats
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path) -> None:
        payload = {
            "source": self.source,
            "trackedSince": self.tracked_since,
            "values": self.values,
            "recentEventIds": list(self._recent_event_ids),
        }
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)

    def observe(self, turn: Any) -> None:
        tick = int(getattr(turn, "tick", 0) or 0)
        current_unit_ids = {_object_id(unit) for unit in getattr(turn, "units", ())}
        if self.previous_unit_ids is not None:
            self.values["units_lost"] += len(self.previous_unit_ids - current_unit_ids)
        self.previous_unit_ids = current_unit_ids

        if tick != self.last_observed_tick:
            if getattr(turn, "core", None) is not None:
                self.values["core_survival_ticks"] += 1
            owned_ids = set(current_unit_ids)
            if getattr(turn, "core", None) is not None:
                owned_ids.add(_object_id(turn.core))
            beacon = getattr(turn, "beacon", None)
            carrier_id = getattr(beacon, "carrier_id", None) if beacon is not None else None
            if carrier_id is not None and str(carrier_id) in owned_ids:
                self.values["beacon_ticks_held"] += 1
            self.last_observed_tick = tick

        for index, event in enumerate(getattr(turn, "events", ())):
            event_key = self._event_key(event, tick, index)
            if event_key in self._recent_event_id_set:
                continue
            self._remember_event(event_key)
            event_type = str(_enum_value(getattr(event, "event_type", "")))
            reason = str(_enum_value(getattr(event, "reason_code", "")))
            values = _event_values(event)

            if event_type == "SHOT_HIT":
                self.values["damage_dealt"] += _integer_value(values, "damage")
            elif event_type == "SWEEP_RESOLVED":
                self.values["damage_dealt"] += _integer_value(values, "targets_hit")
            elif event_type in {"UNIT_DAMAGED", "CORE_DAMAGED"}:
                self.values["damage_received"] += _integer_value(values, "damage")
            elif event_type == "DESTRUCTION_PARTICIPATION":
                field_name = (
                    "core_destruction_participations"
                    if reason == "CORE"
                    else "unit_destruction_participations"
                )
                self.values[field_name] += 1
            elif event_type == "HARVEST_SUCCEEDED":
                if str(_enum_value(values.get("source", ""))) == "RESOURCE_NODE":
                    self.values["resources_harvested"] += _integer_value(values, "amount")
            elif event_type == "DEPOSIT_SUCCEEDED":
                self.values["resources_deposited"] += _integer_value(values, "amount")
            elif event_type == "BEACON_PICKED_UP":
                self.values["beacon_pickups"] += 1
            elif event_type == "BEACON_HARVEST_BONUS":
                self.values["beacon_bonus_resources_harvested"] += _integer_value(
                    values, "amount"
                )
            elif event_type == "CORE_SPAWN_SUCCEEDED":
                self.values["units_spawned"] += 1
            elif event_type == "UNIT_HEAL_SUCCEEDED":
                self.values["unit_hp_recovered"] += _integer_value(values, "amount")
            elif event_type == "CORE_HEAL_SUCCEEDED":
                self.values["core_hp_recovered"] += _integer_value(values, "amount")
            elif event_type == "CORE_RESPAWNED":
                self.values["respawn_count"] += 1

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "trackedSince": self.tracked_since,
            "values": dict(self.values),
        }

    def _event_key(self, event: Any, tick: int, index: int) -> str:
        event_id = getattr(event, "event_id", None)
        if event_id is not None:
            return str(event_id)
        return ":".join(
            (
                str(tick),
                str(index),
                str(getattr(event, "event_type", "")),
                str(getattr(event, "actor_id", "")),
                str(getattr(event, "target_id", "")),
                str(getattr(event, "position", "")),
                json.dumps(_event_values(event), sort_keys=True, default=str),
            )
        )

    def _remember_event(self, event_key: str) -> None:
        if len(self._recent_event_ids) == self._recent_event_ids.maxlen:
            removed = self._recent_event_ids.popleft()
            self._recent_event_id_set.discard(removed)
        self._recent_event_ids.append(event_key)
        self._recent_event_id_set.add(event_key)


def fetch_lifetime_stats(
    api_key: str,
    base_url: str = "https://api.arenahero.io",
) -> OperatorStats | None:
    """Use the official private endpoint when it accepts API-key authentication."""

    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/api/v1/me/stats",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5.0,
        )
        if response.status_code != HTTPStatus.OK:
            return None
        payload = response.json()
        if not isinstance(payload, dict) or not all(
            type(payload.get(name)) is int for name in PLAYER_STAT_FIELDS
        ):
            return None
        return OperatorStats.from_values(payload, source="arena-hero")
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def build_snapshot(
    turn: Any,
    memory: Any,
    operator_stats: OperatorStats | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe view of one Turn without configuration or credentials."""

    workers = [_unit_payload(turn, worker, memory) for worker in turn.workers]
    vanguards = [_unit_payload(turn, unit, memory) for unit in turn.vanguards]
    rangers = [_unit_payload(turn, unit, memory) for unit in turn.rangers]
    core = None
    if turn.core is not None:
        core_view = _view(turn.core)
        core = {
            "id": _object_id(turn.core),
            "position": _position(turn.core),
            "hp": int(getattr(turn.core, "hp", getattr(core_view, "hp", 0)) or 0),
            "shield": int(
                getattr(turn.core, "shield", getattr(core_view, "shield", 0)) or 0
            ),
            "state": str(_enum_value(getattr(core_view, "state", "UNKNOWN"))),
        }

    beacon = getattr(turn, "beacon", None)
    beacon_payload = None
    if beacon is not None:
        beacon_payload = {
            "position": _position(beacon),
            "status": str(_enum_value(getattr(beacon, "status", "UNKNOWN"))),
            "carrierId": (
                str(getattr(beacon, "carrier_id"))
                if getattr(beacon, "carrier_id", None) is not None
                else None
            ),
        }

    state = getattr(turn, "state", None)
    resources = int(getattr(turn, "resources", 0) or 0)
    resource_space = int(getattr(turn, "resource_space", 0) or 0)
    resource_capacity = int(
        getattr(turn, "resource_capacity", resources + resource_space) or 0
    )
    return {
        "generatedAt": int(time.time() * 1000),
        "stats": (operator_stats or OperatorStats()).to_payload(),
        "game": {
            "tick": int(getattr(turn, "tick", 0)),
            "playerStatus": str(_enum_value(getattr(state, "status", "ACTIVE"))),
            "resources": resources,
            "resourceCapacity": resource_capacity,
            "resourceSpace": resource_space,
            "population": int(
                getattr(state, "population", len(getattr(turn, "units", ()))) or 0
            ),
            "counts": {
                "workers": len(workers),
                "vanguards": len(vanguards),
                "rangers": len(rangers),
                "enemies": len(getattr(turn, "visible_enemies", ())),
            },
            "core": core,
            "beacon": beacon_payload,
            "workers": workers,
            "units": workers + vanguards + rangers,
            "enemies": [_enemy_payload(enemy) for enemy in turn.visible_enemies],
            "terrain": {
                "explored": _positions(memory.explored_cells),
                "visible": _positions(memory.visible_cells),
                "obstacles": _positions(memory.known_obstacles),
                "resources": _positions(memory.known_resources),
                "visibleResources": _positions(turn.resource_cells),
            },
            "events": [_event_payload(event) for event in tuple(turn.events)[-12:]],
        },
    }


@dataclass
class DashboardRuntime:
    """Thread-safe state shared by the tactic loop and HTTP clients."""

    started_at: float = field(default_factory=time.monotonic)
    accepted_submissions: int = 0
    failed_submissions: int = 0
    status: str = "connecting"
    last_error: str | None = None
    _sequence: int = 0
    _snapshot: dict[str, Any] = field(default_factory=dict)
    _condition: threading.Condition = field(default_factory=threading.Condition)

    def publish(self, snapshot: dict[str, Any]) -> None:
        with self._condition:
            self.status = "connected"
            self.last_error = None
            self._snapshot = deepcopy(snapshot)
            self._bump_locked()

    def record_submission(self, accepted: bool) -> None:
        with self._condition:
            if accepted:
                self.accepted_submissions += 1
            else:
                self.failed_submissions += 1
            self._bump_locked()

    def record_error(self, error: BaseException) -> None:
        with self._condition:
            self.status = "error"
            self.failed_submissions += 1
            message = re.sub(r"ah_live_[A-Za-z0-9_-]+", "[redacted]", str(error))
            self.last_error = f"{error.__class__.__name__}: {message[:240]}"
            self._bump_locked()

    def stop(self) -> None:
        with self._condition:
            self.status = "stopped"
            self._bump_locked()

    def current(self) -> tuple[int, dict[str, Any]]:
        with self._condition:
            return self._sequence, self._payload_locked()

    def wait_for_update(
        self, after_sequence: int, timeout: float = 15.0
    ) -> tuple[int, dict[str, Any]]:
        with self._condition:
            self._condition.wait_for(
                lambda: self._sequence > after_sequence,
                timeout=timeout,
            )
            return self._sequence, self._payload_locked()

    def _bump_locked(self) -> None:
        self._sequence += 1
        self._condition.notify_all()

    def _payload_locked(self) -> dict[str, Any]:
        payload = deepcopy(self._snapshot)
        payload.setdefault("generatedAt", int(time.time() * 1000))
        payload["runtime"] = {
            "status": self.status,
            "uptimeSeconds": int(time.monotonic() - self.started_at),
            "acceptedSubmissions": self.accepted_submissions,
            "failedSubmissions": self.failed_submissions,
            "lastError": self.last_error,
            "sequence": self._sequence,
        }
        return payload


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], runtime: DashboardRuntime):
        self.runtime = runtime
        super().__init__(address, DashboardRequestHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            _, payload = self.server.runtime.current()
            self._send_json(payload)
            return
        if path == "/api/runtime":
            _, payload = self.server.runtime.current()
            self._send_json(
                {
                    "serverTime": int(time.time() * 1000),
                    "runtime": payload["runtime"],
                }
            )
            return
        if path == "/api/events":
            self._send_event_stream()
            return
        route = STATIC_ROUTES.get(path)
        if route is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        filename, content_type = route
        try:
            content = (STATIC_DIR / filename).read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: dict[str, Any]) -> None:
        # SDK models may retain UUID values in event payloads. Convert those
        # identifiers at the HTTP boundary so one malformed event cannot drop
        # the entire /api/state response.
        content = json.dumps(
            payload,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_event_stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sequence = -1
        try:
            while True:
                next_sequence, payload = self.server.runtime.wait_for_update(sequence)
                if next_sequence == sequence:
                    self.wfile.write(b": keepalive\n\n")
                else:
                    data = json.dumps(payload, separators=(",", ":"), default=str)
                    message = f"id: {next_sequence}\ndata: {data}\n\n".encode("utf-8")
                    self.wfile.write(message)
                    sequence = next_sequence
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_dashboard(
    runtime: DashboardRuntime | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> tuple[DashboardServer, threading.Thread]:
    runtime = runtime or DashboardRuntime()
    server = DashboardServer((host, port), runtime)
    thread = threading.Thread(
        target=server.serve_forever,
        name="arena-hero-dashboard",
        daemon=True,
    )
    thread.start()
    return server, thread
