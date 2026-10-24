import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronRight,
  Copy,
  Database,
  Expand,
  FileText,
  GitBranch,
  GitFork as Github,
  Menu,
  Network,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-500.css";
import "@fontsource/dm-sans/latin-600.css";
import "@fontsource/dm-mono/latin-400.css";
import "@fontsource/instrument-serif/latin-400.css";
import catalog from "./catalog.json";
import {
  REPO,
  commands,
  dependencyPath,
  doc,
  evidenceRows,
  steps,
  walkthroughs,
} from "./model.mjs";
import "./styles.css";

const aliases = {
  "redis-cart": "redis-cart",
  emailservice: "email",
  productcatalogservice: "product catalog",
  currencyservice: "currency",
  paymentservice: "payment",
  shippingservice: "shipping",
  adservice: "ads",
  cartservice: "cart",
  recommendationservice: "recommend",
  checkoutservice: "checkout",
  frontend: "frontend",
  loadgenerator: "load generator",
};
const widePositions = {
  loadgenerator: [86, 14],
  frontend: [76, 30],
  checkoutservice: [67, 52],
  cartservice: [84, 66],
  "redis-cart": [93, 86],
  recommendationservice: [91, 46],
  productcatalogservice: [66, 86],
  paymentservice: [47, 72],
  currencyservice: [58, 13],
  adservice: [93, 25],
  shippingservice: [44, 94],
  emailservice: [25, 88],
};
const narrowPositions = {
  loadgenerator: [16, 60],
  frontend: [50, 60],
  checkoutservice: [84, 60],
  paymentservice: [16, 71],
  cartservice: [50, 71],
  "redis-cart": [84, 71],
  recommendationservice: [16, 82],
  productcatalogservice: [50, 82],
  currencyservice: [84, 82],
  shippingservice: [16, 93],
  emailservice: [50, 93],
  adservice: [84, 93],
};

function OutLink({ href, children, className = "", ...props }) {
  return (
    <a
      className={className}
      href={href}
      target="_blank"
      rel="noreferrer"
      {...props}
    >
      {children}
      <ArrowUpRight size={16} aria-hidden="true" />
    </a>
  );
}

function SectionLabel({ number, children, light = false }) {
  return (
    <div className={`section-label ${light ? "light" : ""}`}>
      <span>{number}</span>
      <span>{children}</span>
    </div>
  );
}

function Header() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const close = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, []);
  return (
    <header className="site-header">
      <a className="brand" href="#top" aria-label="Agentic GraphRAG home">
        <span className="brand-mark">
          <Network size={20} strokeWidth={1.7} />
        </span>
        <span>
          GraphRAG<span className="brand-period">.</span>
        </span>
      </a>
      <nav
        id="main-nav"
        aria-label="Main navigation"
        className={open ? "nav open" : "nav"}
      >
        {[
          ["Architecture", "#architecture"],
          ["Walkthrough", "#walkthrough"],
          ["Evidence", "#evidence"],
          ["Get started", "#get-started"],
        ].map(([label, href]) => (
          <a key={href} href={href} onClick={() => setOpen(false)}>
            {label}
          </a>
        ))}
      </nav>
      <OutLink
        className="header-source"
        href={REPO}
        aria-label="Source on GitHub"
      >
        <Github size={17} />
        <span>Source</span>
      </OutLink>
      <button
        className="icon-button menu-toggle"
        title={open ? "Close navigation" : "Open navigation"}
        aria-label={open ? "Close navigation" : "Open navigation"}
        aria-expanded={open}
        aria-controls="main-nav"
        onClick={() => setOpen(!open)}
      >
        {open ? <X /> : <Menu />}
      </button>
    </header>
  );
}

function SystemMap() {
  const container = useRef(null),
    canvas = useRef(null);
  const [size, setSize] = useState({ width: 1400, height: 660 });
  const [selected, setSelected] = useState("redis-cart");
  const narrow = size.width < 700;
  const positions = narrow ? narrowPositions : widePositions;
  const path = dependencyPath(catalog.edges, "frontend", selected);
  const active = new Set(path.slice(1).map((node, i) => `${path[i]}:${node}`));
  const service = catalog.services.find((s) => s.name === selected);
  useLayoutEffect(() => {
    const observer = new ResizeObserver(([entry]) =>
      setSize({
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      }),
    );
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const c = canvas.current,
      dpr = Math.min(window.devicePixelRatio || 1, 2);
    c.width = size.width * dpr;
    c.height = size.height * dpr;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    const point = (name) => [
      (positions[name][0] * size.width) / 100,
      (positions[name][1] * size.height) / 100,
    ];
    const draw = ([a, b], highlighted) => {
      const [x1, y1] = point(a),
        [x2, y2] = point(b);
      const dx = x2 - x1,
        dy = y2 - y1,
        distance = Math.hypot(dx, dy);
      const inset = narrow ? 15 : 20;
      const endX = x2 - (dx / distance) * inset,
        endY = y2 - (dy / distance) * inset;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(endX, endY);
      ctx.strokeStyle = highlighted ? "#dd422d" : "#c4c5bd";
      ctx.lineWidth = highlighted ? 1.6 : 0.8;
      ctx.stroke();
      const angle = Math.atan2(dy, dx),
        head = highlighted ? 6 : 4;
      ctx.beginPath();
      ctx.moveTo(endX, endY);
      ctx.lineTo(
        endX - head * Math.cos(angle - 0.5),
        endY - head * Math.sin(angle - 0.5),
      );
      ctx.lineTo(
        endX - head * Math.cos(angle + 0.5),
        endY - head * Math.sin(angle + 0.5),
      );
      ctx.closePath();
      ctx.fillStyle = ctx.strokeStyle;
      ctx.fill();
    };
    catalog.edges
      .filter(([a, b]) => !active.has(`${a}:${b}`))
      .forEach((edge) => draw(edge, false));
    catalog.edges
      .filter(([a, b]) => active.has(`${a}:${b}`))
      .forEach((edge) => draw(edge, true));
  }, [size, selected, narrow]);
  return (
    <div className="system-map" ref={container}>
      <canvas ref={canvas} aria-hidden="true" data-testid="topology-canvas" />
      <div className="map-caption">
        <span className="mono">DECLARED SYSTEM MAP</span>
        <span>12 services / 16 dependencies</span>
      </div>
      <div
        className="map-nodes"
        role="group"
        aria-label="Explore registered services"
      >
        {catalog.services.map((s) => {
          const [x, y] = positions[s.name];
          return (
            <button
              key={s.name}
              className={`map-node ${path.includes(s.name) ? "on-path" : ""} ${selected === s.name ? "selected" : ""}`}
              style={{ left: `${x}%`, top: `${y}%` }}
              onClick={() => setSelected(s.name)}
              aria-label={`Inspect ${s.name}`}
              aria-pressed={selected === s.name}
              title={s.name}
            >
              {s.type === "cache" ? (
                <Database size={13} />
              ) : (
                <span className="node-square" />
              )}
              <span>{aliases[s.name]}</span>
            </button>
          );
        })}
      </div>
      <div className="map-inspector" aria-live="polite">
        <span className="inspector-index">↳</span>
        <span className="mono">{service.name}</span>
        <span>{service.language}</span>
        <span className="map-static-note">Declared path, not live health</span>
      </div>
    </div>
  );
}

function Hero() {
  return (
    <>
      <section className="hero" id="top" aria-labelledby="hero-title">
        <SystemMap />
        <div className="hero-copy">
          <div className="eyebrow">
            <span className="red-square" />
            OPEN-SOURCE AIOPS RESEARCH
          </div>
          <h1 id="hero-title">
            Agentic
            <br />
            <span>GraphRAG.</span>
          </h1>
          <p>
            Root cause, in context.
            <br />
            Trace the dependencies. Constrain the repair.
            <br className="desktop-break" /> Verify what actually recovered.
          </p>
          <a href="#architecture" className="button button-red">
            Explore the architecture
            <ArrowDown size={17} />
          </a>
        </div>
        <a className="hero-index" href="#walkthrough">
          <span>FAILURE → INVESTIGATION → RECOVERY</span>
          <ArrowDown size={15} />
        </a>
      </section>
      <div className="spec-strip">
        {[
          [catalog.services.length, "registered services"],
          [catalog.edges.length, "declared dependencies"],
          ["15", "approved skill names"],
          ["01", "explicit fallback edge"],
        ].map(([count, label]) => (
          <div key={label}>
            <strong>{String(count).padStart(2, "0")}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function Architecture() {
  return (
    <section className="architecture section" id="architecture">
      <div className="section-top">
        <SectionLabel number="01">THE ARCHITECTURE</SectionLabel>
        <span className="mono muted">NEO4J / LANGGRAPH / DOCKER</span>
      </div>
      <div className="section-intro">
        <h2>
          Two graphs.
          <br />
          <span className="serif">One constrained loop.</span>
        </h2>
        <p>
          Where could the failure come from?
          <br />
          What is the agent allowed to do?
          <br />
          Those are different questions. We model both.
        </p>
      </div>
      <div className="dual-graphs">
        <article className="graph-half">
          <div className="graph-title">
            <span className="small-index">A</span>
            <h3>The infrastructure graph</h3>
            <span className="mono">THE WHERE</span>
          </div>
          <div className="dependency-line">
            <span>frontend</span>
            <ChevronRight />
            <span>checkout</span>
            <ChevronRight />
            <span>cart</span>
            <ChevronRight />
            <span className="graph-emphasis">redis-cart</span>
          </div>
          <p>
            Service nodes and <code>DEPENDS_ON</code> edges place symptoms in
            their declared dependency chain. Observations stay distinct from
            potential impact.
          </p>
          <div className="graph-foot">
            <GitBranch size={16} />
            <span>Topology-aware traversal / Q1</span>
          </div>
        </article>
        <article className="graph-half">
          <div className="graph-title">
            <span className="small-index">B</span>
            <h3>The skill graph</h3>
            <span className="mono">THE HOW</span>
          </div>
          <div className="skill-line">
            <span>Redis_Restart_SOP</span>
            <div>
              <ArrowRight size={20} />
              <small>NEXT_IF_FAIL</small>
            </div>
            <span>Redis_Flush_SOP</span>
          </div>
          <p>
            Reviewed procedures connect to applicable services. The LLM selects
            a retrieved skill name; it cannot invent a script, target or host
            command.
          </p>
          <div className="graph-foot">
            <ShieldCheck size={16} />
            <span>Graph-constrained candidates / Q2 + Q3</span>
          </div>
        </article>
      </div>
      <div className="architecture-note">
        <span className="black-square" />
        <p>
          Two logical graphs in one Neo4j database. <code>APPLIES_TO</code>{" "}
          connects the procedure to the service.
        </p>
        <OutLink href={doc("docs/ARCHITECTURE.md")}>
          Read the architecture
        </OutLink>
      </div>
    </section>
  );
}

function Walkthrough() {
  const [scenario, setScenario] = useState("payment"),
    [step, setStep] = useState(0),
    [playing, setPlaying] = useState(false);
  const current = walkthroughs[scenario],
    stage = current.stages[step];
  useEffect(() => {
    if (!playing) return;
    if (step === steps.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setStep((s) => s + 1), 2100);
    return () => clearTimeout(timer);
  }, [playing, step]);
  const selectStep = (index) => {
    setStep(index);
    setPlaying(false);
  };
  const changeScenario = (name) => {
    setScenario(name);
    setStep(0);
    setPlaying(false);
  };
  const tabKey = (e, index) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? 5
          : (index + (e.key === "ArrowRight" ? 1 : 5)) % 6;
    selectStep(next);
    document.getElementById(`step-${next}`).focus();
  };
  return (
    <section className="walkthrough section" id="walkthrough">
      <div className="section-top">
        <SectionLabel number="02" light>
          FOLLOW AN INCIDENT
        </SectionLabel>
        <span className="illustrative-label">
          ILLUSTRATIVE SEQUENCE / NOT A LIVE RUN
        </span>
      </div>
      <div className="walkthrough-head">
        <h2>
          A failure is only
          <br />
          <span className="serif">the beginning.</span>
        </h2>
        <div
          className="scenario-switch"
          role="group"
          aria-label="Example incident"
        >
          {Object.entries(walkthroughs).map(([id, item]) => (
            <button
              key={id}
              aria-pressed={id === scenario}
              onClick={() => changeScenario(id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className="step-tabs" role="tablist" aria-label="Incident lifecycle">
        {steps.map((name, i) => (
          <button
            key={name}
            id={`step-${i}`}
            role="tab"
            aria-label={`${String(i + 1).padStart(2, '0')} ${name}`}
            aria-selected={step === i}
            aria-controls="step-panel"
            tabIndex={step === i ? 0 : -1}
            onClick={() => selectStep(i)}
            onKeyDown={(e) => tabKey(e, i)}
          >
            <span>{String(i + 1).padStart(2, "0")}</span>
            {name}
          </button>
        ))}
      </div>
      <div
        className="walkthrough-content"
        id="step-panel"
        role="tabpanel"
        aria-labelledby={`step-${step}`}
      >
        <div className="stage-copy">
          <span className="mono stage-source">{stage[3]}</span>
          <h3>{stage[0]}</h3>
          <p>{stage[1]}</p>
          <span className="stage-fact">
            <span className="yellow-square" />
            {stage[2]}
          </span>
        </div>
        <div className="case-chain" aria-label="Declared dependency path">
          <span className="mono muted">DECLARED DEPENDENCY PATH</span>
          {current.chain.map((name, i) => (
            <React.Fragment key={name}>
              <div
                className={`chain-service ${name === current.root ? "chain-root" : ""}`}
              >
                <span className="mono">{name}</span>
                {name === current.root ? (
                  <span>candidate root</span>
                ) : (
                  <span>{i === 0 ? "storefront" : "dependency"}</span>
                )}
              </div>
              {i < current.chain.length - 1 && (
                <div className="chain-connector">
                  <ArrowDown size={14} />
                  <span>DEPENDS_ON</span>
                </div>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>
      <div className="walkthrough-bottom">
        <div className="walk-controls">
          <button
            className="icon-button inverse"
            title="Reset walkthrough"
            aria-label="Reset walkthrough"
            onClick={() => {
              setStep(0);
              setPlaying(false);
            }}
          >
            <RotateCcw size={17} />
          </button>
          <button
            className="icon-button inverse"
            title="Previous step"
            aria-label="Previous step"
            disabled={step === 0}
            onClick={() => selectStep(step - 1)}
          >
            <ArrowLeft size={18} />
          </button>
          <button
            className="icon-button play-button"
            title={playing ? "Pause walkthrough" : "Play walkthrough"}
            aria-label={playing ? "Pause walkthrough" : "Play walkthrough"}
            onClick={() => {
              if (step === 5) setStep(0);
              setPlaying(!playing);
            }}
          >
            {playing ? <Pause size={17} /> : <Play size={17} />}
          </button>
          <button
            className="icon-button inverse"
            title="Next step"
            aria-label="Next step"
            disabled={step === 5}
            onClick={() => selectStep(step + 1)}
          >
            <ArrowRight size={18} />
          </button>
          <span className="mono step-count">
            {String(step + 1).padStart(2, "0")} / 06
          </span>
        </div>
        <OutLink href={doc("eval/results/verified/20261014/README.md")}>
          Read the recorded incidents
        </OutLink>
      </div>
    </section>
  );
}

function Workspace() {
  const dialog = useRef(null);
  return (
    <section className="workspace section" id="workspace">
      <div className="section-top">
        <SectionLabel number="03">THE OPERATIONS WORKSPACE</SectionLabel>
        <span className="mono muted">STREAMLIT / FASTAPI</span>
      </div>
      <div className="section-intro">
        <h2>
          Less guessing.
          <br />
          <span className="serif">More to inspect.</span>
        </h2>
        <p>
          Service state, incident decisions, graph context and verification
          evidence in one operational workspace. Not a chat window.
        </p>
      </div>
      <button
        className="workspace-capture"
        onClick={() => dialog.current.showModal()}
        aria-label="Enlarge the operations workspace screenshot"
        title="Enlarge workspace"
      >
        <img
          src="./images/workspace-overview.png"
          width="1440"
          height="1150"
          alt="Actual GraphRAG Streamlit workspace showing the service topology and twelve unknown service observations while infrastructure was unavailable"
          loading="lazy"
        />
        <span className="capture-expand">
          <Expand size={19} />
        </span>
      </button>
      <div className="capture-caption">
        <span>ACTUAL WORKSPACE / 19 OCT 2026</span>
        <p>
          Infrastructure was unavailable at capture. Unknown stays unknown; this
          image is not a recovery result.
        </p>
      </div>
      <dialog
        ref={dialog}
        className="image-dialog"
        onClick={(e) => {
          if (e.target === dialog.current) dialog.current.close();
        }}
      >
        <button
          className="icon-button dialog-close"
          title="Close image"
          aria-label="Close image"
          onClick={() => dialog.current.close()}
        >
          <X />
        </button>
        <img
          src="./images/workspace-overview.png"
          alt="Full operations workspace screenshot, October 19, 2026"
        />
      </dialog>
    </section>
  );
}

function Evidence() {
  const cases = evidenceRows.map((row) => [
    row.name,
    `${row.handling.toFixed(3)} s`,
    row.tokens.toLocaleString("en-US"),
    row.outcome,
    row.status,
  ]);
  return (
    <section className="evidence section" id="evidence">
      <div className="section-top">
        <SectionLabel number="04">RESEARCH & EVIDENCE</SectionLabel>
        <span className="mono muted">OBSERVATIONS, NOT PROMISES</span>
      </div>
      <div className="section-intro">
        <h2>
          Show the work.
          <br />
          <span className="serif">Keep the caveats.</span>
        </h2>
        <p>
          These selected development trials used real Groq responses and real
          sandbox procedures. Failures are part of the record.
        </p>
      </div>
      <div className="evidence-table-wrap">
        <table>
          <caption className="sr-only">
            Individual development trial observations from October 13 and 14,
            2026
          </caption>
          <thead>
            <tr>
              <th>Development trial</th>
              <th>Agent handling</th>
              <th>Model tokens</th>
              <th>Independent outcome</th>
            </tr>
          </thead>
          <tbody>
            {cases.map(([name, time, tokens, result, status]) => (
              <tr key={name}>
                <th scope="row">{name}</th>
                <td className="mono">{time}</td>
                <td className="mono">{tokens}</td>
                <td>
                  <span className={`outcome-square ${status}`} />
                  {result}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="evidence-caveat">
        <span className="mono">READ THESE NUMBERS CORRECTLY</span>
        <p>
          Individual observations, not averages or a frozen comparative
          benchmark. Handling excludes detection. The payment alert originated
          at payment, so that trial is not a multihop localization result. No
          LLM advantage over equal-information baselines has been established.
        </p>
      </div>
      <div className="research-links">
        <OutLink href={doc("eval/results/verified/20261014/README.md")}>
          <FileText size={17} />
          Trial record & retained failures
        </OutLink>
        <OutLink href={doc("docs/JOURNAL_STUDY_PLAN.md")}>
          <FileText size={17} />
          Journal study plan
        </OutLink>
        <OutLink href={doc("docs/AGENTIC_SCOPE.md")}>
          <FileText size={17} />
          Autonomy & limitations
        </OutLink>
      </div>
      <div className="scope-columns">
        <div>
          <span className="mono scope-label">
            <span className="green-square" />
            IMPLEMENTED
          </span>
          <p>
            Fixed-catalog recovery loop. Dual-graph retrieval. Restricted
            execution. Application-level verification. Bounded shadow
            investigation.
          </p>
        </div>
        <div>
          <span className="mono scope-label">
            <span className="red-square" />
            STILL BEING VALIDATED
          </span>
          <p>
            Broader live symptom diagnosis, fresh-evidence replanning, durable
            recovery contracts, held-out evaluation and deployment transfer.
          </p>
        </div>
      </div>
    </section>
  );
}

function GetStarted() {
  const [tab, setTab] = useState("setup"),
    [copied, setCopied] = useState(false),
    [copyError, setCopyError] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(commands[tab]);
      setCopied(true);
      setCopyError(false);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2200);
    } catch {
      setCopyError(true);
    }
  };
  return (
    <section className="get-started section" id="get-started">
      <div className="section-top">
        <SectionLabel number="05">RUN THE RESEARCH STACK</SectionLabel>
        <span className="mono muted">LOCAL FIRST / SOURCE CHECKOUT</span>
      </div>
      <div className="start-grid">
        <div>
          <h2>
            Open source.
            <br />
            <span className="serif">Open to inspection.</span>
          </h2>
          <p>
            Python 3.11+, Docker Compose, Neo4j and a configured model provider.
            The reviewed testbed is Google Online Boutique.
          </p>
          <p className="start-limit">
            A research prototype, not a production service. No pip, npm or
            Homebrew distribution is available yet. Start in observation-only
            mode.
          </p>
          <OutLink className="text-link" href={`${REPO}#quick-start`}>
            Full setup & environment guide
          </OutLink>
        </div>
        <div className="terminal">
          <div className="terminal-bar">
            <div role="group" aria-label="Setup commands">
              {[
                ["setup", "01 Setup"],
                ["infrastructure", "02 Infrastructure"],
                ["observe", "03 Observe"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  aria-pressed={tab === id}
                  onClick={() => {
                    setTab(id);
                    setCopied(false);
                    setCopyError(false);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              className="icon-button inverse"
              title={copied ? "Copied" : "Copy command"}
              aria-label={copied ? "Command copied" : "Copy command"}
              onClick={copy}
            >
              {copied ? <Check size={17} /> : <Copy size={17} />}
            </button>
          </div>
          <pre>
            <code>{commands[tab]}</code>
          </pre>
          <div className="terminal-note">
            <span className="mono">
              {copyError
                ? "Clipboard unavailable"
                : copied
                  ? "Copied to clipboard"
                  : "$  local workspace"}
            </span>
            <span>Secrets stay in your local .env</span>
          </div>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-top">
        <span className="eyebrow">AN EXPERIMENT IN ACCOUNTABLE AUTOMATION</span>
        <OutLink href={REPO}>
          Explore the repository
          <Github size={17} />
        </OutLink>
      </div>
      <div className="footer-wordmark">
        GraphRAG<span>.</span>
      </div>
      <div className="footer-bottom">
        <div>
          Raghav Nimbalkar<span>MTech AI/ML / MIT World Peace University</span>
        </div>
        <div>
          Agentic GraphRAG for Root Cause Analysis
          <span>Research prototype / October 2026</span>
        </div>
        <a href="#top" className="back-top" aria-label="Back to top">
          <ArrowUpRight size={19} />
          Back to top
        </a>
      </div>
    </footer>
  );
}

function App() {
  return (
    <>
      <a className="skip-link" href="#architecture">
        Skip to content
      </a>
      <Header />
      <main>
        <Hero />
        <Architecture />
        <Walkthrough />
        <Workspace />
        <Evidence />
        <GetStarted />
      </main>
      <Footer />
    </>
  );
}
createRoot(document.getElementById("root")).render(<App />);
