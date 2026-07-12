from __future__ import annotations

import random
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_mapper.artifacts import create_observation_directory, write_json
from app_mapper.graph import (
    GraphError,
    ReplayRefused,
    capture_identified_state,
    graph_summary,
    replay_edge,
)
from app_mapper.models import (
    GraphEdge,
    ValidationEdgeResult,
    ValidationRecord,
)


class ValidationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sample_edges(
    edges: list[GraphEdge], sample_size: int, seed: int
) -> list[GraphEdge]:
    """Choose reproducible forward navigation edges before reverse-only edges."""
    eligible = [
        edge
        for edge in edges
        if edge.action.risk == "safe_navigation" and edge.action.reversible
    ]
    forward = [
        edge
        for edge in eligible
        if edge.action.action_key.startswith("open_")
    ]
    pool = forward or eligible
    ordered = sorted(pool, key=lambda edge: edge.edge_id)
    if sample_size >= len(ordered):
        return ordered
    return sorted(random.Random(seed).sample(ordered, sample_size), key=lambda edge: edge.edge_id)


def shortest_edge_path(
    edges: list[GraphEdge], source: str, destination: str
) -> list[GraphEdge] | None:
    if source == destination:
        return []
    adjacency: dict[str, list[GraphEdge]] = {}
    for edge in edges:
        if edge.action.risk == "safe_navigation" and edge.action.reversible:
            adjacency.setdefault(edge.source_node_id, []).append(edge)
    queue: deque[tuple[str, list[GraphEdge]]] = deque([(source, [])])
    visited = {source}
    while queue:
        node_id, path = queue.popleft()
        for edge in sorted(adjacency.get(node_id, []), key=lambda item: item.edge_id):
            if edge.destination_node_id in visited:
                continue
            next_path = [*path, edge]
            if edge.destination_node_id == destination:
                return next_path
            visited.add(edge.destination_node_id)
            queue.append((edge.destination_node_id, next_path))
    return None


def classify_failure(exc: Exception) -> str:
    message = str(exc).casefold()
    if isinstance(exc, ReplayRefused):
        return "unsafe"
    if "ambiguous" in message or "review_required" in message:
        return "ambiguous_state"
    if "not found" in message or "timed out" in message or "locator" in message:
        return "stale_locator"
    if "expected" in message or "does not match" in message or "reached" in message:
        return "drift"
    return "failure"


def _run_path(
    path: list[GraphEdge],
    *,
    pid: int,
    diagnostics: dict[str, Any],
    graph_root: Path,
    registry_root: Path,
    replay_root: Path,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> list[str]:
    completed: list[str] = []
    for edge in path:
        replay_edge(
            edge.edge_id,
            pid,
            diagnostics,
            graph_root,
            registry_root,
            replay_root,
            timeout,
            max_depth,
            max_elements,
        )
        completed.append(edge.edge_id)
    return completed


def validate_graph(
    pid: int,
    diagnostics: dict[str, Any],
    graph_root: Path,
    registry_root: Path,
    validation_root: Path,
    *,
    sample_size: int,
    seed: int,
    edge_ids: list[str] | None,
    timeout: float,
    max_depth: int,
    max_elements: int,
) -> tuple[ValidationRecord, Path]:
    graph = graph_summary(graph_root, registry_root)
    by_id = {edge.edge_id: edge for edge in graph.edges}
    if edge_ids:
        missing = sorted(set(edge_ids) - set(by_id))
        if missing:
            raise ValidationError(f"Unknown edge IDs: {', '.join(missing)}")
        sampled = [by_id[edge_id] for edge_id in dict.fromkeys(edge_ids)]
    else:
        sampled = sample_edges(graph.edges, sample_size, seed)
    if not sampled:
        raise ValidationError("The graph has no safe reversible edges to validate.")

    run = create_observation_directory(validation_root)
    record = ValidationRecord(
        run_id=run.name,
        run_path=str(run.resolve()),
        started_at=_now(),
        graph_root=str(graph_root.resolve()),
        registry_root=str(registry_root.resolve()),
        sample_size=len(sampled),
        seed=seed,
        sampled_edge_ids=[edge.edge_id for edge in sampled],
    )
    write_json(run / "validation.json", record.model_dump(mode="json"))
    replay_root = run / "replays"

    for index, edge in enumerate(sampled, start=1):
        started_at = _now()
        restoration_edge_ids: list[str] = []
        replay_path: str | None = None
        observed_destination: str | None = None
        destination_verified = False
        return_verified = False
        source_restored = False
        try:
            current = capture_identified_state(
                pid,
                diagnostics,
                run / "checks" / f"{index:03d}-source",
                registry_root,
                max_depth,
                max_elements,
            )
            path = shortest_edge_path(graph.edges, current, edge.source_node_id)
            if path is None:
                raise ValidationError(
                    f"No safe graph path restores {current} to required source {edge.source_node_id}."
                )
            restoration_edge_ids.extend(
                _run_path(
                    path,
                    pid=pid,
                    diagnostics=diagnostics,
                    graph_root=graph_root,
                    registry_root=registry_root,
                    replay_root=replay_root,
                    timeout=timeout,
                    max_depth=max_depth,
                    max_elements=max_elements,
                )
            )
            source_restored = True
            replay_result, replay_dir = replay_edge(
                edge.edge_id,
                pid,
                diagnostics,
                graph_root,
                registry_root,
                replay_root,
                timeout,
                max_depth,
                max_elements,
            )
            replay_path = str(replay_dir.resolve())
            observed_destination = replay_result.get("observed_destination_node_id")
            destination_verified = bool(replay_result.get("success"))

            return_path = shortest_edge_path(
                graph.edges, edge.destination_node_id, edge.source_node_id
            )
            if return_path is None:
                raise ValidationError(
                    f"No safe graph path returns {edge.destination_node_id} to {edge.source_node_id}."
                )
            restoration_edge_ids.extend(
                _run_path(
                    return_path,
                    pid=pid,
                    diagnostics=diagnostics,
                    graph_root=graph_root,
                    registry_root=registry_root,
                    replay_root=replay_root,
                    timeout=timeout,
                    max_depth=max_depth,
                    max_elements=max_elements,
                )
            )
            returned = capture_identified_state(
                pid,
                diagnostics,
                run / "checks" / f"{index:03d}-returned",
                registry_root,
                max_depth,
                max_elements,
            )
            return_verified = returned == edge.source_node_id
            if not return_verified:
                raise GraphError(
                    f"Return drift: reached {returned}, expected {edge.source_node_id}."
                )
            status = "success"
            error = None
        except Exception as exc:
            status = classify_failure(exc)
            error = f"{type(exc).__name__}: {exc}"

        result = ValidationEdgeResult(
            edge_id=edge.edge_id,
            source_node_id=edge.source_node_id,
            destination_node_id=edge.destination_node_id,
            action=edge.action.semantic_description,
            status=status,
            started_at=started_at,
            completed_at=_now(),
            replay_path=replay_path,
            restoration_edge_ids=restoration_edge_ids,
            source_restored=source_restored,
            destination_verified=destination_verified,
            return_verified=return_verified,
            observed_destination_node_id=observed_destination,
            error=error,
        )
        record.results.append(result)
        write_json(run / "validation.json", record.model_dump(mode="json"))

    counts = {
        status: sum(result.status == status for result in record.results)
        for status in (
            "success",
            "failure",
            "drift",
            "stale_locator",
            "ambiguous_state",
            "unsafe",
        )
    }
    record.summary = {
        **counts,
        "attempted": len(record.results),
        "reliability": round(counts["success"] / len(record.results), 6),
    }
    record.success = counts["success"] == len(record.results)
    record.completed_at = _now()
    write_json(run / "validation.json", record.model_dump(mode="json"))
    return record, run
