// Quick single-frame still renderer for visual iteration.
//   node still.mjs --comp CardFan3D --frame 100 --out /tmp/x.png
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const { values } = parseArgs({
  options: {
    comp: { type: "string" },
    frame: { type: "string" },
    out: { type: "string" },
    props: { type: "string" },
  },
});

const comp = values.comp ?? "CardFan3D";
const frame = values.frame ? Number(values.frame) : 100;
const out = values.out || path.resolve(__dirname, "out", `${comp}_f${frame}.png`);
const inputProps = values.props ? JSON.parse(values.props) : {};

const serveUrl = await bundle({
  entryPoint: path.resolve(__dirname, "src/index.ts"),
});
const composition = await selectComposition({ serveUrl, id: comp, inputProps });
await renderStill({ composition, serveUrl, output: out, frame, inputProps });
console.log(out);
