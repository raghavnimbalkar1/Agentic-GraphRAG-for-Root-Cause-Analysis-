# Paper Updates — changes to make in the `.tex`

Everything below is verified against the committed artifacts (`benchmark_full.json`,
`ablation.json`, `trainticket_localisation.json`, Neo4j). Apply to your LaTeX source. Grouped by
paper section, with exact find → replace and ready-to-paste blocks.

---

## 1. Factual corrections (do these — they are errors)

**1a. Skill count** — Section IV-B (Module B: The Dual Graph)
- Find: `The deployment carries 14 skills over 11 trigger conditions.`
- Replace: `The deployment carries 15 skills over 11 trigger conditions.`

**1b. Blast-radius F1 baselines** — Section VI-C (RQ2/RQ3)
- Find: `against baselines around 0.74.`
- Replace: `against 0.69 (zero-shot) and 0.73 (vector-RAG).`

**1c. Vector-RAG tokens** — Section VI-C
- Find: `$\approx$445 for B1 and $\approx$650 for B2`
- Replace: `$\approx$445 for B1 and $\approx$662 for B2`

**1d. Citation key (optional, cosmetic)** — the rendered bibliography is correct (`C. Pei … WWW '25,
2025`); only the internal `\cite` key `chen2024flowofaction` is misnamed and never appears in the PDF.
If tidying: rename to `pei2025flowofaction` in the `\bibitem` and its two `\cite` calls. Not required.

---

## 2. Depth table tidy (optional but recommended) — Table II

Relabel the deepest row from `3--4 (deep)` to `4`, and add per-bucket n. Owning the small n=2 at the
deepest bucket pre-empts the obvious critique.

```latex
\begin{tabular}{lccc}
\toprule
\textbf{Depth} & \textbf{GraphRAG} & \textbf{B1 Zero-shot} & \textbf{B2 Vector RAG} \\
\midrule
1 (n=8) & 100 & 100 & 100 \\
2 (n=5) & 100 & 80  & 40  \\
3 (n=6) & 100 & 17  & 17  \\
4 (n=2) & 100 & 0   & 0   \\
\bottomrule
\end{tabular}
```

---

## 3. NEW — Ablation subsection (paste into Section VI)

You have real ablation data now (`eval/results/ablation.json`). Add:

```latex
\subsection{Ablation}
Removing each load-bearing component isolates its contribution. Without the infrastructure graph
(taking the alerting service as the root), localisation falls to 0\% across all depths---every
scenario is a genuine cascade, so traversal is strictly necessary; the intermediate ``LLM guesses
without the graph'' point is the 62\% zero-shot baseline. Removing Progressive Context Injection
(placing the entire skill library in context rather than the root's candidate set) raises
per-decision cost from $\approx$814 to $\approx$1358 tokens ($+67\%$) on a 15-skill library---an
overhead that grows linearly with library size while the injected context stays constant.
```

---

## 4. NEW — TrainTicket generalisation (strengthen Section VI + soften the future-work mention)

Your conclusion names TrainTicket as "the natural externality test." You have that result now
(`eval/results/trainticket_localisation.json`), so you can present it as **done**, not future work.

Paste into Section VI (a "Generalisation" paragraph or subsection):

```latex
\subsection{Generalisation to a Deeper Topology}
To test that the depth result is not an artifact of one small topology, the identical Q1 traversal
was run on the FudanSELab TrainTicket dependency graph (36 services, 73 edges, transcribed from its
published architecture and loaded as an isolated graph). This is a localisation study---traversal
versus a topology-blind zero-shot LLM given the TrainTicket service catalogue---since remediation on
TrainTicket's Spring Cloud stack requires deployment beyond our single-host hardware. Traversal
localised the root cause correctly at every depth from 1 to 7 (7/7), where the zero-shot LLM
succeeded on only 2/7; at depth 7 the root (\texttt{station}) is seven dependency hops from the
alerting \texttt{frontend}, reached in $\approx$0.03\,s, while the LLM guessed \texttt{gateway}. The
graph advantage is therefore not topology-specific: it holds at nearly double Online Boutique's
depth on a standard benchmark.
```

Then in the Conclusion, change the TrainTicket future-work line from "stress-testing against
TrainTicket is the natural externality test" to something like: "Extending the *closed loop*
(remediation, not only localisation) to TrainTicket's Spring Cloud stack, on cluster-scale hardware,
is the natural next step."

---

## 5. NEW — Multi-fault handling (update Section VII limitation — REQUIRED if repo is public)

The `feature/multi-root` work is now **merged to `main`**, so the code contains multi-fault handling.
Your paper's Section VII currently says it is "unhandled and untested here" — that sentence is now
inconsistent with the repo. Update it:

- Find (approx.): `The deepest-unhealthy heuristic assumes a single root and can mis-attribute under
  simultaneous correlated faults --- unhandled and untested here.`
- Replace: `The deepest-unhealthy heuristic assumes a single root. Genuinely \emph{independent}
  simultaneous faults are handled by an orchestration layer that detects every independent root
  (an unhealthy service with no unhealthy dependency of its own) and dispatches the single-root loop
  at each---validated end-to-end on two concurrent faults. \emph{Correlated} faults, where one
  fault's symptoms mark upstream nodes unhealthy, and a quantitative multi-fault evaluation, remain
  future work.`

This turns a limitation into a contribution while staying honest (correlated-fault handling and the
multi-fault *evaluation* genuinely remain open).

---

## 6. Numbers reference (all verified — cite with confidence)

| Metric | GraphRAG | Zero-shot (B1) | Vector-RAG (B2) |
|---|---|---|---|
| Root accuracy (overall) | 100% | 62% | 52% |
| Blast-radius F1 | 1.00 | 0.69 | 0.73 |
| Mean tokens / incident | 867 | 445 | 662 |
| Mean MTTR (s) | 6.8 ± 2.8 † | 2.6 ± 1.7 ‡ | 3.1 ± 2.1 ‡ |

† real inject→resolve→verify. ‡ inference latency only (baselines never remediate).

- Depth table: 100/100/100 · 100/80/40 · 100/17/17 · 100/0/0 (n = 8/5/6/2).
- Chaos autonomy: 16/16 detected & resolved, 0 manual alerts, det 10.9s, MTTR 20.7s.
- Skills 15 · trigger conditions 11 · services 12 · fault types 10 · unit tests 50.
- TrainTicket: 36 services, 73 edges; traversal 7/7, zero-shot 2/7 across depth 1–7.
- Ablation: infra graph 100%→0%; PCI 814 vs 1358 tokens (+67%).
