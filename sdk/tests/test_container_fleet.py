import unittest
from unittest import mock

from orchestrator import docker_fleet as fleet


class FleetTests(unittest.TestCase):
    def test_wing_span_is_gui_only_vision_trajectory(self):
        actions = fleet.wing_span_actions(12.5)
        self.assertEqual(actions[0]["type"], "vision_click")
        self.assertEqual(actions[-4], {"type": "type", "text": "12.5"})
        self.assertEqual(actions[-1]["type"], "vision_assert")
        self.assertNotIn("openvsp", repr(actions).lower())

    @mock.patch.object(fleet, "_healthy", return_value=True)
    @mock.patch.object(fleet, "_container_running", return_value=True)
    def test_worker_ports_are_isolated(self, _running, _healthy):
        first = fleet.worker(1)
        second = fleet.worker(2)
        self.assertNotEqual(first.api_port, second.api_port)
        self.assertNotEqual(first.vnc_port, second.vnc_port)

    def test_mass_sweep_trajectory_sets_span_runs_massprop_and_reads(self):
        actions = fleet.wing_span_mass_actions(14.0)
        types = [a["type"] for a in actions]
        self.assertIn("vision_read", types)                 # it measures, not just sets
        self.assertEqual(actions[-1]["type"], "vision_read")
        self.assertIn("Total_Mass", actions[-1]["keys"])
        self.assertTrue(any("Mass Prop" in a.get("target", "") for a in actions))
        self.assertNotIn("openvsp", repr(actions).lower())  # GUI-only, no API

    def test_mass_from_job_extracts_vision_read_values(self):
        job = {"state": "done", "actions": [
            {"type": "vision_click"},
            {"type": "vision_read", "values": {"Total_Mass": 412.5, "X_Cg": 18.5}},
        ]}
        self.assertEqual(fleet.mass_from_job(job)["Total_Mass"], 412.5)


if __name__ == "__main__":
    unittest.main()
