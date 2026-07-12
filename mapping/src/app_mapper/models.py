from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UIState(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    type: Literal["workspace", "dialog", "manager", "menu", "unknown"]
    description: str = Field(min_length=1, max_length=1_000)


class NavigationTarget(StrictModel):
    label: str = Field(min_length=1, max_length=200)
    action_type: Literal["click"]
    bounding_box: list[int] = Field(
        min_length=4,
        max_length=4,
        description="[left, top, right, bottom] normalized to integers in [0, 1000]",
    )
    expected_destination: str = Field(min_length=1, max_length=500)
    risk: Literal["safe_navigation", "model_modifying", "destructive", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: Literal["visual", "accessibility", "hybrid"]
    accessibility_match: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_box(self) -> "NavigationTarget":
        if any(coordinate < 0 or coordinate > 1_000 for coordinate in self.bounding_box):
            raise ValueError("bounding_box coordinates must be in [0, 1000]")
        left, top, right, bottom = self.bounding_box
        if left >= right or top >= bottom:
            raise ValueError("bounding_box must have positive width and height")
        return self


class HoloInterpretation(StrictModel):
    state: UIState
    navigation_targets: list[NavigationTarget] = Field(max_length=50)


class RejectedTarget(StrictModel):
    target: NavigationTarget
    reason: str


class ValidatedInterpretation(StrictModel):
    state: UIState
    navigation_targets: list[NavigationTarget]
    rejected_targets: list[RejectedTarget]
    read_only: Literal[True] = True
    actions_executed: Literal[0] = 0


class NodeFingerprints(StrictModel):
    structural_sha256: str
    visual_dhash: str
    semantic_sha256: str
    structural_variants: list[str]
    visual_variants: list[str]
    semantic_variants: list[str]
    normalization_version: Literal[1] = 1


class NodeObservation(StrictModel):
    path: str
    observed_at: str
    structural_sha256: str
    visual_dhash: str
    semantic_sha256: str


class NodeRecord(StrictModel):
    schema_version: Literal[1] = 1
    node_id: str
    semantic_name: str
    semantic_description: str
    state_type: Literal["workspace", "dialog", "manager", "menu", "unknown"]
    semantic_source: Literal["holo", "deterministic"]
    application: dict[str, Any]
    window_summary: dict[str, Any]
    stable_landmarks: list[str]
    semantic_landmarks: list[str] = Field(default_factory=list)
    interactive_control_summary: dict[str, int]
    fingerprints: NodeFingerprints
    representative_screenshot: str
    first_seen: str
    last_seen: str
    observation_count: int = Field(ge=1)
    observations: list[NodeObservation]


class MatchCandidate(StrictModel):
    node_id: str
    score: float = Field(ge=0.0, le=1.0)
    compatible: bool
    reasons: list[str]


class IdentityDecision(StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["new", "matched", "ambiguous"]
    node_id: str | None
    observation_path: str
    candidates: list[MatchCandidate]
    review_required: bool


class EdgeAction(StrictModel):
    action_key: str = Field(min_length=1, max_length=100)
    semantic_description: str
    mechanism: Literal["macos_accessibility", "quartz_coordinate"] = "macos_accessibility"
    accessibility_locator: dict[str, Any]
    keyboard_locator: str | None = None
    visual_locator: dict[str, Any] | None = None
    preconditions: list[str]
    expected_postconditions: list[str]
    risk: Literal["safe_navigation"] = "safe_navigation"
    reversible: Literal[True] = True
    reverse_action_key: str = Field(min_length=1, max_length=100)


class EdgeEvidence(StrictModel):
    recorded_at: str
    run_path: str
    source_observation: str
    destination_observation: str
    returned_observation: str


class ReplayStatistics(StrictModel):
    attempts: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    refused_wrong_source: int = Field(default=0, ge=0)
    last_attempt_at: str | None = None
    last_success_at: str | None = None


class GraphEdge(StrictModel):
    schema_version: Literal[1] = 1
    edge_id: str
    source_node_id: str
    destination_node_id: str
    action: EdgeAction
    evidence: list[EdgeEvidence]
    replay: ReplayStatistics = Field(default_factory=ReplayStatistics)


class GraphNodeSummary(StrictModel):
    node_id: str
    semantic_name: str
    state_type: Literal["workspace", "dialog", "manager", "menu", "unknown"]
    representative_screenshot: str


class GraphRecord(StrictModel):
    schema_version: Literal[1] = 1
    directed: Literal[True] = True
    updated_at: str
    nodes: list[GraphNodeSummary]
    edges: list[GraphEdge]


class DiscoveryCandidate(StrictModel):
    candidate_id: str
    label: str
    origin: Literal["holo", "human_allowlist"]
    classification: Literal[
        "informational_dialog",
        "manager_window",
        "view_display_panel",
        "menu",
        "tab",
        "excluded",
        "unknown",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    locator: dict[str, Any]
    policy_decision: Literal["approved", "rejected"]
    policy_reasons: list[str]
    status: Literal["proposed", "rejected", "succeeded", "failed", "skipped"]
    destination_node_id: str | None = None
    return_verified: bool = False
    trace_path: str | None = None
    error: str | None = None


class DiscoveryRecord(StrictModel):
    schema_version: Literal[1] = 1
    started_at: str
    completed_at: str | None = None
    mode: Literal["plan", "execute"]
    source_node_id: str | None = None
    interpretation_path: str | None = None
    max_candidates: int = Field(ge=1)
    candidates: list[DiscoveryCandidate]
    summary: dict[str, int]
    success: bool = False
    actions_executed: int = Field(default=0, ge=0)


class ExplorationBounds(StrictModel):
    max_depth: int = Field(ge=1, le=3)
    max_nodes: int = Field(ge=2, le=100)
    max_actions: int = Field(ge=1, le=1_000)
    max_seconds: float = Field(gt=0, le=86_400)
    max_retries: int = Field(ge=0, le=5)
    risk: Literal["safe_navigation"] = "safe_navigation"
    allow_relaunch: bool = False


class ExplorationTask(StrictModel):
    task_id: str
    source_node_id: str | None = None
    depth: int = Field(ge=1)
    action_type: Literal["menu", "about"]
    target: str
    status: Literal["queued", "running", "succeeded", "failed", "skipped"] = "queued"
    attempts: int = Field(default=0, ge=0)
    phase: Literal[
        "queued", "source_verified", "opened", "destination_verified", "closed", "complete"
    ] = "queued"
    destination_node_id: str | None = None
    return_verified: bool = False
    loop_detected: bool = False
    edge_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    trace_path: str | None = None


class ExplorationState(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    run_path: str
    app: Literal["OpenVSP"] = "OpenVSP"
    status: Literal["running", "paused", "completed", "failed", "stopped_bound"]
    started_at: str
    updated_at: str
    completed_at: str | None = None
    bounds: ExplorationBounds
    graph_root: str
    registry_root: str
    target_pid: int
    focus_guard_bundle_id: str | None = None
    source_node_id: str | None = None
    discovered_node_ids: list[str] = Field(default_factory=list)
    expanded_node_ids: list[str] = Field(default_factory=list)
    queue: list[ExplorationTask]
    actions_executed: int = Field(default=0, ge=0)
    tasks_completed: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    stop_reason: str | None = None


class ValidationEdgeResult(StrictModel):
    edge_id: str
    source_node_id: str
    destination_node_id: str
    action: str
    status: Literal[
        "success",
        "failure",
        "drift",
        "stale_locator",
        "ambiguous_state",
        "unsafe",
    ]
    started_at: str
    completed_at: str
    replay_path: str | None = None
    restoration_edge_ids: list[str] = Field(default_factory=list)
    source_restored: bool = False
    destination_verified: bool = False
    return_verified: bool = False
    observed_destination_node_id: str | None = None
    error: str | None = None


class ValidationRecord(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    run_path: str
    started_at: str
    completed_at: str | None = None
    graph_root: str
    registry_root: str
    sample_size: int = Field(ge=1)
    seed: int
    sampled_edge_ids: list[str]
    results: list[ValidationEdgeResult] = Field(default_factory=list)
    summary: dict[str, int | float] = Field(default_factory=dict)
    success: bool = False


class MenuControl(StrictModel):
    control_id: str
    path: list[str] = Field(min_length=2)
    top_level_menu: str
    title: str
    enabled: bool
    identifier: str | None = None
    has_submenu: bool = False
    classification: Literal[
        "safe_dialog",
        "submenu",
        "view_state_change",
        "model_modifying",
        "file_operation",
        "analysis_workflow",
        "external_or_system",
        "destructive",
        "unknown",
    ]
    policy_decision: Literal["approved", "review_required", "rejected", "not_applicable"]
    policy_reasons: list[str]


class MenuInventory(StrictModel):
    schema_version: Literal[1] = 1
    captured_at: str
    app: Literal["OpenVSP"] = "OpenVSP"
    app_version: str | None = None
    read_only: Literal[True] = True
    actions_executed: Literal[0] = 0
    source_path: str
    truncated: bool
    controls: list[MenuControl]
    summary: dict[str, int]


class SafeExpansionCandidate(StrictModel):
    control_id: str
    path: list[str]
    status: Literal["proposed", "already_mapped", "succeeded", "failed", "skipped"]
    source_node_id: str | None = None
    destination_node_id: str | None = None
    edge_ids: list[str] = Field(default_factory=list)
    return_verified: bool = False
    trace_path: str | None = None
    error: str | None = None


class SafeExpansionRecord(StrictModel):
    schema_version: Literal[1] = 1
    started_at: str
    completed_at: str | None = None
    mode: Literal["plan", "execute"]
    inventory_path: str
    max_candidates: int = Field(ge=1)
    candidates: list[SafeExpansionCandidate]
    actions_executed: int = Field(default=0, ge=0)
    summary: dict[str, int] = Field(default_factory=dict)
    success: bool = False


class RecursiveCandidate(StrictModel):
    candidate_id: str
    parent_path: list[str]
    label: str
    depth: int = Field(ge=2)
    status: Literal["proposed", "already_mapped", "succeeded", "failed", "skipped"]
    source_node_id: str | None = None
    destination_node_id: str | None = None
    edge_ids: list[str] = Field(default_factory=list)
    return_verified: bool = False
    error: str | None = None


class RecursiveExplorationRecord(StrictModel):
    schema_version: Literal[1] = 1
    started_at: str
    completed_at: str | None = None
    mode: Literal["plan", "execute"]
    candidates: list[RecursiveCandidate]
    actions_executed: int = Field(default=0, ge=0)
    summary: dict[str, int] = Field(default_factory=dict)
    success: bool = False
