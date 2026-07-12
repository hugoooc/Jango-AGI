import unittest
from unittest import mock

from orchestrator import docker_fleet as fleet


class FleetTests(unittest.TestCase):
    def test_wing_span_is_gui_only_vision_trajectory(self):
        actions = fleet.wing_span_actions(12.5)
        self.assertEqual(actions[0]["type"], "vision_click")
        self.assertEqual(actions[-3], {"type": "type", "text": "12.5"})
        self.assertNotIn("openvsp", repr(actions).lower())

    @mock.patch.object(fleet, "_healthy", return_value=True)
    @mock.patch.object(fleet, "_container_running", return_value=True)
    def test_worker_ports_are_isolated(self, _running, _healthy):
        first = fleet.worker(1)
        second = fleet.worker(2)
        self.assertNotEqual(first.api_port, second.api_port)
        self.assertNotEqual(first.vnc_port, second.vnc_port)


if __name__ == "__main__":
    unittest.main()
