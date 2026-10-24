import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = new URL("../../", import.meta.url);
const registry = JSON.parse(
  await readFile(
    new URL("core/deployments/online_boutique.json", root),
    "utf8",
  ),
);
const topology = JSON.parse(
  await readFile(new URL("eval/fixtures/localisation.json", root), "utf8"),
);
const catalog = {
  environment: registry.environment_id,
  revision: registry.revision,
  provenance: "Reviewed declared topology, not live health or measured impact.",
  services: registry.services.map(
    ({ name, service_type, language, description }) => ({
      name,
      type: service_type,
      language,
      description,
    }),
  ),
  edges: topology.edges,
};
await writeFile(
  new URL("../src/catalog.json", import.meta.url),
  `${JSON.stringify(catalog, null, 2)}\n`,
);
console.log(
  `Synced ${catalog.services.length} registered services and ${catalog.edges.length} declared edges from ${fileURLToPath(root)}.`,
);
