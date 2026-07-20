# Live Demo & Viva Runbook

A tight, rehearsable sequence for presenting Agentic GraphRAG. Total run: **~6 minutes**.
Every step below has been verified working. Screenshot each surface on your own machine for the
thesis appendix — the figures are noted with 📸.

---

## 0. Before the room (pre-flight, do this 10 min early)

```bash
# 1. Infra
docker compose up -d                                  # Neo4j
docker compose -f simulation/docker-compose.yml up -d # Online Boutique (12 services)

# 2. Agent + sensing + UI (three terminals, or backgrounded)
python -m agent.main                       # FastAPI agent on :8888
python -m simulation.telemetry_collector   # health observation loop
streamlit run dashboard/app.py             # dashboard on :8501

# 3. Sanity — all four must be green in the dashboard header strip
#    agent · collector · Neo4j · LLM
```

**Health check:** open the dashboard; the header's four status chips must all be green. If the
collector chip is red, live scenarios will hang — restart it. Confirm all 12 services show HEALTHY
on the Live Console.

**Insurance:** if anything is down on the day, the **Autonomy Run** and **Evaluation** tabs read
from committed artifacts and need no live stack — you can present the whole result set from those
alone.

---

## 1. The problem (30s, no screen)

> "When a microservice fails deep in a dependency chain, the symptoms surface far upstream. The
> alert fires on the frontend; the actual broken service is four hops away. On-call engineers get an
> alert storm and have two minutes to find the real cause. Existing tools either only *advise*, or
> they *guess* from text, or they run fixes unsafely on production. My system does all three safely:
> it localises by graph topology, fixes in a sandbox, and verifies it worked."

---

## 2. The architecture (45s) — 🗺️ **Dual Graph & Architecture** tab   📸

> "Everything the agent knows is two graphs in Neo4j. Left — the infrastructure graph: services and
> their dependencies, the WHERE. Right — the skill graph: remediation SOPs, what they apply to, and
> fallback chains, the HOW. Every edge is *data*, not code — which is why I could later drop in a
> completely different architecture with zero code change."

Point at the callout: **"redis-cart has 4 candidate SOPs"** — "so when it fails, the agent *chooses*
among them; it's a decision, not a lookup." Then read the 5-layer loop at the bottom.

---

## 3. Watch it work — full autonomy (90s) — 🚨 **Live RCA Console** tab   📸

1. Fault type → `high_cpu`, target → `adservice`. Click **Inject fault & let agent resolve**.
2. Narrate while it runs: *"The injector only breaks adservice — it fires no alert. The collector
   detects the real CPU spike, raises the incident, and the agent takes over."*
3. Watch the graph node go **red → green**. When the report appears, scroll to the **Agent Decision**
   panel:
   > "Here's the proof it reasoned: it had two options — throttle or restart — and chose the
   > lower-risk throttle, with its stated reason. Then it re-checked the *actual* CPU, not just the
   > script's exit code, before declaring success."

*(Backup fault if adservice is slow to spike: `stale_data` on redis-cart — resolves in ~6s.)*

---

## 4. Why the graph matters (90s) — ⚔️ **Live Duel vs Baselines** tab   📸📸

1. Keep topology on **Online Boutique**, pick the **depth-4** scenario. Click **Run the duel**.
   > "Same alert, same LLM, three systems. The alert says only 'storefront 5xx' — it names neither
   > the root nor the path."
2. Result: **GraphRAG → redis-cart ✓** (4-hop traversal); **both baselines → frontend ✗**.
   > "The baselines even *explain* their wrong answer confidently. Vector-RAG retrieved a *frontend*
   > SOP by text similarity — the wrong level entirely."
3. **Switch topology to TrainTicket**, pick **depth-7**, run again.   📸
   > "And this isn't a small-topology fluke. This is the TrainTicket benchmark — 36 services. The
   > same traversal follows seven dependency hops to `station` in 0.03 seconds; the LLM guesses
   > `gateway`. The graph advantage *grows* with depth."

---

## 5. The numbers (60s) — 📊 **Evaluation Results** tab   📸

> "Across 21 scenarios, three repetitions: root-cause accuracy is flat at 100% at every depth, while
> the baselines collapse from 100% at depth 1 to 0% at depth 4."

Point at the line chart (flat green vs collapsing lines) and the depth table. Then be honest,
unprompted:
> "I'm precise about what this claims: one fault at a time, so traversal is deterministic — the claim
> is robustness to alert ambiguity, not solved multi-fault RCA. And blast-radius F1 being 1.0 is
> because the graph *computes* the ground truth. I state both in the paper."

---

## 6. Unattended autonomy (30s) — 🤖 **Autonomy Run** tab   📸

> "Finally — no human at all. A chaos daemon injected random faults for 11 minutes and never raised
> an alert. The collector detected 16 of 16, the agent resolved all 16, zero escalations. This log is
> the autonomy proof."

---

## 7. Close (30s, no screen)

> "So: topology-aware localisation that holds to 7 hops, a graph-as-allowlist that kept the LLM from
> ever executing anything outside vetted procedures — I tested it against prompt injection — and a
> genuinely closed loop that detects, fixes, and verifies on its own. The honest boundaries —
> single-host, single-fault, hand-authored topology — each map to a concrete next step in the
> roadmap: full deployment on TrainTicket, then auto-discovering topology from traces toward a
> general tool."

---

## Likely questions — and your answers

- **"Is it just an if-else mapping errors to fixes?"** → "No. I grepped it: zero fault-condition
  literals in the localise, retrieve, and decide code — it's all Cypher over the graph. The only
  per-condition code is *verifying* a fix and *sensing* the fault."
- **"Your 100% is suspicious."** → "It's partly by construction — one fault, so the deepest-unhealthy
  traversal is deterministic. The claim is robustness to alert ambiguity. Multi-fault is future work."
- **"Why not run on a bigger real system?"** → "Hardware. TrainTicket's ~41 Spring services want
  16 GB+ and a cluster I'd have to rent. So I proved the *localisation* generalises to it — the
  research-hard part ports for free — and named the full deployment as v2."
- **"How would this become a product?"** → "Two leaps: v2 is deploying the full loop on a second real
  system — bounded engineering. The real gate to 'any cluster' is v3: auto-discovering the dependency
  graph from traces instead of hand-authoring it."
- **"Isn't running fixes dangerous?"** → "Every fix runs in an ephemeral, capability-dropped,
  network-isolated sandbox, and the LLM can only ever *name* a graph-vetted procedure — it never
  writes executable code. Unparseable or out-of-set output escalates to a human."
