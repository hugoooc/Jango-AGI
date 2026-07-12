from app_mapper.config import OPENVSP
from app_mapper.macos.applications import find_processes


def test_detected_processes_match_the_openvsp_bundle() -> None:
    for process in find_processes(OPENVSP):
        assert process["bundle_id"] == OPENVSP.bundle_id
        assert process["localized_name"] == "OpenVSP"
        assert process["pid"] > 0
