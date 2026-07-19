#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { capabilityCatalog } from "./catalog.mjs";
import { inspectGeometry } from "./geometry.mjs";
import { runStudy } from "./optimizer.mjs";

function args(argv) {
  const parsed = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) parsed._.push(token);
    else parsed[token.slice(2)] = argv[index + 1]?.startsWith("--") ? true : argv[++index];
  }
  return parsed;
}

const input = args(process.argv.slice(2));
const command = input._[0] ?? "catalog";

if (command === "catalog") {
  console.log(JSON.stringify(capabilityCatalog(), null, 2));
} else if (command === "inspect") {
  if (!input.geometry) throw new Error("--geometry is required");
  console.log(JSON.stringify(await inspectGeometry(resolve(input.geometry)), null, 2));
} else if (command === "run") {
  if (!input.geometry || !input.study) throw new Error("--geometry and --study are required");
  const study = JSON.parse(await readFile(resolve(input.study), "utf8"));
  const result = await runStudy(study, resolve(input.geometry), { outputDir: input.output ? resolve(input.output) : undefined });
  console.log(JSON.stringify({ status: result.status, run_id: result.run_id, objective: result.study.objective, winner: result.winner.metrics, artifacts: result.artifacts }, null, 2));
} else {
  throw new Error(`Unknown command '${command}'. Use catalog, inspect, or run.`);
}
