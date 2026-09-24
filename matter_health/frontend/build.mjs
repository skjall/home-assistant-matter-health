// Bundles the page into the Python package, where the add-on serves it.
import { build, context } from "esbuild";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";

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

// The page names the bundle with a hash of its content, so a browser or the
// companion app holding an old copy fetches the new one after an update.
async function writePage() {
  const bundle = await readFile(`${out}/app.js`).catch(() => "");
  const version = createHash("sha256").update(bundle).digest("hex").slice(0, 12);
  const page = await readFile("src/index.html", "utf8");
  await writeFile(`${out}/index.html`, page.replace("static/app.js", `static/app.js?v=${version}`));
}

await mkdir(out, { recursive: true });
if (process.argv.includes("--watch")) {
  await writePage();
  await (await context(options)).watch();
} else {
  await build(options);
  await writePage();
}
