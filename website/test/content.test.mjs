import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createHash } from "node:crypto";
import {
  commands,
  dependencyPath,
  evidenceRows,
  REPO,
  walkthroughs,
} from "../src/model.mjs";

const json = async (path) =>
  JSON.parse(await readFile(new URL(path, import.meta.url), "utf8"));
const catalog = await json("../src/catalog.json");
const registry = await json("../../core/deployments/online_boutique.json");
const topology = await json("../../eval/fixtures/localisation.json");
const evidence = await json(
  "../../eval/results/verified/20261014/development_rehearsals.json",
);

test("public catalog mirrors reviewed services and declared edges without truth labels", () => {
  assert.deepEqual(
    catalog.services.map((s) => s.name),
    registry.services.map((s) => s.name),
  );
  assert.deepEqual(catalog.edges, topology.edges);
  assert.equal(catalog.revision, registry.revision);
  assert.equal(catalog.services.length, 12);
  assert.equal(catalog.edges.length, 16);
  assert.ok(!JSON.stringify(catalog).includes("truth_root"));
  for (const row of catalog.services)
    assert.deepEqual(Object.keys(row), [
      "name",
      "type",
      "language",
      "description",
    ]);
});

test("every displayed dependency edge connects registered services", () => {
  const names = new Set(catalog.services.map((s) => s.name));
  for (const edge of catalog.edges)
    assert.ok(edge.every((name) => names.has(name)));
});

test("diagram traversal follows direction and terminates on cycles", () => {
  assert.deepEqual(
    dependencyPath(
      [
        ["a", "b"],
        ["b", "a"],
        ["b", "c"],
      ],
      "a",
      "c",
    ),
    ["a", "b", "c"],
  );
  assert.deepEqual(
    dependencyPath(
      [
        ["a", "b"],
        ["b", "a"],
      ],
      "a",
      "c",
    ),
    [],
  );
  assert.deepEqual(dependencyPath([["a", "b"]], "b", "a"), []);
});

for (const [id, example] of Object.entries(walkthroughs)) {
  test(`${id} walkthrough uses a real declared chain and six bounded stages`, () => {
    assert.equal(example.stages.length, 6);
    assert.equal(example.chain.at(-1), example.root);
    for (let i = 1; i < example.chain.length; i++)
      assert.ok(
        catalog.edges.some(
          ([a, b]) => a === example.chain[i - 1] && b === example.chain[i],
        ),
      );
  });
}

for (const row of evidenceRows) {
  test(`${row.name} measurements and overall outcome match the reviewed export`, () => {
    const source = evidence.trials.find(
      (item) => item.label === row.recordLabel,
    );
    assert.ok(source);
    assert.equal(row.handling, source.handling_seconds);
    assert.equal(row.tokens, source.tokens_used);
    assert.equal(row.overallPassed, source.overall_rehearsal_passed);
    if (!row.overallPassed) assert.match(row.outcome, /failed/);
  });
}

test("worked examples do not turn root-local trials into hidden-root accuracy claims", () => {
  assert.match(walkthroughs.payment.stages[1][1], /zero-hop/);
  assert.match(walkthroughs.redis.stages[0][1], /not proof of a kernel OOM/);
  assert.match(walkthroughs.redis.stages[3][1], /destructive/);
  assert.match(
    walkthroughs.redis.stages[4][1],
    /persistent launch configuration/,
  );
});

test("setup is source-based and observe-only, not an invented package installer", () => {
  assert.match(commands.setup, /git clone/);
  assert.match(commands.observe, /--observe-only --record-evidence/);
  assert.ok(
    Object.values(commands).every(
      (value) =>
        !/API_KEY=|gsk_|brew install|pip install agentic|curl.*\|.*sh/.test(
          value,
        ),
    ),
  );
  assert.match(REPO, /^https:\/\/github.com\//);
});

test("workspace asset is an unmodified copy of the existing reviewed capture", async () => {
  const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
  const original = await readFile(
    new URL("../../docs/images/workspace-overview.png", import.meta.url),
  );
  const website = await readFile(
    new URL("../public/images/workspace-overview.png", import.meta.url),
  );
  assert.equal(hash(original), hash(website));
});

test("walkthrough metrics match their actual selected incidents", () => {
  for (const [id, label] of [
    ["payment", "Payment: corrected recovery"],
    ["redis", "Redis: persistent-cap fallback"],
  ]) {
    const source = evidence.trials.find((t) => t.label === label),
      text = walkthroughs[id].stages.at(-1)[1];
    assert.ok(text.includes(source.handling_seconds.toFixed(3)));
    assert.ok(text.includes(source.tokens_used.toLocaleString("en-US")));
  }
});
