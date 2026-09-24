// Bundles the page into the Python package, where the add-on serves it.
import { build, context } from "esbuild";
import { copyFile, mkdir } from "node:fs/promises";

const out = "../app/matter_health/web/static";
const options = {
  entryPoints: ["src/main.ts"],
  bundle: true,
  format: "esm",
  target: "es2022",
  minify: !process.argv.includes("--watch"),
  sourcemap: process.argv.includes("--watch") ? "inline" : false,
  outfile: `${out}/app.js`,
  legalComments: "none",
};

await mkdir(out, { recursive: true });
await copyFile("src/index.html", `${out}/index.html`);
if (process.argv.includes("--watch")) {
  await (await context(options)).watch();
} else {
  await build(options);
}
