"""OpenVSP workers + custom tools for the fleet.

Two ways to give the fleet capacity to run an OpenVSP mass-properties variant:

  desktop_worker()  — an agent on a local/remote desktop (`user_device`) that
                      operates the real OpenVSP GUI. One per machine.
  The agent does the seeing+clicking; our custom @tools do the machine-side
  determinism we learned the hard way (FLTK-safe field commit) so a numeric set
  is never lost to a mis-grounded click.

The task string tells the agent exactly what to do; `answer_schema` constrains
the reply to structured numbers we can aggregate.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))

from pydantic import BaseModel
from hai_agents import Agent, tool
from hai_agents.types.environment import Environment_Desktop

from .fleet import Variant, Worker


class MassResult(BaseModel):
    """Structured answer schema the agent must return."""
    total_mass: float
    cg_x: float
    cg_y: float
    cg_z: float
    span_before: float | None = None
    span_after: float | None = None


# ---- custom tools: machine-side determinism the model shouldn't guess -------

@tool(name="set_wing_span_exact",
      description="Set the OpenVSP Wing Plan-tab Span field to an exact value using "
                  "the FLTK-safe commit (triple-click, type, AppleScript Return). "
                  "Use this instead of clicking/typing yourself when you must set a "
                  "precise numeric span; a normal Return is ignored by OpenVSP fields.")
def set_wing_span_exact(value: float) -> str:
    import geom as G
    G.open_geom("Wing")
    G.goto_tab("Plan")
    before = G.read_field("the Span numeric value field in the Total Planform panel (top row)")
    G.set_field("the Span numeric value field in the Total Planform panel (top row)", value)
    after = G.read_field("the Span numeric value field in the Total Planform panel (top row)")
    return f"span {before} -> {after} (committed)"


DESKTOP_INSTRUCTIONS = """\
You operate OpenVSP, an aircraft-design desktop app, via mouse/keyboard/screenshots.
Goal: apply one geometry change, run Mass Properties, and report the numbers.
Rules:
- To set a precise wing span, call the tool `set_wing_span_exact` — do NOT type it
  yourself (OpenVSP ignores a normal Return; the tool commits correctly).
- Run Mass Properties via Analysis > Mass Prop..., click Compute, then read the
  Results panel (Total Mass, X/Y/Z Cg) directly off the screen.
- Return ONLY the structured answer.
"""


def desktop_worker(worker_id="local-desktop") -> Worker:
    """A worker that drives the real OpenVSP GUI on this (or a remote) machine.
    One per machine — the SDK serves one desktop bridge per host."""
    def build(variant: Variant) -> Agent:
        return Agent(
            name=f"vsp-{variant.name}",
            description="Operate OpenVSP to run a mass-properties variant.",
            environments=[Environment_Desktop(id="desktop", host="user_device")],
            instructions=DESKTOP_INSTRUCTIONS,
            answer_format=MassResult.model_json_schema(),
        )
    return Worker(id=worker_id, build_agent=build, tools=[set_wing_span_exact],
                  is_local_desktop=True, max_steps=60, max_time_s=1200.0)


def span_variants(base_task_geom="Wing") -> list[Variant]:
    """A small sweep: baseline + a few span scalings. Each is a plain-language task."""
    specs = (("baseline", None), ("+8% span", 1.08), ("+12% span", 1.12), ("+16% span", 1.16))
    variants = []
    for name, scale in specs:
        if scale is None:
            task = ("Run Mass Properties on the current model and report total mass and CG. "
                    "Do not change any geometry.")
        else:
            task = (f"First call set_wing_span_exact to scale the wing span by {scale}x "
                    f"(read the current span, multiply by {scale}, set it). Then run Mass "
                    f"Properties and report total mass, CG, and the span before/after.")
        variants.append(Variant(name=name, task=task, params={"scale": scale}))
    return variants
