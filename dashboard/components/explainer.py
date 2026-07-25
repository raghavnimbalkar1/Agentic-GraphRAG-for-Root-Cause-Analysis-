"""
dashboard/components/explainer.py

A plain-language "Start Here" intro for the dashboard — the first thing a
non-expert (professor, external examiner) sees. Explains the whole idea with the
alarm/fire analogy, a compact animated dependency-trace, the four-step loop in
everyday words, and the headline proof numbers. No jargon; the technical tabs
come after.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

# Self-contained animated trace (renders in a Streamlit component iframe).
# A normal AI blames the alerting `frontend`; GraphRAG traces the dependency
# edges to the true root, `redis-cart`, three hops away.
_TRACE_HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
  :root{--panel:#131d31;--line:#2b3a54;--steel:#8391a8;--ink:#e8edf6;
        --signal:#2ec9ae;--fault:#f16a6e;--warn:#e7a24b;
        --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;}
  *{box-sizing:border-box} html,body{margin:0}
  .card{background:linear-gradient(180deg,#0e1626,#0b1220);border:1px solid var(--line);
        border-radius:14px;padding:10px 8px 6px;position:relative;font-family:var(--mono);}
  .tl{position:absolute;top:11px;left:15px;font-size:10px;letter-spacing:.14em;
      color:var(--steel);text-transform:uppercase;}
  svg{display:block;width:100%;height:auto}
  .edge{stroke:var(--line);stroke-width:2.4;fill:none}
  .nc{fill:#16223a;stroke:var(--steel);stroke-width:2}
  .lab{font-family:var(--mono);fill:var(--ink);font-size:12px}
  .sub{font-family:var(--mono);fill:var(--steel);font-size:10px;letter-spacing:.03em}
  .bt{font-family:var(--mono);font-size:10.5px;font-weight:700}
  @media (prefers-reduced-motion:no-preference){
    .pulse{transform-box:fill-box;transform-origin:center;animation:pulse 11s ease-in-out infinite}
    .trace{stroke-dasharray:470;stroke-dashoffset:470;animation:draw 11s ease-in-out infinite}
    .ring{transform-box:fill-box;transform-origin:center;animation:ring 11s ease-in-out infinite}
    .bad{animation:bad 11s ease-in-out infinite;opacity:0}
    .good{animation:good 11s ease-in-out infinite;opacity:0}
    .glow{transform-box:fill-box;transform-origin:center;animation:glow 11s ease-in-out infinite}
    .sym{offset-path:path("M660 140 L490 140 L300 140 L138 140");
         animation:travel 11s linear infinite;opacity:0}
  }
  @keyframes pulse{0%,8%{filter:none}3%{filter:drop-shadow(0 0 8px var(--fault))}12%,100%{filter:none}}
  @keyframes glow{0%,10%{opacity:.16}4%{opacity:.5}30%,100%{opacity:.22}}
  @keyframes travel{0%,6%{opacity:0;offset-distance:0%}9%{opacity:1}
    26%{offset-distance:100%;opacity:1}30%{opacity:0}100%{opacity:0;offset-distance:100%}}
  @keyframes ring{0%,24%{transform:rotate(0)}26%{transform:rotate(-13deg)}
    28%{transform:rotate(11deg)}30%{transform:rotate(-7deg)}32%,100%{transform:rotate(0)}}
  @keyframes bad{0%,36%{opacity:0;transform:translateY(-4px)}42%{opacity:1;transform:translateY(0)}
    72%{opacity:1}82%,100%{opacity:.5}}
  @keyframes draw{0%,50%{stroke-dashoffset:470}72%{stroke-dashoffset:0}94%{stroke-dashoffset:0}100%{stroke-dashoffset:470}}
  @keyframes good{0%,68%{opacity:0;transform:scale(.6)}74%{opacity:1;transform:scale(1)}94%{opacity:1}100%{opacity:0}}
</style></head><body>
  <div class="card">
    <span class="tl">live dependency trace &middot; Neo4j</span>
    <svg viewBox="0 0 760 250" role="img" aria-label="A dependency graph. The cache service on the
      right has failed; the symptom surfaces at the frontend on the left. A normal AI blames the
      frontend, which is wrong. GraphRAG traces the edges to the failed cache, which is correct.">
      <!-- spine + branches -->
      <path class="edge" d="M100 140 L300 140 L490 140 L660 140"/>
      <path class="edge" d="M100 140 C 150 96, 190 78, 230 70"/>
      <path class="edge" d="M100 140 C 140 180, 165 204, 200 214"/>
      <path class="edge" d="M300 140 C 340 168, 366 188, 396 200"/>
      <!-- green GraphRAG trace -->
      <path class="trace" d="M100 140 L300 140 L490 140 L660 140" fill="none"
            stroke="var(--signal)" stroke-width="4.4" stroke-linecap="round" stroke-linejoin="round"/>
      <!-- fault glow -->
      <circle class="glow" cx="660" cy="140" r="40" fill="var(--fault)" opacity=".18"/>
      <!-- branch nodes (healthy) -->
      <g><circle class="nc" cx="230" cy="66" r="15"/><text class="sub" x="230" y="42" text-anchor="middle">adservice</text></g>
      <g><circle class="nc" cx="200" cy="216" r="15"/><text class="sub" x="200" y="244" text-anchor="middle">recommend</text></g>
      <g><circle class="nc" cx="404" cy="204" r="13"/><text class="sub" x="404" y="232" text-anchor="middle">shipping</text></g>
      <!-- spine nodes -->
      <g>
        <circle class="nc" cx="100" cy="140" r="30"/>
        <text class="lab" x="100" y="144" text-anchor="middle">frontend</text>
        <g transform="translate(128 108)"><g class="ring">
          <path d="M0 8 C0 3 3 0 7 0 C11 0 14 3 14 8 L14 12 L16 15 L-2 15 L0 12 Z" fill="var(--warn)"/>
          <circle cx="7" cy="18" r="2.4" fill="var(--warn)"/>
        </g></g>
        <g transform="translate(100 96)"><g class="bad">
          <circle r="11" fill="var(--warn)"/>
          <path d="M-4 -4 L4 4 M4 -4 L-4 4" stroke="#0b1220" stroke-width="2.6" stroke-linecap="round"/>
          <text class="bt" x="-16" y="4" text-anchor="end" fill="var(--warn)">normal AI: &ldquo;it&rsquo;s here&rdquo;</text>
        </g></g>
      </g>
      <g><circle class="nc" cx="300" cy="140" r="24"/><text class="lab" x="300" y="144" text-anchor="middle" font-size="11">checkout</text></g>
      <g><circle class="nc" cx="490" cy="140" r="24"/><text class="lab" x="490" y="144" text-anchor="middle" font-size="11">cart</text></g>
      <g>
        <circle class="nc pulse" cx="660" cy="140" r="30" stroke="var(--fault)" fill="#2a1720"/>
        <text class="lab" x="660" y="144" text-anchor="middle" fill="var(--fault)" font-size="11">redis-cart</text>
        <g transform="translate(660 186)"><g class="good">
          <circle r="12" fill="var(--signal)"/>
          <path d="M-5 0 L-1.5 4 L5 -4" stroke="#0b1220" stroke-width="2.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          <text class="bt" x="0" y="30" text-anchor="middle" fill="var(--signal)">TRUE ROOT &middot; 3 hops down</text>
        </g></g>
      </g>
      <circle class="sym" r="5.5" fill="var(--fault)"/>
    </svg>
  </div>
</body></html>
"""


def render_start_here() -> None:
    st.markdown("### The alarm rings at the front door. The fire is in the basement.")
    st.markdown(
        "When one microservice breaks, the error shows up somewhere else — the alert fires on the "
        "customer-facing page, but the real fault is buried several services deep. A normal AI reads "
        "the alert and blames the page it fired on. **This system follows the dependency map straight "
        "to the true source — and fixes it, by itself, no human involved.**"
    )
    components.html(_TRACE_HTML, height=270, scrolling=False)

    st.markdown("#### What it does — four steps, no human in the loop")
    steps = [
        ("1 · Detect", "A monitor watching real health spots the failure and raises the incident. Nobody has to notice first."),
        ("2 · Trace", "It walks the dependency graph from the symptom to the deepest broken service — the true root cause."),
        ("3 · Fix, sealed", "It runs an approved repair inside a locked-down sandbox that can touch nothing else — never the live host."),
        ("4 · Verify", "It re-checks the exact thing that broke. Only a genuine recovery counts as resolved."),
    ]
    for col, (title, desc) in zip(st.columns(4), steps):
        with col:
            st.markdown(f"**{title}**")
            st.caption(desc)

    st.markdown("#### Why it's the real deal, not a fluke")
    m = st.columns(4)
    m[0].metric("Fixed on its own", "16 / 16",
                help="Random failures injected over an 11-minute unattended run — all detected and resolved.")
    m[1].metric("Humans involved", "0", help="Zero alerts raised by hand; the system ran the whole loop itself.")
    m[2].metric("Finds the true cause", "100%",
                help="Correct root cause at every depth, where the text-guessing baselines fell to 0%.")
    m[3].metric("Layers deep it traces", "7",
                help="On a 36-service benchmark (TrainTicket) — nearly double a typical demo's depth.")

    st.info(
        "**Explore the tabs above →**  Watch it resolve a live fault in **Live RCA Console** · "
        "see both halves of the knowledge graph in **Dual Graph** · or pit it head-to-head against "
        "baseline AIs in **Live Duel**.",
    )
