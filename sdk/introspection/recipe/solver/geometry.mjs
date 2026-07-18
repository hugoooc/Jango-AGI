import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, dirname, extname } from "node:path";
import { DEFAULT_DESIGN } from "./catalog.mjs";

function numericTag(block, tag) {
  const match = block.match(new RegExp(`<${tag}\\s+Value="([+\\-0-9.eE]+)"`));
  return match ? Number(match[1]) : null;
}

function componentBlocks(xml) {
  return [...xml.matchAll(/^    <Geom>\s*$[\s\S]*?^    <\/Geom>\s*$/gm)].map((match) => match[0]);
}

function componentInfo(block) {
  const name = block.match(/<ParmContainer>[\s\S]*?<Name>([^<]+)<\/Name>/)?.[1] ?? "unnamed";
  const type = block.match(/<TypeName>([^<]+)<\/TypeName>/)?.[1] ?? "Unknown";
  return { name, type };
}

export async function inspectGeometry(path) {
  if (extname(path).toLowerCase() !== ".vsp3") throw new Error(`Geometry must be an OpenVSP .vsp3 file: ${path}`);
  const bytes = await readFile(path);
  const xml = bytes.toString("utf8");
  if (!xml.includes("<Vsp_Geometry") || !xml.includes("</Vsp_Geometry>")) throw new Error(`Invalid or truncated OpenVSP geometry: ${path}`);
  const blocks = componentBlocks(xml);
  if (!blocks.length) throw new Error(`No OpenVSP geometry components found in ${path}`);
  const components = blocks.map(componentInfo);
  const mainWing = blocks.find((block) => componentInfo(block).type === "Wing" && /^(wing|main wing|aile)$/i.test(componentInfo(block).name))
    ?? blocks.find((block) => componentInfo(block).type === "Wing" && !/(stabilizer|tail|empennage)/i.test(componentInfo(block).name));
  const modelName = xml.match(/<Vehicle>[\s\S]*?<Name>([^<]+)<\/Name>/)?.[1]
    ?? xml.match(/<ParmContainer>[\s\S]*?<Name>([^<]+)<\/Name>/)?.[1]
    ?? basename(path, ".vsp3");
  const area = mainWing ? numericTag(mainWing, "TotalArea") : null;
  const ar = mainWing ? numericTag(mainWing, "TotalAR") : null;
  const span = mainWing ? (numericTag(mainWing, "TotalProjectedSpan") ?? numericTag(mainWing, "TotalSpan")) : null;
  return {
    path,
    model_name: modelName,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    size_bytes: bytes.length,
    component_count: components.length,
    components,
    extracted_design: {
      ...DEFAULT_DESIGN,
      wing_area_m2: area && area > 20 ? area : DEFAULT_DESIGN.wing_area_m2,
      aspect_ratio: ar && ar > 2 ? ar : (area && span ? span ** 2 / area : DEFAULT_DESIGN.aspect_ratio),
    },
  };
}

function scaleTag(block, tags, factor) {
  const names = tags.join("|");
  return block.replace(new RegExp(`<(${names})\\s+Value="([+\\-0-9.eE]+)"`, "g"), (whole, tag, raw) => {
    const value = Number(raw);
    return Number.isFinite(value) ? `<${tag} Value="${(value * factor).toExponential(18)}"` : whole;
  });
}

function offsetTag(block, tags, delta, min = -89, max = 89) {
  const names = tags.join("|");
  return block.replace(new RegExp(`<(${names})\\s+Value="([+\\-0-9.eE]+)"`, "g"), (whole, tag, raw) => {
    const value = Math.max(min, Math.min(max, Number(raw) + delta));
    return Number.isFinite(value) ? `<${tag} Value="${value.toExponential(18)}"` : whole;
  });
}

export async function writeCandidateGeometry(sourcePath, destinationPath, candidateDesign, baselineDesign, metadata = {}) {
  await mkdir(dirname(destinationPath), { recursive: true });
  await copyFile(sourcePath, destinationPath);
  let xml = await readFile(destinationPath, "utf8");
  const blocks = componentBlocks(xml);
  const mainWing = blocks.find((block) => componentInfo(block).type === "Wing" && /^(wing|main wing|aile)$/i.test(componentInfo(block).name))
    ?? blocks.find((block) => componentInfo(block).type === "Wing" && !/(stabilizer|tail|empennage)/i.test(componentInfo(block).name));
  if (!mainWing) throw new Error("Cannot mutate geometry: no main Wing component found");

  const areaScale = candidateDesign.wing_area_m2 / baselineDesign.wing_area_m2;
  const spanScale = Math.sqrt(areaScale * candidateDesign.aspect_ratio / baselineDesign.aspect_ratio);
  const chordScale = areaScale / spanScale;
  const thicknessScale = candidateDesign.thickness_ratio / baselineDesign.thickness_ratio;
  const sweepDelta = candidateDesign.sweep_deg - baselineDesign.sweep_deg;
  let updatedWing = scaleTag(mainWing, ["Span", "ProjectedSpan", "TotalSpan", "TotalProjectedSpan"], spanScale);
  updatedWing = scaleTag(updatedWing, ["Root_Chord", "Tip_Chord", "Avg_Chord", "Chord", "TotalChord"], chordScale);
  updatedWing = scaleTag(updatedWing, ["Area", "TotalArea", "CurvedArea"], areaScale);
  updatedWing = scaleTag(updatedWing, ["ThickChord"], thicknessScale);
  updatedWing = offsetTag(updatedWing, ["Sweep"], sweepDelta, 0, 70);
  xml = xml.replace(mainWing, updatedWing);

  const tail = blocks.find((block) => componentInfo(block).type === "Wing" && /(horizontal stabilizer|horizontal tail|htail|empennage horizontal)/i.test(componentInfo(block).name));
  if (tail) {
    const tailAreaScale = candidateDesign.tail_area_ratio / baselineDesign.tail_area_ratio;
    let updatedTail = scaleTag(tail, ["Span", "ProjectedSpan", "TotalSpan", "TotalProjectedSpan", "Root_Chord", "Tip_Chord", "Avg_Chord", "Chord", "TotalChord"], Math.sqrt(tailAreaScale));
    updatedTail = scaleTag(updatedTail, ["Area", "TotalArea", "CurvedArea"], tailAreaScale);
    xml = xml.replace(tail, updatedTail);
  }

  const manifest = {
    generator: "Jango AGI task-local conceptual MDAO",
    generated_at: new Date().toISOString(),
    source: basename(sourcePath),
    design: candidateDesign,
    ...metadata,
  };
  const comment = `  <!-- JANGO_DESIGN ${JSON.stringify(manifest).replaceAll("--", "-")} -->\n`;
  xml = xml.replace(/(<Vsp_Geometry[^>]*>\s*)/, `$1\n${comment}`);
  await writeFile(destinationPath, xml, "utf8");
  await writeFile(`${destinationPath}.jango.json`, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  return inspectGeometry(destinationPath);
}
