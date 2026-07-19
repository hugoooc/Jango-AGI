import { appendFile, mkdir, writeFile } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { canonicalMetric, DEFAULT_DESIGN, DESIGN_BOUNDS, METRICS, normalizeStudy } from "./catalog.mjs";
import { inspectGeometry, writeCandidateGeometry } from "./geometry.mjs";
import { clamp, evaluateDesign, sanitizeDesign } from "./model.mjs";
import { renderDashboard } from "./dashboard.mjs";

function mulberry32(seed) {
  let value = seed >>> 0;
  return () => {
    value += 0x6d2b79f5;
    let t = value;
    t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61);
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

function normal(random) {
  const u = Math.max(1e-12, random());
  const v = random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function metricTolerance(metric, target, explicit) {
  if (explicit != null) return explicit;
  if (metric === "passengers") return 1;
  if (metric === "static_margin") return 0.002;
  if (metric === "operating_attitude_deg") return 0.15;
  return Math.max(Math.abs(target) * 0.015, 0.01);
}

function constraintStatus(metrics, constraint) {
  const actual = metrics[constraint.metric];
  const tolerance = constraint.tolerance ?? 0;
  let rawViolation = 0;
  if (constraint.operator === ">=" || constraint.operator === ">") rawViolation = Math.max(0, constraint.value - actual - tolerance);
  if (constraint.operator === "<=" || constraint.operator === "<") rawViolation = Math.max(0, actual - constraint.value - tolerance);
  if (constraint.operator === "==") rawViolation = Math.max(0, Math.abs(actual - constraint.value) - tolerance);
  const scale = Math.max(Math.abs(constraint.value), Math.abs(actual), 1);
  return { ...constraint, actual, unit: METRICS[constraint.metric].unit, passed: rawViolation <= 0, violation: rawViolation, normalized_violation: rawViolation / scale };
}

function redesignDistance(design, baseline) {
  let sum = 0;
  let count = 0;
  for (const [name, [min, max]] of Object.entries(DESIGN_BOUNDS)) {
    sum += ((design[name] - baseline[name]) / (max - min)) ** 2;
    count += 1;
  }
  return Math.sqrt(sum / count);
}

function assess(evaluation, study, baselineEvaluation, baselineDesign) {
  const metric = study.objective.metric;
  const actual = evaluation.metrics[metric];
  const baselineValue = baselineEvaluation.metrics[metric];
  const scale = Math.max(Math.abs(study.objective.target ?? baselineValue), Math.abs(baselineValue), 1);
  let objectiveLoss;
  let objectivePassed = true;
  let objectiveTolerance = null;
  if (study.objective.mode === "target") {
    objectiveTolerance = metricTolerance(metric, study.objective.target, study.objective.tolerance);
    objectiveLoss = Math.abs(actual - study.objective.target) / scale;
    objectivePassed = Math.abs(actual - study.objective.target) <= objectiveTolerance;
  } else if (study.objective.mode === "maximize") {
    objectiveLoss = -(actual - baselineValue) / scale;
  } else {
    objectiveLoss = (actual - baselineValue) / scale;
  }
  const constraints = study.constraints.map((constraint) => constraintStatus(evaluation.metrics, constraint));
  if (!evaluation.diagnostics.geometry_valid) {
    constraints.push({ metric: "geometry_valid", operator: "==", value: true, actual: false, unit: "boolean", passed: false, violation: 1, normalized_violation: 1, source: "solver gate" });
  }
  if (!evaluation.diagnostics.flight_envelope_valid) {
    constraints.push({ metric: "flight_envelope_valid", operator: "==", value: true, actual: false, unit: "boolean", passed: false, violation: 1, normalized_violation: 1, source: "solver gate" });
  }
  const violation = constraints.reduce((sum, item) => sum + item.normalized_violation ** 2, 0);
  const distance = redesignDistance(evaluation.design, baselineDesign);
  const score = objectiveLoss + 250 * violation + 0.035 * distance;
  return { score, objective_loss: objectiveLoss, objective_passed: objectivePassed, objective_tolerance: objectiveTolerance, constraints, feasible: constraints.every((item) => item.passed), redesign_distance: distance };
}

function seededCandidate(index, budget, random, baseline, locked, elites) {
  if (index === 0) return { ...baseline };
  const candidate = {};
  const globalPhase = index < Math.max(64, budget * 0.42) || elites.length === 0;
  const parent = elites[Math.floor(random() * elites.length)]?.evaluation.design ?? baseline;
  const progress = index / budget;
  const mutationSigma = 0.19 * (1 - progress) + 0.018;
  for (const [name, [min, max]] of Object.entries(DESIGN_BOUNDS)) {
    if (locked.has(name)) {
      candidate[name] = baseline[name];
    } else if (globalPhase) {
      const stratum = (index * 0.61803398875 + random()) % 1;
      candidate[name] = min + stratum * (max - min);
    } else {
      candidate[name] = clamp(parent[name] + normal(random) * mutationSigma * (max - min), min, max);
    }
  }
  return candidate;
}

function injectObjectiveHint(candidate, study, baseline, index) {
  if (study.objective.mode !== "target" || index % 5 !== 1) return candidate;
  const target = study.objective.target;
  if (study.objective.metric === "payload_kg") candidate.payload_kg = clamp(target, ...DESIGN_BOUNDS.payload_kg);
  if (study.objective.metric === "passengers") candidate.payload_kg = clamp(target * 100 + 40, ...DESIGN_BOUNDS.payload_kg);
  if (study.objective.metric === "operating_altitude_m") candidate.operating_altitude_m = clamp(target, ...DESIGN_BOUNDS.operating_altitude_m);
  if (study.objective.metric === "fuel_mass_kg") candidate.fuel_mass_kg = clamp(target, ...DESIGN_BOUNDS.fuel_mass_kg);
  if (study.objective.metric === "operating_attitude_deg") {
    candidate.wing_incidence_deg = clamp(baseline.wing_incidence_deg - (target - 2.5), ...DESIGN_BOUNDS.wing_incidence_deg);
  }
  return candidate;
}

function roundMetrics(metrics) {
  return Object.fromEntries(Object.entries(metrics).map(([name, value]) => [name, Number(value.toFixed(name === "static_margin" ? 5 : 3))]));
}

export async function optimizeStudy(rawStudy, baselineDesign = DEFAULT_DESIGN, options = {}) {
  const study = normalizeStudy(rawStudy);
  const baseline = sanitizeDesign({ ...DEFAULT_DESIGN, ...baselineDesign, ...(rawStudy.baseline_design ?? {}) });
  const baselineEvaluation = evaluateDesign(baseline);
  const random = mulberry32(study.seed);
  const locked = new Set(study.locked_variables);
  const ranked = [];
  const convergence = [];
  let elites = [];
  let incumbent = null;

  for (let index = 0; index < study.budget; index += 1) {
    if (options.signal?.aborted) throw new Error("Study aborted");
    let design = seededCandidate(index, study.budget, random, baseline, locked, elites);
    design = injectObjectiveHint(design, study, baseline, index);
    const evaluation = evaluateDesign(design);
    const assessment = assess(evaluation, study, baselineEvaluation, baseline);
    const item = { candidate: index, evaluation, assessment };
    ranked.push(item);
    if (!incumbent || assessment.score < incumbent.assessment.score) {
      incumbent = item;
      convergence.push({ candidate: index, score: assessment.score, feasible: assessment.feasible, objective: evaluation.metrics[study.objective.metric] });
      await options.onEvent?.({ type: "INCUMBENT", candidate: index, score: assessment.score, feasible: assessment.feasible, objective: evaluation.metrics[study.objective.metric] });
    }
    if (index % 24 === 23) {
      elites = [...ranked].sort((a, b) => a.assessment.score - b.assessment.score).slice(0, 18);
      await options.onProgress?.({ completed: index + 1, budget: study.budget, incumbent: incumbent.evaluation.metrics[study.objective.metric], feasible: incumbent.assessment.feasible });
    }
  }

  const accepted = ranked.filter((item) => item.assessment.feasible && item.assessment.objective_passed).sort((a, b) => a.assessment.score - b.assessment.score);
  const feasible = ranked.filter((item) => item.assessment.feasible).sort((a, b) => a.assessment.score - b.assessment.score);
  const winner = accepted[0] ?? feasible[0] ?? [...ranked].sort((a, b) => a.assessment.score - b.assessment.score)[0];
  const status = winner.assessment.feasible && winner.assessment.objective_passed ? "accepted" : "infeasible";
  const baselineAssessment = assess(baselineEvaluation, study, baselineEvaluation, baseline);
  return {
    schema_version: "jango.study.v1",
    status,
    fidelity: "conceptual/preliminary multidisciplinary estimate; requires higher-fidelity verification before design release",
    study,
    baseline: { design: baseline, metrics: roundMetrics(baselineEvaluation.metrics), diagnostics: baselineEvaluation.diagnostics, assessment: baselineAssessment },
    winner: { candidate: winner.candidate, design: winner.evaluation.design, metrics: roundMetrics(winner.evaluation.metrics), diagnostics: winner.evaluation.diagnostics, assessment: winner.assessment },
    deltas: Object.fromEntries(Object.keys(winner.evaluation.metrics).map((metric) => [metric, Number((winner.evaluation.metrics[metric] - baselineEvaluation.metrics[metric]).toFixed(3))])),
    evidence: { candidates_evaluated: ranked.length, feasible_candidates: feasible.length, target_satisfying_candidates: accepted.length, convergence },
  };
}

function reportMarkdown(result, geometry) {
  const objective = result.study.objective;
  const rows = result.winner.assessment.constraints.map((item) => `| ${item.metric} | ${item.operator} ${item.value} | ${Number(item.actual).toFixed(3)} | ${item.passed ? "PASS" : "FAIL"} |`).join("\n") || "| — | — | — | PASS |";
  return `# Jango engineering decision\n\n**Status:** ${result.status.toUpperCase()}  \n**Objective:** ${objective.mode} ${objective.metric}${objective.target == null ? "" : ` = ${objective.target}`}  \n**Geometry:** ${geometry.model_name} (${geometry.sha256.slice(0, 12)})  \n**Fidelity:** ${result.fidelity}\n\n## Baseline → winner\n\n- Objective: ${result.baseline.metrics[objective.metric]} → ${result.winner.metrics[objective.metric]} ${METRICS[objective.metric].unit}\n- Candidate: ${result.winner.candidate} / ${result.evidence.candidates_evaluated - 1}\n- Feasible candidates: ${result.evidence.feasible_candidates}\n\n## Constraint gate\n\n| Metric | Requirement | Actual | Gate |\n|---|---:|---:|---|\n${rows}\n\n## Design changes\n\n\`\`\`json\n${JSON.stringify(result.winner.design, null, 2)}\n\`\`\`\n\nThis result is reproducible from \`study.json\`, \`source-geometry.json\`, and \`events.jsonl\`. It is preliminary-design evidence, not certification evidence.\n`;
}

export async function runStudy(rawStudy, geometryPath, options = {}) {
  const geometry = await inspectGeometry(resolve(geometryPath));
  const runId = options.runId ?? `study-${new Date().toISOString().replace(/[:.]/g, "-")}-${Math.random().toString(16).slice(2, 8)}`;
  const runDir = resolve(options.outputDir ?? join(process.cwd(), "jango-runs", runId));
  await mkdir(runDir, { recursive: true });
  const eventPath = join(runDir, "events.jsonl");
  const startedAt = new Date().toISOString();
  let sequence = 0;
  const emit = async (event) => {
    const record = { sequence: sequence++, timestamp: new Date().toISOString(), run_id: runId, ...event };
    await appendFile(eventPath, `${JSON.stringify(record)}\n`, "utf8");
    await options.onEvent?.(record);
  };
  await writeFile(join(runDir, "study.json"), `${JSON.stringify(normalizeStudy(rawStudy), null, 2)}\n`, "utf8");
  await writeFile(join(runDir, "source-geometry.json"), `${JSON.stringify(geometry, null, 2)}\n`, "utf8");
  await writeFile(join(runDir, "dashboard.html"), renderDashboard({ runId, study: normalizeStudy(rawStudy), geometry }), "utf8");
  await emit({ type: "STUDY_STARTED", objective: normalizeStudy(rawStudy).objective, geometry: geometry.model_name });
  await emit({ type: "DOMAIN_FANOUT", domains: ["geometry", "aerodynamics", "weights", "performance", "stability"] });
  const result = await optimizeStudy(rawStudy, geometry.extracted_design, {
    signal: options.signal,
    onEvent: async (event) => emit(event),
    onProgress: options.onProgress,
  });
  const candidatePath = join(runDir, "candidate.vsp3");
  const candidateGeometry = await writeCandidateGeometry(resolve(geometryPath), candidatePath, result.winner.design, result.baseline.design, { run_id: runId, status: result.status, objective: result.study.objective });
  const verification = evaluateDesign(result.winner.design);
  result.run_id = runId;
  result.started_at = startedAt;
  result.completed_at = new Date().toISOString();
  result.source_geometry = geometry;
  result.artifacts = {
    run_directory: runDir,
    study: join(runDir, "study.json"),
    result: join(runDir, "result.json"),
    report: join(runDir, "report.md"),
    events: eventPath,
    candidate_geometry: candidatePath,
    candidate_manifest: `${candidatePath}.jango.json`,
    dashboard: join(runDir, "dashboard.html"),
  };
  result.verification = { deterministic_replay_match: JSON.stringify(roundMetrics(verification.metrics)) === JSON.stringify(result.winner.metrics), candidate_geometry_sha256: candidateGeometry.sha256 };
  await emit({ type: "VERIFICATION_COMPLETED", status: result.status, replay_match: result.verification.deterministic_replay_match });
  await emit({ type: "STUDY_COMPLETED", status: result.status, objective_value: result.winner.metrics[result.study.objective.metric] });
  await writeFile(join(runDir, "result.json"), `${JSON.stringify(result, null, 2)}\n`, "utf8");
  await writeFile(join(runDir, "report.md"), reportMarkdown(result, geometry), "utf8");
  await writeFile(join(runDir, "dashboard.html"), renderDashboard({ runId, study: result.study, geometry, result }), "utf8");
  return result;
}

export function parseConstraintExpression(metric, operator, value, tolerance = 0) {
  return { metric: canonicalMetric(metric), operator, value: Number(value), tolerance: Number(tolerance) };
}
