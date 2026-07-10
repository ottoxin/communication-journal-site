import { access, cp, mkdir, rm } from "node:fs/promises";
import { constants } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const staticSource = resolve(root, "site");
const output = resolve(root, "dist");

async function exists(path) {
  try {
    await access(path, constants.R_OK);
    return true;
  } catch {
    return false;
  }
}

if (!(await exists(resolve(staticSource, "index.html")))) {
  const committedArtifact =
    (await exists(resolve(output, "client", "index.html"))) &&
    (await exists(resolve(output, "server", "index.js")));
  if (!committedArtifact) {
    throw new Error("Build the static site before preparing the Sites artifact.");
  }
  console.log("Using the committed Sites deployment artifact.");
} else {
  await rm(output, { recursive: true, force: true });
  await mkdir(resolve(output, "client"), { recursive: true });
  await mkdir(resolve(output, "server"), { recursive: true });
  await cp(staticSource, resolve(output, "client"), {
    recursive: true,
    filter(source) {
      const name = source.split("/").pop() || "";
      return name !== ".DS_Store" && name !== "articles.jsonl" && !name.startsWith("verification-");
    }
  });
  await cp(resolve(root, "worker", "index.js"), resolve(output, "server", "index.js"));
  console.log("Prepared the Sites deployment artifact in dist/.");
}
