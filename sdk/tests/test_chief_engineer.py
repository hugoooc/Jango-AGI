import sys
import tempfile
import unittest
from pathlib import Path

SDK = Path(__file__).resolve().parents[1]
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from chief_engineer.api import SyntheticApi
from chief_engineer.events import EventBus
from chief_engineer.fleet import LocalVmProvider
from chief_engineer.models import Domain, MetricSpec, ParameterSpec
from chief_engineer.orchestrator import ChiefEngineer
from chief_engineer.planner import EngineeringPlanner
from chief_engineer.mission import AutonomousChief


class ChiefEngineerTests(unittest.TestCase):
    def test_planner_decomposes_ld_and_stability(self):
        planner = EngineeringPlanner()
        goal = planner.parse_goal("Optimize L/D while staying stable")
        self.assertEqual(goal.objective, "L_D")
        self.assertIn(Domain.AERODYNAMICS, goal.domains)
        self.assertIn(Domain.STABILITY, goal.domains)
        self.assertEqual(goal.constraints[0].metric, "static_margin")

    def test_fleet_provisions_one_vm_per_worker(self):
        provider = LocalVmProvider()
        engine = ChiefEngineer(
            api_factory=lambda _handle: SyntheticApi(),
            vm_provider=provider,
        )
        report = engine.run("Optimize L/D while staying stable", max_iterations=1, worker_count=4)
        self.assertEqual(report.status, "complete")
        self.assertEqual(len(provider.provisioned), 5)  # baseline + four exploration workers
        self.assertTrue(all(item.state == "released" for item in provider.provisioned))

    def test_synthetic_api_loop_finds_feasible_improvement(self):
        engine = ChiefEngineer(api_factory=lambda _handle: SyntheticApi())
        report = engine.run("Optimize L/D while staying stable", max_iterations=3, worker_count=6)
        self.assertEqual(report.status, "complete")
        self.assertIsNotNone(report.winner_evaluation)
        self.assertTrue(report.winner_evaluation.feasible)
        self.assertGreater(report.winner_evaluation.metrics["L_D"], report.baseline.metrics["L_D"])
        self.assertGreaterEqual(len(report.iterations), 1)

    def test_api_worker_errors_are_returned_as_structured_evaluations(self):
        class BrokenApi(SyntheticApi):
            def evaluate(self, design, analyses):
                raise RuntimeError("solver unavailable")

        engine = ChiefEngineer(api_factory=lambda _handle: BrokenApi())
        report = engine.run("Optimize L/D while staying stable", max_iterations=1, worker_count=2)
        self.assertEqual(report.status, "failed")
        self.assertIn("solver unavailable", report.reason)

    def test_autonomous_mission_runs_sequential_parallel_teams(self):
        events = []
        chief = AutonomousChief(
            "mission-test",
            api_factory=lambda _handle: SyntheticApi(),
            event_sink=lambda event, payload: events.append((event, payload)),
        )
        outcome = chief.run(
            "Optimize L/D while staying stable",
            worker_budget=3,
            max_cycles=1,
        )
        spawned = [payload for event, payload in events if event == "team.spawned"]
        self.assertEqual([item["stage_id"] for item in spawned], ["geometry", "aerodynamics", "stability"])
        self.assertEqual([item["agent_count"] for item in spawned], [4, 6, 6])
        self.assertEqual(len(outcome.cycles[0].stages), 3)
        self.assertTrue(outcome.winner_evaluation.feasible)
        transfers = [payload for event, payload in events if event == "artifact.transferred"]
        self.assertTrue(any(item["from_stage"] == "geometry" and item["to_stage"] == "aerodynamics" for item in transfers))
        self.assertTrue(any(item["from_stage"] == "aerodynamics" and item["to_stage"] == "stability" for item in transfers))

    def test_worker_budget_batches_without_dropping_agents(self):
        events = []
        chief = AutonomousChief(
            "mission-batch",
            api_factory=lambda _handle: SyntheticApi(),
            event_sink=lambda event, payload: events.append((event, payload)),
        )
        chief.run("Optimize L/D while staying stable", worker_budget=2, max_cycles=1)
        completed = [payload["agent_id"] for event, payload in events if event == "agent.completed"]
        # baseline + geometry(4) + aero(6) + stability(6)
        self.assertEqual(len(completed), 17)

    def test_worker_progress_is_forwarded_with_agent_identity(self):
        class ProgressApi(SyntheticApi):
            def set_progress_sink(self, sink):
                self.sink = sink

            def evaluate(self, design, analyses):
                self.sink({"phase": "geometry-exported", "progress": 0.36})
                return super().evaluate(design, analyses)

        events = []
        chief = AutonomousChief(
            "mission-progress",
            api_factory=lambda _handle: ProgressApi(),
            event_sink=lambda event, payload: events.append((event, payload)),
        )
        chief.run("Optimize L/D while staying stable", worker_budget=2, max_cycles=1)
        progress = [payload for event, payload in events if event == "agent.progress"]
        self.assertEqual(len(progress), 17)
        self.assertTrue(all(item.get("agent_id") for item in progress))
        self.assertTrue(all(item["phase"] == "geometry-exported" for item in progress))

    def test_event_bus_replays_durable_event_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mission.events.jsonl"
            first = EventBus("mission-persist", path)
            first.publish("agent.progress", {"agent_id": "a-1", "progress": 0.36})
            first.publish("agent.completed", {"agent_id": "a-1"})
            first.close()

            restored = EventBus("mission-persist", path)
            snapshot = restored.snapshot()
            self.assertEqual([item.sequence for item in snapshot], [1, 2])
            self.assertEqual(snapshot[0].payload["progress"], 0.36)
            self.assertEqual(snapshot[1].event, "agent.completed")

    def test_capability_catalog_parses_non_demo_objectives(self):
        metrics = (
            MetricSpec("mass", "structures", "min", ("weight",), domains=(Domain.STRUCTURES, Domain.GEOMETRY)),
            MetricSpec("cg_x", "structures", "min", ("longitudinal cg",), domains=(Domain.STRUCTURES,)),
        )
        planner = EngineeringPlanner(metrics)
        mass = planner.parse_goal("Minimize weight")
        cg = planner.parse_goal("Move longitudinal CG below 2.5")
        self.assertEqual((mass.objective, mass.direction, mass.analyses), ("mass", "min", ("structures",)))
        self.assertEqual(cg.constraints[0].metric, "cg_x")
        self.assertEqual(cg.constraints[0].value, 2.5)

    def test_capability_driven_mass_mission_uses_declared_variables(self):
        parameters = (
            ParameterSpec(
                "wing_span", 5.0, 15.0, 0.10,
                domains=(Domain.GEOMETRY, Domain.STRUCTURES),
            ),
        )
        metrics = (
            MetricSpec("mass", "structures", "min", ("weight",), domains=(Domain.STRUCTURES, Domain.GEOMETRY)),
        )
        chief = AutonomousChief(
            "mission-mass",
            api_factory=lambda _handle: SyntheticApi(),
            parameter_specs=parameters,
            metric_specs=metrics,
        )
        outcome = chief.run("Minimize mass", initial_design={"wing_span": 10.0}, worker_budget=2, max_cycles=1)
        self.assertEqual(outcome.status, "complete")
        self.assertLess(outcome.winner_evaluation.metrics["mass"], outcome.baseline_evaluation.metrics["mass"])
        self.assertEqual(set(outcome.winner.design), {"wing_span"})

    def test_adapter_can_introduce_a_completely_new_domain(self):
        class ThermalApi:
            def evaluate(self, design, analyses):
                flow = float(design.get("coolant_flow", 2.0))
                return {"coolant_flow": flow, "peak_temperature": 400.0 - 20.0 * flow}

            def close(self):
                pass

        parameters = (
            ParameterSpec("coolant_flow", 0.5, 5.0, 0.20, domains=("thermal",)),
        )
        metrics = (
            MetricSpec(
                "peak_temperature", "thermal", "min", ("peak temperature",),
                domains=("thermal",),
            ),
        )
        chief = AutonomousChief(
            "mission-thermal",
            api_factory=lambda _handle: ThermalApi(),
            parameter_specs=parameters,
            metric_specs=metrics,
        )
        outcome = chief.run("Minimize peak temperature", worker_budget=3, max_cycles=1)
        self.assertEqual([stage.id for stage in outcome.plan.stages], ["thermal"])
        self.assertEqual(outcome.plan.stages[0].domain, "thermal")
        self.assertEqual(outcome.status, "complete")
        self.assertLess(
            outcome.winner_evaluation.metrics["peak_temperature"],
            outcome.baseline_evaluation.metrics["peak_temperature"],
        )


if __name__ == "__main__":
    unittest.main()
