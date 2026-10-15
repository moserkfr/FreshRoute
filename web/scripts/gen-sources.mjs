// Collects every `sources` map (and per-item `source` fields) from ../config/*.yaml into
// src/data/sources.json for the "Assumptions & sources" page. Runs before `dev` and `build`.
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const here = dirname(fileURLToPath(import.meta.url));
const configDir = join(here, "..", "..", "config");
const out = join(here, "..", "src", "data", "sources.json");

function walk(node, path, rows) {
  if (Array.isArray(node)) {
    node.forEach((item, i) => {
      const label = item && typeof item === "object" ? item.id ?? item.dest_id ?? item.vehicle_id ?? item.class_id ?? i : i;
      walk(item, [...path, String(label)], rows);
    });
    return;
  }
  if (!node || typeof node !== "object") return;
  for (const [key, value] of Object.entries(node)) {
    if (/sources$/.test(key) && value && typeof value === "object" && !Array.isArray(value)) {
      for (const [param, text] of Object.entries(value)) {
        rows.push({ section: path.join(" › ") || "(top level)", param, text: String(text) });
      }
    } else if (key === "source" && typeof value === "string") {
      rows.push({ section: path.join(" › "), param: "source", text: value });
    } else {
      walk(value, [...path, key], rows);
    }
  }
}

const files = readdirSync(configDir).filter((f) => f.endsWith(".yaml")).sort();
const result = files.map((file) => {
  const rows = [];
  walk(parse(readFileSync(join(configDir, file), "utf8")), [], rows);
  return { file: `config/${file}`, rows };
});
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify(result, null, 1));
console.log(`sources: ${result.reduce((n, f) => n + f.rows.length, 0)} entries from ${files.length} files`);

// The network (hub + destinations) for pages that draw it without a loaded State.
const net = parse(readFileSync(join(configDir, "network.yaml"), "utf8"));
const pick = (o) => ({ id: o.id ?? o.dest_id, name: o.name, lat: o.lat, lon: o.lon, type: o.type ?? "hub", region: o.region ?? null });
writeFileSync(join(dirname(out), "network.json"), JSON.stringify({ hub: pick(net.hub), destinations: net.destinations.map(pick) }, null, 1));
