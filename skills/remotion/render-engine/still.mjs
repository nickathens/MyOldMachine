// Quick single-frame still renderer for visual iteration. Mirrors render.mjs's
// browser provisioning and --props handling (inline JSON or a .json file path).
//   node still.mjs --comp CardFan3D --frame 100 --out /tmp/x.png
//   node still.mjs --comp BarChartBuild --props ./data.json
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { bundle } from "@remotion/bundler";
import * as renderer from "@remotion/renderer";

const { renderStill, selectComposition } = renderer;
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const { values } = parseArgs({
  options: {
    comp: { type: "string" },
    frame: { type: "string" },
    out: { type: "string" },
    props: { type: "string" },
  },
});

// --props accepts an inline JSON object/array or a path to a .json file.
function resolveProps(p) {
  if (!p) return {};
  const trimmed = p.trim();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    return JSON.parse(trimmed);
  }
  if (!fs.existsSync(p)) {
    throw new Error(`props file not found: ${p}`);
  }
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

const comp = values.comp ?? "CardFan3D";
const frame = values.frame ? Number(values.frame) : 100;
// Resolved here so the path printed at the end is absolute, as the SKILL
// promises; a relative --out still lands where it did, under the working folder.
const out = values.out
  ? path.resolve(values.out)
  : path.resolve(__dirname, "out", `${comp}_f${frame}.png`);

let inputProps;
try {
  inputProps = resolveProps(values.props);
} catch (e) {
  console.error(`[remotion] invalid --props: ${e.message}`);
  process.exit(1);
}

fs.mkdirSync(path.dirname(out), { recursive: true });

// Remotion keeps its headless browser beside the nearest package.json above the
// WORKING folder (node_modules/.remotion), or in <cwd>/.remotion when there is
// none. Run from the bot's folder, as every SKILL example is, that meant a
// second 92 MB download into the repo root. Every path given on the command
// line (--out, a --props file) is read above, so from here on work from the
// engine itself.
process.chdir(__dirname);

if (typeof renderer.ensureBrowser === "function") {
  console.error("[remotion] ensuring headless browser is available...");
  await renderer.ensureBrowser();
}

console.error("[remotion] bundling project...");
const serveUrl = await bundle({
  entryPoint: path.resolve(__dirname, "src/index.ts"),
});
const composition = await selectComposition({ serveUrl, id: comp, inputProps });
await renderStill({ composition, serveUrl, output: out, frame, inputProps });
console.log(out);
