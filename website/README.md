# Project Website

The explanatory React/Vite site lives here, in the same repository as the RCA
system. It is separate from the Streamlit operations dashboard. It has no
backend, analytics, incident endpoint, model client or remediation permissions.

## Development

Use Node 22.12+ (24 recommended):

```bash
cd website
npm ci
npm run dev
```

Vite prints the actual local URL and selects another port if needed.

```bash
npm test
npm run build
npm run preview
```

Browser regression checks:

```bash
npx playwright install chromium
npx playwright test
```

The website workflow runs content checks, a production build, and desktop/mobile
browser tests. Backend Python checks remain separate.

## Content and Provenance

- `src/catalog.json` is a public projection of the reviewed service registry and
  declared topology. Regenerate from the repository root inputs with
  `npm run sync:catalog`; tests fail when the snapshot drifts.
- `src/model.mjs` holds the illustrative walkthrough and source-checkout commands.
  The walkthrough is not a live fault, model call or recorded event replay.
- `src/evidence.mjs` contains selected dated development observations. Tests
  compare every displayed timing, token total and overall outcome with the
  reviewed export. CPU mitigation must retain its cleanup-failure caveat.
- `public/images/workspace-overview.png` is an unmodified existing project
  capture. Its caption discloses unavailable infrastructure and unknown state.
- Fonts are bundled locally from Fontsource; controls use Lucide icons. The
  display map uses canvas lines and accessible DOM buttons, not a live graph API.

No production-readiness, packaged installer, published-paper acceptance, general
accuracy or LLM-superiority claim is made. Source evidence stays linked directly
to the repository. Private research notes and manuscript drafts are not included.

## Hosting

`npm run build` creates a static `dist/`. Relative asset paths support deployment
at a root or subdirectory. The tracked `.openai/hosting.json` records the Site
identity; it contains no credentials. The initial hosted preview is owner-private.
Site access is separate from the public GitHub repository and must be explicitly
changed before advertising a public launch.

The repository remains the maintained source. Deployment packaging uses a
disposable ignored copy containing only `website/`, never the Python project,
`.env`, audit records, private research files or Docker credentials. Temporary
hosting credentials must stay in memory and go to the source helper over stdin.

When moving to another host, update canonical/social URLs in `index.html`.
`public/images/site-preview.png` is a rendered website screenshot for link previews.

## Third-Party Notices

Redistributed font, icon and UI-runtime notices are included with the static
assets: [DM Sans](public/licenses/dm-sans.txt), [DM Mono](public/licenses/dm-mono.txt),
[Instrument Serif](public/licenses/instrument-serif.txt),
[Lucide](public/licenses/lucide.txt), [React](public/licenses/react.txt) and
[React DOM](public/licenses/react-dom.txt). Dependency versions are locked in
`package-lock.json`.
