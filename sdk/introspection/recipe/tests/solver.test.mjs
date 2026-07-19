import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { canonicalMetric, capabilityCatalog } from "../solver/catalog.mjs";
import { inspectGeometry } from "../solver/geometry.mjs";
import { evaluateDesign } from "../solver/model.mjs";
import { optimizeStudy, runStudy } from "../solver/optimizer.mjs";

const geometry = fileURLToPath(new URL("../assets/reference-aircraft.vsp3", import.meta.url));
const commonConstraints = (metric) => [
  ...(metric === "static_margin" ? [] : [{ metric: "static_margin", operator: ">=", value: 0.05 }]),
  { metric: "payload_kg", operator: ">=", value: metric === "payload_kg" || metric === "passengers" ? 15000 : 18000 },
  { metric: "range_km", operator: ">=", value: metric === "range_km" ? 5000 : 4500 },
  { metric: "mtow_kg", operator: "<=", value: 95000 },
];

test("catalog canonicalizes user-facing TLAR names", () => {
  assert.equal(canonicalMetric("Max take off weight"), "mtow_kg");
  assert.equal(canonicalMetric("number of people"), "passengers");
  assert.equal(canonicalMetric("Operating attitude"), "operating_attitude_deg");
  assert.ok(capabilityCatalog().metrics.length >= 16);
  assert.throws(() => canonicalMetric("magic efficiency"), /Unsupported metric/);
});

test("reference OpenVSP file is validated and fingerprinted", async () => {
  const inspected = await inspectGeometry(geometry);
  assert.ok(inspected.size_bytes > 500000);
  assert.ok(inspected.component_count >= 5);
  assert.ok(inspected.components.some((item) => item.name === "Wing" && item.type === "Wing"));
  assert.ok(inspected.sha256.match(/^[a-f0-9]{64}$/));
  assert.ok(inspected.extracted_design.wing_area_m2 > 100);
});

test("baseline multidisciplinary model is internally viable", () => {
  const baseline = evaluateDesign();
  assert.equal(baseline.diagnostics.geometry_valid, true);
  assert.equal(baseline.diagnostics.flight_envelope_valid, true);
  assert.ok(baseline.metrics.range_km > 5000);
  assert.ok(baseline.metrics.static_margin >= 0.05);
  assert.ok(baseline.metrics.max_speed_mps > baseline.metrics.cruise_speed_mps);
});

const targetCases = [
  ["payload_kg", 22000], ["passengers", 210], ["range_km", 8000], ["takeoff_distance_m", 1300],
  ["climb_rate_mps", 25], ["cruise_speed_mps", 230], ["max_speed_mps", 250], ["mtow_kg", 75000],
  ["landing_distance_m", 1050], ["max_altitude_m", 18500], ["operating_altitude_m", 12000],
  ["operating_attitude_deg", 1.5], ["lift_to_drag", 18], ["static_margin", 0.08],
  ["stall_speed_mps", 50], ["fuel_mass_kg", 25000],
];

for (const [metric, target] of targetCases) {
  test(`target pathway: ${metric}`, async () => {
    const result = await optimizeStudy({
      objective: { metric, mode: "target", target },
      constraints: commonConstraints(metric),
      budget: 900,
      seed: 43 + targetCases.findIndex(([name]) => name === metric),
    });
    assert.equal(result.status, "accepted", JSON.stringify(result.winner.assessment.constraints));
    assert.equal(result.winner.assessment.feasible, true);
    assert.equal(result.winner.assessment.objective_passed, true);
    assert.ok(result.winner.metrics.cruise_speed_mps <= result.winner.metrics.max_speed_mps);
    assert.ok(result.winner.metrics.operating_altitude_m <= result.winner.metrics.max_altitude_m);
    assert.equal(result.evidence.candidates_evaluated, 900);
  });
}

test("same seed and contract produce exactly the same winner", async () => {
  const study = { objective: { metric: "range_km", mode: "target", target: 7500 }, constraints: commonConstraints("range_km"), budget: 300, seed: 777 };
  const first = await optimizeStudy(study);
  const second = await optimizeStudy(study);
  assert.deepEqual(first.winner.design, second.winner.design);
  assert.deepEqual(first.winner.metrics, second.winner.metrics);
});

test("maximize and minimize modes improve feasible incumbents", async () => {
  const constraints = [{ metric: "payload", operator: ">=", value: 18000 }, { metric: "range", operator: ">=", value: 5000 }, { metric: "static margin", operator: ">=", value: 0.05 }];
  const maximize = await optimizeStudy({ objective: { metric: "lift_to_drag", mode: "maximize" }, constraints, budget: 600, seed: 90 });
  const minimize = await optimizeStudy({ objective: { metric: "mtow", mode: "minimize" }, constraints, budget: 600, seed: 91 });
  assert.equal(maximize.status, "accepted");
  assert.ok(maximize.winner.metrics.lift_to_drag > maximize.baseline.metrics.lift_to_drag);
  assert.equal(minimize.status, "accepted");
  assert.ok(minimize.winner.metrics.mtow_kg < minimize.baseline.metrics.mtow_kg);
});

test("runStudy writes replayable evidence and a modified valid OpenVSP candidate", async () => {
  const output = await mkdtemp(join(tmpdir(), "jango-study-"));
  const result = await runStudy({
    objective: { metric: "range", mode: "target", target: 8000 },
    constraints: [{ metric: "mtow", operator: "<=", value: 90000 }, { metric: "static margin", operator: ">=", value: 0.05 }],
    budget: 300,
    seed: 42,
  }, geometry, { outputDir: output, runId: "test-run" });
  assert.equal(result.status, "accepted");
  assert.equal(result.verification.deterministic_replay_match, true);
  const candidate = await inspectGeometry(result.artifacts.candidate_geometry);
  assert.notEqual(candidate.sha256, result.source_geometry.sha256);
  const manifest = JSON.parse(await readFile(result.artifacts.candidate_manifest, "utf8"));
  assert.equal(manifest.run_id, "test-run");
  const events = (await readFile(result.artifacts.events, "utf8")).trim().split("\n").map(JSON.parse);
  assert.equal(events[0].type, "STUDY_STARTED");
  assert.equal(events.at(-1).type, "STUDY_COMPLETED");
});

test("invalid or truncated geometry is rejected before optimization", async () => {
  const output = await mkdtemp(join(tmpdir(), "jango-invalid-"));
  const invalid = join(output, "invalid.vsp3");
  await writeFile(invalid, "<Vsp_Geometry>", "utf8");
  await assert.rejects(() => inspectGeometry(invalid), /Invalid or truncated/);
});
