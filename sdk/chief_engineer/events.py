"""Thread-safe mission event stream used by the control room."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Condition
from typing import Iterator


@dataclass(frozen=True)
class MissionEvent:
    sequence: int
    timestamp: float
    event: str
    mission_id: str
    payload: dict

    def as_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "event": self.event,
            "mission_id": self.mission_id,
            "payload": self.payload,
        }


class EventBus:
    def __init__(self, mission_id: str, persist_path: str | Path | None = None):
        self.mission_id = mission_id
        self._events: list[MissionEvent] = []
        self._condition = Condition()
        self.closed = False
        self.persist_path = Path(persist_path) if persist_path else None
        if self.persist_path and self.persist_path.exists():
            for line in self.persist_path.read_text().splitlines():
                try:
                    item = json.loads(line)
                    self._events.append(MissionEvent(
                        int(item["sequence"]),
                        float(item["timestamp"]),
                        str(item["event"]),
                        str(item.get("mission_id", mission_id)),
                        dict(item.get("payload", {})),
                    ))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue

    def publish(self, event: str, payload: dict | None = None) -> MissionEvent:
        with self._condition:
            item = MissionEvent(len(self._events) + 1, time.time(), event, self.mission_id, payload or {})
            self._events.append(item)
            if self.persist_path:
                self.persist_path.parent.mkdir(parents=True, exist_ok=True)
                with self.persist_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(item.as_dict(), separators=(",", ":")) + "\n")
            self._condition.notify_all()
            return item

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()

    def snapshot(self, after: int = 0) -> list[MissionEvent]:
        with self._condition:
            return [event for event in self._events if event.sequence > after]

    def stream(self, after: int = 0, heartbeat_s: float = 10.0) -> Iterator[MissionEvent | None]:
        cursor = after
        while True:
            heartbeat = False
            with self._condition:
                available = [event for event in self._events if event.sequence > cursor]
                if not available and not self.closed:
                    self._condition.wait(timeout=heartbeat_s)
                    available = [event for event in self._events if event.sequence > cursor]
                if not available:
                    if self.closed:
                        return
                    heartbeat = True
            if heartbeat:
                yield None
                continue
            for event in available:
                cursor = event.sequence
                yield event
