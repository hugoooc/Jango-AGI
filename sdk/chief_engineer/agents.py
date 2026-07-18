"""Domain-specialist proposal agents.

Specialists do not drive a desktop and do not call the solver themselves. They
propose candidate parameter sets. The VM worker then evaluates those sets via
the direct engineering API, making proposal and execution independently
replaceable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .models import Candidate, Domain, DomainName, GoalSpec, ParameterSpec, domain_name


class SpecialistAgent(Protocol):
    domain: DomainName

    def propose(
        self,
        parent: Mapping[str, float],
        goal: GoalSpec,
        iteration: int,
        parent_id: str,
    ) -> Sequence[tuple[dict[str, float], str]]:
        ...


@dataclass
class CapabilityDrivenAgent:
    """Explore only variables declared executable by the selected software.

    This is the generic specialist used by the product runtime. Adding another
    CAD/CFD/FEA adapter extends the design space through ``ParameterSpec``
    records; no new hard-coded proposal class is required.
    """

    domain: DomainName
    parameters: tuple[ParameterSpec, ...]

    def propose(self, parent, goal, iteration, parent_id):
        current_domain = domain_name(self.domain)
        relevant = [
            item for item in self.parameters
            if current_domain in {domain_name(domain) for domain in item.domains}
        ]
        proposals = []
        shrink = max(0.35, 1.0 / max(1.0, iteration ** 0.5))
        # Alternate the signs per variable so small fan-outs still cover more
        # than one parameter instead of spending every slot on one dimension.
        directional = []
        for index, spec in enumerate(relevant):
            value = parent.get(spec.name)
            if value is None:
                continue
            current = float(value)
            scale = max(abs(current), (spec.maximum - spec.minimum) * 0.25, 1e-9)
            step = max(scale * spec.relative_step * shrink, (spec.maximum - spec.minimum) * 0.01)
            signs = (-1.0, 1.0) if index % 2 == 0 else (1.0, -1.0)
            for sign in signs:
                target = min(spec.maximum, max(spec.minimum, current + sign * step))
                if abs(target - current) <= 1e-12:
                    continue
                directional.append((spec, current, target))
        # Round-robin across parameters first, then explore the opposite sign.
        first = directional[::2]
        second = directional[1::2]
        for spec, current, target in first + second:
            design = {**parent, spec.name: target}
            direction = "increase" if target > current else "decrease"
            proposals.append((
                design,
                f"{direction} {spec.description or spec.name} from {current:.6g} to {target:.6g} "
                f"within the adapter-declared [{spec.minimum:.6g}, {spec.maximum:.6g}] bounds",
            ))
        return tuple(proposals)


@dataclass
class AerodynamicsAgent:
    domain: Domain = Domain.AERODYNAMICS

    def propose(self, parent, goal, iteration, parent_id):
        span = float(parent.get("wing_span", 10.0))
        sweep = float(parent.get("wing_sweep", 25.0))
        twist = float(parent.get("wing_twist", -1.0))
        return (
            ({**parent, "wing_span": span * 0.94}, "reduce induced drag with a compact span step"),
            ({**parent, "wing_span": span * 1.06}, "increase aspect ratio to improve L/D"),
            ({**parent, "wing_sweep": sweep - 5.0}, "test lower sweep for reduced profile drag"),
            ({**parent, "wing_sweep": sweep + 5.0}, "test higher sweep for the current flight point"),
            ({**parent, "wing_twist": twist + 1.0}, "test a washout change for lift distribution"),
        )


@dataclass
class StabilityAgent:
    domain: Domain = Domain.STABILITY

    def propose(self, parent, goal, iteration, parent_id):
        span = float(parent.get("htail_span", 7.0))
        arm = float(parent.get("htail_arm", 5.0))
        return (
            ({**parent, "htail_span": span * 0.90}, "reduce tail span while checking the stability margin"),
            ({**parent, "htail_span": span * 1.10}, "increase tail authority to protect static stability"),
            ({**parent, "htail_arm": arm * 0.90}, "test a shorter tail moment arm"),
            ({**parent, "htail_arm": arm * 1.10}, "test a longer tail moment arm"),
            ({**parent, "cg_shift": float(parent.get("cg_shift", 0.0)) - 0.25}, "move CG forward to recover margin"),
        )


@dataclass
class StructuralAgent:
    domain: Domain = Domain.STRUCTURES

    def propose(self, parent, goal, iteration, parent_id):
        thickness = float(parent.get("skin_thickness", 0.08))
        return (
            ({**parent, "skin_thickness": max(0.02, thickness * 0.90)}, "reduce skin thickness and measure mass impact"),
            ({**parent, "skin_thickness": thickness * 1.10}, "increase skin thickness for structural margin"),
        )


@dataclass
class GeometryAgent:
    domain: Domain = Domain.GEOMETRY

    def propose(self, parent, goal, iteration, parent_id):
        taper = float(parent.get("wing_taper", 0.55))
        span = float(parent.get("wing_span", 10.0))
        sweep = float(parent.get("wing_sweep", 25.0))
        return (
            ({**parent, "wing_taper": max(0.2, taper - 0.08)}, "test a lower taper ratio"),
            ({**parent, "wing_taper": min(1.0, taper + 0.08)}, "test a higher taper ratio"),
            ({**parent, "wing_span": span * 1.08}, "stretch the lifting surface for higher aspect ratio"),
            ({**parent, "wing_span": span * 0.92}, "test a compact wing geometry"),
            ({**parent, "wing_sweep": max(0.0, sweep - 4.0)}, "reduce sweep to test the low-speed design space"),
            ({**parent, "wing_sweep": sweep + 4.0}, "increase sweep to test the high-speed design space"),
        )


def default_specialists() -> dict[str, SpecialistAgent]:
    return {
        domain_name(Domain.AERODYNAMICS): AerodynamicsAgent(),
        domain_name(Domain.STABILITY): StabilityAgent(),
        domain_name(Domain.STRUCTURES): StructuralAgent(),
        domain_name(Domain.GEOMETRY): GeometryAgent(),
    }


def materialize_candidates(
    specialists: Mapping[str, SpecialistAgent],
    domains: Sequence[DomainName],
    parent: Mapping[str, float],
    parent_id: str,
    goal: GoalSpec,
    iteration: int,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    ordinal = 0
    for domain in domains:
        name = domain_name(domain)
        specialist = specialists[name]
        for design, rationale in specialist.propose(parent, goal, iteration, parent_id):
            candidates.append(Candidate(
                id=f"i{iteration}-{name[:4]}-{ordinal:02d}",
                parent_id=parent_id,
                design=dict(design),
                proposed_by=domain,
                rationale=rationale,
                iteration=iteration,
            ))
            ordinal += 1
    return candidates
