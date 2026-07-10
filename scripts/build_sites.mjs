import { access, cp, mkdir, rm } from "node:fs/promises";
import { constants } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const staticSource = resolve(root, "site");
const output = resolve(root, "dist");

try {
  await access(resolve(staticSource, "index.html"), constants.R_OK);
} catch {
  throw new Error("Build the static site before preparing the Sites artifact.");
}

await rm(output, { recursive: true, force: true });
await mkdir(resolve(output, "client"), { recursive: true });
await mkdir(resolve(output, "server"), { recursive: true });
await cp(staticSource, resolve(output, "client"), { recursive: true });
await cp(resolve(root, "worker", "index.js"), resolve(output, "server", "index.js"));

console.log("Prepared the Sites deployment artifact in dist/.");
