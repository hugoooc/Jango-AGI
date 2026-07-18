export const METRICS = {
  payload_kg: { unit: "kg", label: "Payload", preserve: ">=", aliases: ["payload", "payload weight", "charge utile"] },
  passengers: { unit: "people", label: "Passenger capacity", preserve: ">=", aliases: ["people", "persons", "pax", "passenger capacity", "number of people", "personnes"] },
  range_km: { unit: "km", label: "Still-air range", preserve: ">=", aliases: ["range", "flight range", "autonomy", "autonomie", "distance franchissable"] },
  takeoff_distance_m: { unit: "m", label: "Take-off distance", preserve: "<=", aliases: ["takeoff distance", "take off distance", "max takeoff distance", "takeoff field length", "distance decollage"] },
  climb_rate_mps: { unit: "m/s", label: "Initial climb rate", preserve: ">=", aliases: ["climb rate", "minimal climb rate", "rate of climb", "taux de montee"] },
  cruise_speed_mps: { unit: "m/s", label: "Cruise true airspeed", preserve: ">=", aliases: ["cruise speed", "v cruise", "vitesse de croisiere"] },
  max_speed_mps: { unit: "m/s", label: "Maximum level-flight speed", preserve: ">=", aliases: ["max speed", "maximum speed", "v max", "vitesse max"] },
  mtow_kg: { unit: "kg", label: "Maximum take-off weight", preserve: "<=", aliases: ["mtow", "max takeoff weight", "max take off weight", "maximum takeoff weight", "maximum take off weight", "masse maximale decollage"] },
  landing_distance_m: { unit: "m", label: "Landing distance", preserve: "<=", aliases: ["landing distance", "max landing distance", "landing field length", "distance atterrissage"] },
  max_altitude_m: { unit: "m", label: "Service ceiling", preserve: ">=", aliases: ["max altitude", "maximum altitude", "service ceiling", "ceiling", "plafond"] },
  operating_altitude_m: { unit: "m", label: "Operating altitude", preserve: "==", aliases: ["operating altitude", "cruise altitude", "altitude operationnelle"] },
  operating_attitude_deg: { unit: "deg", label: "Cruise body attitude", preserve: "==", aliases: ["operating attitude", "cruise attitude", "body attitude", "assiette"] },
  lift_to_drag: { unit: "-", label: "Cruise lift-to-drag ratio", preserve: ">=", aliases: ["l/d", "ld", "lift drag", "lift to drag", "finesse"] },
  static_margin: { unit: "MAC", label: "Static margin", preserve: ">=", aliases: ["static margin", "stability margin", "marge statique"] },
  stall_speed_mps: { unit: "m/s", label: "Landing stall speed", preserve: "<=", aliases: ["stall speed", "v stall", "vitesse decrochage"] },
  fuel_mass_kg: { unit: "kg", label: "Usable fuel mass", preserve: "<=", aliases: ["fuel", "fuel mass", "carburant"] },
};

export const DEFAULT_DESIGN = {
  wing_area_m2: 132.0,
  aspect_ratio: 9.70,
  sweep_deg: 25.0,
  taper_ratio: 0.28,
  thickness_ratio: 0.12,
  tail_area_ratio: 0.24,
  thrust_n: 242000,
  fuel_mass_kg: 21000,
  payload_kg: 18000,
  fixed_mass_kg: 22000,
  clmax_takeoff: 2.05,
  clmax_landing: 2.55,
  operating_altitude_m: 10668,
  wing_incidence_deg: 2.0,
};

export const DESIGN_BOUNDS = {
  wing_area_m2: [85, 235],
  aspect_ratio: [6.5, 15.0],
  sweep_deg: [12, 40],
  taper_ratio: [0.16, 0.52],
  thickness_ratio: [0.085, 0.18],
  tail_area_ratio: [0.15, 0.42],
  thrust_n: [140000, 520000],
  fuel_mass_kg: [6000, 65000],
  payload_kg: [3000, 50000],
  fixed_mass_kg: [18000, 32000],
  clmax_takeoff: [1.55, 3.0],
  clmax_landing: [1.9, 3.6],
  operating_altitude_m: [0, 14500],
  wing_incidence_deg: [-1, 6],
};

export const DEFAULT_TLARS = {
  payload_kg: 18000,
  passengers: 180,
  range_km: 5000,
  takeoff_distance_m: 2600,
  climb_rate_mps: 12,
  cruise_speed_mps: 220,
  max_speed_mps: 245,
  mtow_kg: 82000,
  landing_distance_m: 1800,
  max_altitude_m: 12000,
  operating_altitude_m: 10668,
  operating_attitude_deg: 3.0,
  static_margin: 0.05,
};

function normalizedName(value) {
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const METRIC_ALIASES = new Map();
for (const [name, spec] of Object.entries(METRICS)) {
  for (const alias of [name, name.replaceAll("_", " "), ...spec.aliases]) {
    METRIC_ALIASES.set(normalizedName(alias), name);
  }
}

export function canonicalMetric(value) {
  const key = METRIC_ALIASES.get(normalizedName(value));
  if (!key) {
    throw new Error(`Unsupported metric '${value}'. Use one of: ${Object.keys(METRICS).join(", ")}`);
  }
  return key;
}

export function capabilityCatalog() {
  return {
    execution: "Introspection task-local deterministic conceptual MDAO",
    fidelity: "conceptual/preliminary design; not certification evidence",
    metrics: Object.entries(METRICS).map(([name, spec]) => ({ name, ...spec })),
    design_variables: Object.entries(DESIGN_BOUNDS).map(([name, bounds]) => ({ name, min: bounds[0], max: bounds[1] })),
    objective_modes: ["target", "maximize", "minimize"],
    constraint_operators: [">=", "<=", "==", ">", "<"],
  };
}

export function normalizeStudy(input = {}) {
  if (!input.objective) throw new Error("A study objective is required");
  const rawObjective = typeof input.objective === "string" ? { metric: input.objective } : input.objective;
  const objective = {
    metric: canonicalMetric(rawObjective.metric),
    mode: rawObjective.mode ?? (rawObjective.target != null ? "target" : "maximize"),
    target: rawObjective.target == null ? null : Number(rawObjective.target),
    tolerance: rawObjective.tolerance == null ? null : Math.abs(Number(rawObjective.tolerance)),
  };
  if (!["target", "maximize", "minimize"].includes(objective.mode)) throw new Error(`Invalid objective mode '${objective.mode}'`);
  if (objective.mode === "target" && !Number.isFinite(objective.target)) throw new Error("A numeric target is required in target mode");

  const constraints = (input.constraints ?? []).map((item) => ({
    metric: canonicalMetric(item.metric),
    operator: item.operator ?? item.op ?? ">=",
    value: Number(item.value),
    tolerance: Math.abs(Number(item.tolerance ?? 0)),
    source: item.source ?? "user",
  }));
  for (const constraint of constraints) {
    if (![">=", "<=", "==", ">", "<"].includes(constraint.operator) || !Number.isFinite(constraint.value)) {
      throw new Error(`Invalid constraint for '${constraint.metric}'`);
    }
  }

  const baselineTlars = {};
  for (const [name, value] of Object.entries(input.baseline_tlars ?? input.baselineTlars ?? {})) {
    baselineTlars[canonicalMetric(name)] = Number(value);
  }
  if (input.preserve_baseline_tlars ?? input.preserveBaselineTlars ?? true) {
    for (const [metric, value] of Object.entries(baselineTlars)) {
      if (metric === objective.metric || constraints.some((item) => item.metric === metric)) continue;
      constraints.push({ metric, operator: METRICS[metric].preserve, value, tolerance: 0, source: "preserved TLAR" });
    }
  }

  const budget = Math.max(64, Math.min(20000, Math.round(Number(input.budget ?? 1200))));
  const seed = Math.round(Number(input.seed ?? 42));
  const lockedVariables = [...new Set(input.locked_variables ?? input.lockedVariables ?? [])];
  for (const name of lockedVariables) if (!(name in DESIGN_BOUNDS)) throw new Error(`Unknown locked design variable '${name}'`);

  return { objective, constraints, baseline_tlars: baselineTlars, budget, seed, locked_variables: lockedVariables };
}
