from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetApplication:
    name: str
    bundle_id: str
    bundle_path: Path


OPENVSP = TargetApplication(
    name="OpenVSP",
    bundle_id="org.openvsp.OpenVSP",
    bundle_path=Path("/Applications/OpenVSP.app"),
)

DEFAULT_ARTIFACT_ROOT = Path("artifacts/observations")
DEFAULT_TRANSITION_ARTIFACT_ROOT = Path("artifacts/transitions")
DEFAULT_INTERPRETATION_ARTIFACT_ROOT = Path("artifacts/interpretations")
DEFAULT_NODE_OBSERVATION_ROOT = Path("artifacts/node-observations")
DEFAULT_NODE_REGISTRY_ROOT = Path("artifacts/nodes")
DEFAULT_GRAPH_ROOT = Path("artifacts/graph")
DEFAULT_GRAPH_RUN_ROOT = Path("artifacts/graph-runs")
DEFAULT_REPLAY_ROOT = Path("artifacts/replays")
DEFAULT_DISCOVERY_ROOT = Path("artifacts/discovery-runs")
DEFAULT_EXPLORATION_ROOT = Path("artifacts/explorations")
DEFAULT_VALIDATION_ROOT = Path("artifacts/validations")
DEFAULT_VIEWER_ROOT = Path("artifacts/viewer")
DEFAULT_AX_MAX_DEPTH = 12
DEFAULT_AX_MAX_ELEMENTS = 5_000
