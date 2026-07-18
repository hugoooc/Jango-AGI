"""Hacknation API-first chief-engineer orchestration."""

from .agents import (
    AerodynamicsAgent,
    GeometryAgent,
    StabilityAgent,
    StructuralAgent,
)
from .adapters import AdapterManifest, CompositeSimulationApi, SoftwareAdapterRegistry
from .api import HttpSimulationApi, OpenVSPDirectApi, SimulationApi, SubprocessOpenVSPApi, SyntheticApi
from .events import EventBus, MissionEvent
from .fleet import ApiFleet, DockerVmProvider, LocalVmProvider, VmHandle, VmProvider, VmSpec
from .mission import AutonomousChief, MissionOutcome
from .models import (
    Candidate,
    Constraint,
    Domain,
    Evaluation,
    ExplorationPlan,
    GoalSpec,
    OptimizationReport,
)
from .orchestrator import ChiefEngineer, synthetic_engineer
from .planner import EngineeringPlanner
from .reasoning import (
    ChiefDecision,
    MissionPlan,
    OpenAICompatibleReasoningProvider,
    RuleBasedReasoningProvider,
    StageSpec,
)

__all__ = [
    "AerodynamicsAgent",
    "AdapterManifest",
    "ApiFleet",
    "AutonomousChief",
    "Candidate",
    "ChiefEngineer",
    "Constraint",
    "ChiefDecision",
    "CompositeSimulationApi",
    "Domain",
    "EngineeringPlanner",
    "EventBus",
    "Evaluation",
    "ExplorationPlan",
    "GeometryAgent",
    "GoalSpec",
    "HttpSimulationApi",
    "LocalVmProvider",
    "DockerVmProvider",
    "OpenVSPDirectApi",
    "SubprocessOpenVSPApi",
    "OpenAICompatibleReasoningProvider",
    "OptimizationReport",
    "MissionEvent",
    "MissionOutcome",
    "MissionPlan",
    "RuleBasedReasoningProvider",
    "SimulationApi",
    "StabilityAgent",
    "StageSpec",
    "StructuralAgent",
    "SyntheticApi",
    "SoftwareAdapterRegistry",
    "VmHandle",
    "VmProvider",
    "VmSpec",
    "synthetic_engineer",
]
