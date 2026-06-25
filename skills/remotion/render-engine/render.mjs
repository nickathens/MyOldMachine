#!/usr/bin/env node
// Remotion render CLI. Bundles the project once, selects a composition, renders
// it to H.264 on the CPU (libx264 via Remotion's bundled ffmpeg). No GPU.
//
// Usage:
//   node render.mjs --comp TitleCard
//   node render.mjs --comp TitleCard --props '{"title":"WELCOME"}' --out /tmp/t.mp4
//   node render.mjs --comp BarChartBuild --props ./data.json --concurrency 50%
//
// --props accepts either an inline JSON object or a path to a .json file.
// Prints the absolute output path to stdout on success; logs go to stderr.

import { bundle } from "@remotion/bundler";
import * as renderer from "@remotion/renderer";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const { renderMedia, selectComposition } = renderer;
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const { values } = parseArgs({
  options: {
    comp: { type: "string" },
    props: { type: "string" },
    out: { type: "string" },
    codec: { type: "string", default: "h264" },
    concurrency: { type: "string" },
  },
});

if (!values.comp) {
  console.error(
    "Usage: node render.mjs --comp <CompositionId> [--props '<json>'|<file.json>] [--out <path.mp4>] [--codec h264] [--concurrency 50%]",
  );
  process.exit(1);
}

function resolveProps(p) {
  if (!p) return {};
  const trimmed = p.trim();
  // Inline JSON object or array.
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    return JSON.parse(trimmed);
  }
  // Otherwise it's a file path. Fail clearly when it's missing instead of
  // falling through to JSON.parse and reporting a confusing syntax error for
  // what is really a typo'd path.
  if (!fs.existsSync(p)) {
    throw new Error(`props file not found: ${p}`);
  }
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

// Remotion's concurrency is number | percent-string | null. parseArgs hands us
// a string, so coerce a bare integer ("2") to a Number while leaving "50%" alone.
function resolveConcurrency(c) {
  if (c == null) return null;
  if (/^\d+$/.test(c)) return Number(c);
  return c;
}

let inputProps;
try {
  inputProps = resolveProps(values.props);
} catch (e) {
  console.error(`[remotion] invalid --props: ${e.message}`);
  process.exit(1);
}

const out =
  values.out || path.resolve(__dirname, "out", `${values.comp}.mp4`);
fs.mkdirSync(path.dirname(out), { recursive: true });

// Older/newer renderer versions differ on browser provisioning. If an explicit
// ensureBrowser exists, use it (clear log line + first-run download); otherwise
// renderMedia downloads the headless shell on demand.
if (typeof renderer.ensureBrowser === "function") {
  console.error("[remotion] ensuring headless browser is available...");
  await renderer.ensureBrowser();
}

console.error("[remotion] bundling project...");
const serveUrl = await bundle({
  entryPoint: path.resolve(__dirname, "src/index.ts"),
});

console.error(`[remotion] selecting composition "${values.comp}"...`);
const composition = await selectComposition({
  serveUrl,
  id: values.comp,
  inputProps,
});

console.error(`[remotion] rendering ${composition.width}x${composition.height} -> ${out}`);
await renderMedia({
  composition,
  serveUrl,
  codec: values.codec,
  outputLocation: out,
  inputProps,
  concurrency: resolveConcurrency(values.concurrency),
  onProgress: ({ progress }) => {
    process.stderr.write(`\r[remotion] ${Math.round(progress * 100)}%   `);
  },
});

process.stderr.write("\n");
console.log(out);
