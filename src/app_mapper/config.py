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
DEFAULT_AX_MAX_DEPTH = 12
DEFAULT_AX_MAX_ELEMENTS = 5_000
