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
