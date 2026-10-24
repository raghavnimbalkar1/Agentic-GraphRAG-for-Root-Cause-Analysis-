export const REPO =
  "https://github.com/raghavnimbalkar1/Agentic-GraphRAG-for-Root-Cause-Analysis-";
export const doc = (path) => `${REPO}/blob/main/${path}`;
export { evidenceRows } from "./evidence.mjs";

export function dependencyPath(edges, from, to) {
  const queue = [[from]],
    visited = new Set();
  while (queue.length) {
    const path = queue.shift(),
      current = path.at(-1);
    if (current === to) return path;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const [caller, target] of edges) {
      if (caller === current && !path.includes(target))
        queue.push([...path, target]);
    }
  }
  return [];
}

export const steps = [
  "Detect",
  "Traverse",
  "Retrieve",
  "Execute",
  "Verify",
  "Report",
];
export const walkthroughs = {
  payment: {
    label: "Payment partition",
    root: "paymentservice",
    chain: ["frontend", "checkoutservice", "paymentservice"],
    stages: [
      [
        "A real failure, independently observed.",
        "The collector observes payment connectivity loss. It creates the incident; the fault injector does not send the alert.",
        "CONNECTION_REFUSED",
        "Independent collector",
      ],
      [
        "Put the failing service in context.",
        "Q1 checks service state and declared dependencies. The recorded payment trial had a payment-origin alert: this was a zero-hop localization case.",
        "paymentservice",
        "Infrastructure graph / Q1",
      ],
      [
        "Retrieve a permitted procedure.",
        "Q2 returns Payment_Restart_SOP for this registered service and condition. The model can select that name, not generate executable code.",
        "Payment_Restart_SOP",
        "Skill graph / Q2",
      ],
      [
        "Restart and reconnect only the target.",
        "A restricted SOP wrapper requests fixed operations from the trusted controller. Payment is restarted and reconnected to the simulation network.",
        "Owned target only",
        "Restricted executor + controller",
      ],
      [
        "Check the application, not just the process.",
        "Fresh service observations and two private-session cart/checkout rounds must pass. A ready container alone cannot establish recovery.",
        "Two successful checkout rounds",
        "Independent verification",
      ],
      [
        "Keep the evidence with the outcome.",
        "The corrected October 13 development trial recorded application recovery, 786 model tokens and 13.448 seconds of agent handling. These are one-trial observations.",
        "Verified application recovery",
        "Incident-addressed report",
      ],
    ],
  },
  redis: {
    label: "Redis fallback",
    root: "redis-cart",
    chain: ["frontend", "checkoutservice", "cartservice", "redis-cart"],
    stages: [
      [
        "A persistent memory cap disrupts Redis.",
        "The collector observes the low Redis memory cap. OOM_KILLED is the testbed condition label, not proof of a kernel OOM event.",
        "OOM_KILLED",
        "Independent collector",
      ],
      [
        "Inspect the dependency chain.",
        "The declared map connects frontend, checkout, cart and Redis. This structural path explains possible exposure, not measured impact at every node.",
        "frontend → checkout → cart → Redis",
        "Infrastructure graph / Q1",
      ],
      [
        "Start with the graph-approved skill.",
        "The model selects Redis_Restart_SOP from the retrieved candidates. The graph supplies the reviewed procedure and its permitted target.",
        "Redis_Restart_SOP",
        "Skill graph / Q2",
      ],
      [
        "A failed check activates an explicit fallback.",
        "Restart leaves the persistent cap in place. NEXT_IF_FAIL permits Redis_Flush_SOP, which deletes keys and resets runtime memory settings. This is destructive.",
        "Restart → verify → flush",
        "Skill graph / NEXT_IF_FAIL",
      ],
      [
        "Verify again after the fallback.",
        "Fresh component evidence and application transactions must pass. Runtime recovery does not mean the persistent launch configuration has been repaired.",
        "Runtime recovery verified",
        "Independent verification",
      ],
      [
        "Report both attempts and their limits.",
        "The October 13 development trial recorded 36.357 seconds of handling and 1,625 model tokens across the decisions. It is not an average or a comparative benchmark.",
        "Two attempts preserved",
        "Incident-addressed report",
      ],
    ],
  },
};

export const commands = {
  setup: `git clone ${REPO}.git\ncd Agentic-GraphRAG-for-Root-Cause-Analysis-\nbash scripts/setup_dev_env.sh\nsource .venv/bin/activate`,
  infrastructure: `docker compose up -d --wait neo4j\npython -m graph.scripts.init_graph\ndocker build -t sop-executor:latest sop-executor/\ndocker compose -f simulation/docker-compose.yml up -d --wait`,
  observe: `python -m simulation.telemetry_collector \\\n  --observe-only --record-evidence\n\n# Separate terminal: one mock checkout observation\npython -m simulation.symptom_monitor --once`,
};
