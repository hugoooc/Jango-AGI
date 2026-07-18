import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { readdir, readFile } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { capabilityCatalog } from "../solver/catalog.mjs";
import { inspectGeometry } from "../solver/geometry.mjs";
import { evaluateDesign } from "../solver/model.mjs";
import { optimizeStudy, runStudy } from "../solver/optimizer.mjs";

const referenceGeometry = fileURLToPath(new URL("../assets/reference-aircraft.vsp3", import.meta.url));
const runsRoot = () => join(process.cwd(), "jango-runs");
const textResult = (value: unknown, details: unknown = value) => ({
  content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }],
  details,
});

const constraintSchema = Type.Object({
  metric: Type.String({ description: "Canonical metric or catalog alias" }),
  operator: Type.String({ description: ">=, <=, ==, >, or <" }),
  value: Type.Number(),
  tolerance: Type.Optional(Type.Number()),
});

export default function jangoExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: "jango_capabilities",
    label: "Jango capability catalog",
    description: "List every task-local aircraft metric, design variable, unit, bound, objective mode, and fidelity limitation supported by Jango. Call this before planning a new objective.",
    promptSnippet: "Discover Jango's canonical TLAR metrics and task-local solver bounds",
    promptGuidelines: ["Call jango_capabilities before translating a new aircraft objective; never invent an unsupported metric."],
    parameters: Type.Object({}),
    async execute() {
      return textResult({ ...capabilityCatalog(), reference_geometry: referenceGeometry });
    },
  });

  pi.registerTool({
    name: "jango_inspect_geometry",
    label: "Inspect OpenVSP geometry",
    description: "Validate and inspect an OpenVSP .vsp3 file, returning immutable provenance, components, and extracted baseline wing design.",
    promptSnippet: "Inspect and fingerprint the source OpenVSP geometry",
    promptGuidelines: ["Use jango_inspect_geometry before running a study with a user-supplied .vsp3 file."],
    parameters: Type.Object({
      geometry_path: Type.Optional(Type.String({ description: "Task-local .vsp3 path; omit for the bundled reference aircraft" })),
    }),
    async execute(_toolCallId, params) {
      return textResult(await inspectGeometry(resolve(params.geometry_path ?? referenceGeometry)));
    },
  });

  pi.registerTool({
    name: "jango_evaluate_design",
    label: "Evaluate aircraft design",
    description: "Run one deterministic multidisciplinary conceptual analysis from a JSON object of design variables. Useful for specialist what-if analysis before the integrated optimization.",
    promptSnippet: "Evaluate one geometry/propulsion/mission design across every TLAR",
    parameters: Type.Object({
      design_json: Type.String({ description: "JSON object containing any catalog design variables; omitted variables use the reference baseline" }),
    }),
    async execute(_toolCallId, params) {
      return textResult(evaluateDesign(JSON.parse(params.design_json)));
    },
  });

  pi.registerTool({
    name: "jango_run_study",
    label: "Run autonomous TLAR study",
    description: "Execute a seeded constrained multidisciplinary optimization entirely inside this Introspection task. Produces an event ledger, result, report, manifest, and mutated OpenVSP candidate.",
    promptSnippet: "Run a task-local constrained TLAR optimization and create evidence artifacts",
    promptGuidelines: [
      "Use jango_run_study only after the Chief has stated the canonical objective, units, constraints, and acceptance condition.",
      "A jango_run_study status of infeasible is a valid engineering result and must never be presented as success.",
    ],
    parameters: Type.Object({
      objective_metric: Type.String({ description: "Canonical metric or catalog alias" }),
      objective_mode: Type.Optional(Type.String({ description: "target, maximize, or minimize" })),
      objective_target: Type.Optional(Type.Number()),
      objective_tolerance: Type.Optional(Type.Number()),
      constraints: Type.Optional(Type.Array(constraintSchema)),
      baseline_tlars: Type.Optional(Type.Record(Type.String(), Type.Number(), { description: "Existing TLAR values to preserve unless they are the objective" })),
      preserve_baseline_tlars: Type.Optional(Type.Boolean()),
      geometry_path: Type.Optional(Type.String({ description: "Task-local .vsp3 path; omit for the bundled reference aircraft" })),
      budget: Type.Optional(Type.Number({ minimum: 64, maximum: 20000 })),
      seed: Type.Optional(Type.Number()),
      locked_variables: Type.Optional(Type.Array(Type.String())),
    }),
    async execute(_toolCallId, params, signal, onUpdate) {
      const study = {
        objective: { metric: params.objective_metric, mode: params.objective_mode, target: params.objective_target, tolerance: params.objective_tolerance },
        constraints: params.constraints ?? [],
        baseline_tlars: params.baseline_tlars ?? {},
        preserve_baseline_tlars: params.preserve_baseline_tlars ?? true,
        budget: params.budget ?? 1200,
        seed: params.seed ?? 42,
        locked_variables: params.locked_variables ?? [],
      };
      const result = await runStudy(study, resolve(params.geometry_path ?? referenceGeometry), {
        signal,
        onProgress: ({ completed, budget, incumbent, feasible }) => onUpdate?.({
          content: [{ type: "text", text: `Exploration ${completed}/${budget} · incumbent ${Number(incumbent).toFixed(3)} · ${feasible ? "feasible" : "constraint search"}` }],
          details: { completed, budget, incumbent, feasible },
        }),
      });
      const summary = {
        run_id: result.run_id,
        status: result.status,
        objective: result.study.objective,
        baseline_objective: result.baseline.metrics[result.study.objective.metric],
        winner_objective: result.winner.metrics[result.study.objective.metric],
        constraints: result.winner.assessment.constraints,
        candidates_evaluated: result.evidence.candidates_evaluated,
        feasible_candidates: result.evidence.feasible_candidates,
        winner_metrics: result.winner.metrics,
        winner_design: result.winner.design,
        verification: result.verification,
        artifacts: result.artifacts,
        fidelity: result.fidelity,
      };
      return textResult(summary, result);
    },
  });

  pi.registerTool({
    name: "jango_read_study",
    label: "Read Jango study",
    description: "Read the authoritative result of a prior study in this task by run id.",
    promptSnippet: "Read a prior Jango result and its constraint gates",
    parameters: Type.Object({ run_id: Type.String() }),
    async execute(_toolCallId, params) {
      const runId = basename(params.run_id);
      const result = JSON.parse(await readFile(join(runsRoot(), runId, "result.json"), "utf8"));
      return textResult(result);
    },
  });

  pi.registerTool({
    name: "jango_list_studies",
    label: "List Jango studies",
    description: "List task-local engineering studies and their current status.",
    promptSnippet: "List study runs persisted in this Introspection task",
    parameters: Type.Object({}),
    async execute() {
      let entries: string[] = [];
      try { entries = await readdir(runsRoot()); } catch { return textResult({ studies: [] }); }
      const studies = [];
      for (const runId of entries.sort()) {
        try {
          const result = JSON.parse(await readFile(join(runsRoot(), runId, "result.json"), "utf8"));
          studies.push({ run_id: runId, status: result.status, objective: result.study.objective, completed_at: result.completed_at });
        } catch { studies.push({ run_id: runId, status: "running" }); }
      }
      return textResult({ studies });
    },
  });

  pi.registerTool({
    name: "jango_validate_tlar_suite",
    label: "Validate all TLAR pathways",
    description: "Run deterministic end-to-end acceptance examples for every supported design TLAR. Intended for deployment smoke tests, not as a substitute for the user's actual study.",
    promptSnippet: "Exercise every TLAR optimization pathway in one reproducible validation suite",
    parameters: Type.Object({ budget_per_case: Type.Optional(Type.Number({ minimum: 128, maximum: 3000 })), seed: Type.Optional(Type.Number()) }),
    async execute(_toolCallId, params, signal, onUpdate) {
      const cases: Array<[string, number]> = [
        ["payload_kg", 22000], ["passengers", 210], ["range_km", 8000], ["takeoff_distance_m", 1300],
        ["climb_rate_mps", 25], ["cruise_speed_mps", 230], ["max_speed_mps", 250], ["mtow_kg", 75000],
        ["landing_distance_m", 1050], ["max_altitude_m", 18500], ["operating_altitude_m", 12000],
        ["operating_attitude_deg", 1.5], ["lift_to_drag", 18],
        ["static_margin", 0.08], ["stall_speed_mps", 50], ["fuel_mass_kg", 25000],
      ];
      const results = [];
      for (let index = 0; index < cases.length; index += 1) {
        if (signal?.aborted) throw new Error("Validation suite aborted");
        const [metric, target] = cases[index];
        onUpdate?.({ content: [{ type: "text", text: `Validating ${index + 1}/${cases.length}: ${metric}` }], details: { index, metric } });
        const result = await optimizeStudy({
          objective: { metric, mode: "target", target },
          constraints: [
            { metric: "static_margin", operator: ">=", value: 0.05 },
            { metric: "payload_kg", operator: ">=", value: metric === "payload_kg" || metric === "passengers" ? 15000 : 18000 },
            { metric: "range_km", operator: ">=", value: metric === "range_km" ? 5000 : 4500 },
            { metric: "mtow_kg", operator: "<=", value: 95000 },
          ],
          budget: params.budget_per_case ?? 900,
          seed: (params.seed ?? 43) + index,
        });
        results.push({ metric, target, status: result.status, actual: result.winner.metrics[metric], feasible_candidates: result.evidence.feasible_candidates });
      }
      return textResult({ status: results.every((item) => item.status === "accepted") ? "passed" : "failed", cases: results });
    },
  });
}
