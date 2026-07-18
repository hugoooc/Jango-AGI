import { DEFAULT_DESIGN, DESIGN_BOUNDS } from "./catalog.mjs";

const G = 9.80665;
const R = 287.05287;
const GAMMA = 1.4;

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function atmosphere(altitudeM) {
  const h = clamp(altitudeM, 0, 20000);
  let temperature;
  let pressure;
  if (h <= 11000) {
    temperature = 288.15 - 0.0065 * h;
    pressure = 101325 * Math.pow(temperature / 288.15, 5.2558797);
  } else {
    temperature = 216.65;
    pressure = 22632.06 * Math.exp((-G * (h - 11000)) / (R * temperature));
  }
  return { temperature_k: temperature, pressure_pa: pressure, density_kgm3: pressure / (R * temperature), sound_speed_mps: Math.sqrt(GAMMA * R * temperature) };
}

export function sanitizeDesign(candidate = {}) {
  const design = { ...DEFAULT_DESIGN };
  for (const [name, bounds] of Object.entries(DESIGN_BOUNDS)) {
    const value = Number(candidate[name] ?? design[name]);
    design[name] = clamp(Number.isFinite(value) ? value : design[name], bounds[0], bounds[1]);
  }
  return design;
}

function levelFlightMaxSpeed(weightN, area, density, cd0, inducedK, thrustN, soundSpeed, sweepDeg) {
  const discriminant = thrustN ** 2 - 4 * cd0 * inducedK * weightN ** 2;
  if (discriminant <= 0) return 0;
  const qArea = (thrustN + Math.sqrt(discriminant)) / (2 * cd0);
  const aerodynamic = Math.sqrt((2 * qArea) / (density * area));
  const dragRiseMach = clamp(0.78 + 0.0032 * (sweepDeg - 20), 0.74, 0.9);
  return Math.min(aerodynamic, dragRiseMach * soundSpeed);
}

function serviceCeiling(design, weightN, area, cd0, inducedK) {
  let ceiling = 0;
  for (let altitude = 0; altitude <= 20000; altitude += 100) {
    const atm = atmosphere(altitude);
    const sigma = atm.density_kgm3 / 1.225;
    const thrust = design.thrust_n * Math.pow(sigma, 0.72);
    const minDrag = 2 * weightN * Math.sqrt(cd0 * inducedK);
    const speed = clamp(Math.sqrt((2 * weightN) / (atm.density_kgm3 * area * 0.7)), 80, 260);
    const climb = ((thrust - minDrag) * speed) / weightN;
    if (climb >= 0.5) ceiling = altitude;
  }
  return ceiling;
}

export function evaluateDesign(rawDesign = {}) {
  const d = sanitizeDesign(rawDesign);
  const area = d.wing_area_m2;
  const span = Math.sqrt(d.aspect_ratio * area);
  const structuralMass = 65 * area ** 1.12 * (d.aspect_ratio / 9.7) ** 0.35 * (1 + 0.003 * d.sweep_deg ** 1.2);
  const propulsionPenalty = 0.075 * Math.max(0, d.thrust_n - 242000) / G;
  const emptyMass = d.fixed_mass_kg + structuralMass + 1800 * d.tail_area_ratio + propulsionPenalty;
  const mtow = emptyMass + d.payload_kg + d.fuel_mass_kg;
  const weight = mtow * G;

  const oswald = clamp(0.9 - 0.0028 * Math.max(0, d.sweep_deg - 15) - 0.07 * (1 - d.taper_ratio), 0.66, 0.88);
  const cd0 = 0.0205 + 0.000009 * (d.sweep_deg - 22) ** 2 + 0.00012 * ((d.thickness_ratio - 0.12) * 100) ** 2;
  const inducedK = 1 / (Math.PI * d.aspect_ratio * oswald);
  const cruiseAtm = atmosphere(d.operating_altitude_m);
  const targetCruiseCl = clamp(0.62 + 0.018 * (d.sweep_deg - 25) / 10, 0.52, 0.72);
  const unconstrainedCruiseSpeed = Math.sqrt((2 * weight) / (cruiseAtm.density_kgm3 * area * targetCruiseCl));
  const sigmaCruise = cruiseAtm.density_kgm3 / 1.225;
  const cruiseThrust = d.thrust_n * Math.pow(sigmaCruise, 0.72);
  const maxSpeed = levelFlightMaxSpeed(weight, area, cruiseAtm.density_kgm3, cd0, inducedK, cruiseThrust, cruiseAtm.sound_speed_mps, d.sweep_deg);
  const cruiseSpeed = Math.min(unconstrainedCruiseSpeed, 0.95 * maxSpeed);
  const cruiseCl = cruiseSpeed > 0 ? weight / (0.5 * cruiseAtm.density_kgm3 * cruiseSpeed ** 2 * area) : 99;
  const cruiseCd = cd0 + inducedK * cruiseCl ** 2;
  const liftToDrag = cruiseCl / cruiseCd;

  const usableFuel = Math.max(1, d.fuel_mass_kg * 0.94);
  const finalWeight = Math.max(emptyMass + d.payload_kg + 0.06 * d.fuel_mass_kg, 1) * G;
  const tsfc = 0.00017 * (1 + 0.003 * Math.max(0, d.thrust_n / 242000 - 1));
  const rangeKm = (cruiseSpeed / tsfc) * liftToDrag * Math.log(weight / finalWeight) / 1000;

  const rho0 = 1.225;
  const stallTakeoff = Math.sqrt((2 * weight) / (rho0 * area * d.clmax_takeoff));
  const liftoffSpeed = 1.18 * stallTakeoff;
  const takeoffDrag = 0.5 * rho0 * (0.7 * liftoffSpeed) ** 2 * area * (cd0 + 0.055);
  const groundAcceleration = G * Math.max(0.045, d.thrust_n / weight - 0.025 - takeoffDrag / weight);
  const takeoffDistance = liftoffSpeed ** 2 / (2 * groundAcceleration) + 300;

  const landingMass = emptyMass + d.payload_kg + 0.12 * d.fuel_mass_kg;
  const landingWeight = landingMass * G;
  const stallLanding = Math.sqrt((2 * landingWeight) / (rho0 * area * d.clmax_landing));
  const approachSpeed = 1.3 * stallLanding;
  const landingDistance = approachSpeed ** 2 / (2 * 0.28 * G) + 320;

  const climbAtm = atmosphere(1000);
  const climbSpeed = Math.max(1.25 * stallTakeoff, 120);
  const qClimb = 0.5 * climbAtm.density_kgm3 * climbSpeed ** 2;
  const climbCl = weight / (qClimb * area);
  const climbDrag = qClimb * area * (cd0 + inducedK * climbCl ** 2);
  const climbThrust = d.thrust_n * 0.72 * Math.pow(climbAtm.density_kgm3 / 1.225, 0.18);
  const climbRate = Math.max(0, ((climbThrust - climbDrag) * climbSpeed) / weight);

  const finiteWingSlope = (2 * Math.PI * d.aspect_ratio) / (2 + Math.sqrt(4 + d.aspect_ratio ** 2 * (1 + Math.tan(d.sweep_deg * Math.PI / 180) ** 2)));
  const operatingAttitude = cruiseCl / finiteWingSlope * 180 / Math.PI - 2.0 - d.wing_incidence_deg;
  const staticMargin = 0.035 + 0.34 * (d.tail_area_ratio - 0.15) - 0.0009 * Math.max(0, d.sweep_deg - 25);
  const maxAltitude = serviceCeiling(d, weight, area, cd0, inducedK);
  const wingFuelCapacity = 0.5 * area * (span / d.aspect_ratio) * d.thickness_ratio * 800;

  const geometryValid = Number(
    d.taper_ratio >= 0.16 && d.taper_ratio <= 0.52 &&
    d.thickness_ratio >= 0.085 && d.thickness_ratio <= 0.18 &&
    span <= 62 && wingFuelCapacity >= d.fuel_mass_kg &&
    staticMargin > 0
  );
  const flightEnvelopeValid = maxSpeed > 0 && cruiseSpeed <= maxSpeed && d.operating_altitude_m <= maxAltitude;

  return {
    design: d,
    metrics: {
      payload_kg: d.payload_kg,
      passengers: Math.floor(d.payload_kg / 100),
      range_km: rangeKm,
      takeoff_distance_m: takeoffDistance,
      climb_rate_mps: climbRate,
      cruise_speed_mps: cruiseSpeed,
      max_speed_mps: maxSpeed,
      mtow_kg: mtow,
      landing_distance_m: landingDistance,
      max_altitude_m: maxAltitude,
      operating_altitude_m: d.operating_altitude_m,
      operating_attitude_deg: operatingAttitude,
      lift_to_drag: liftToDrag,
      static_margin: staticMargin,
      stall_speed_mps: stallLanding,
      fuel_mass_kg: d.fuel_mass_kg,
    },
    diagnostics: {
      geometry_valid: Boolean(geometryValid),
      flight_envelope_valid: Boolean(flightEnvelopeValid),
      empty_mass_kg: emptyMass,
      span_m: span,
      wing_loading_kgm2: mtow / area,
      thrust_to_weight: d.thrust_n / weight,
      cd0,
      oswald_efficiency: oswald,
      cruise_cl: cruiseCl,
      usable_fuel_kg: usableFuel,
      estimated_wing_fuel_capacity_kg: wingFuelCapacity,
      fidelity: "deterministic conceptual/preliminary design",
    },
  };
}
