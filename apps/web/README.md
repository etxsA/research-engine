# Research engine web app

The browser front end of the research engine. It talks to [research-api](../../packages/research-api/README.md) and nothing else, and it runs on your own machines: no analytics, no third-party scripts, no hosted fonts.

It has the build, the providers, sign-in and the forced password change, the layout with a status badge, light and dark themes, Spanish and English, the TypeScript client generated from the API's OpenAPI document, and the test and CI setup. The **Circuits** screen is built (see [Circuit check](#circuit-check)). The other three screens (Ask, Benchmarks, Question sets) are placeholders that later pull requests fill in.

## Run it

You need Node 22.22 or later (`.nvmrc` pins Node 24.22.0 for CI) and [pnpm](https://pnpm.io) 12 (`packageManager` in `package.json`). From this folder:

```bash
pnpm install --frozen-lockfile
```

Start the API in fake mode (test data, loopback only) in one terminal and the web app in another:

```bash
# repository root
uv sync --locked && uv run research-api serve --fake       # http://127.0.0.1:8090
# apps/web
pnpm dev                                                   # http://localhost:5173
```

The dev server forwards `/api` to the API (`RESEARCH_API_URL` changes the target), so the browser only talks to its own origin and the API needs no CORS. In production the API serves the built files from the same origin ([The web app](../../packages/research-api/README.md#the-web-app) in its README; [Run the web app on a shared server](../../docs/run-guide.md#run-the-web-app-on-a-shared-server) for the install steps).

The API does not serve sign-in yet, so in development `/login` finds no `/api/v1/auth/*` endpoints. Until the sign-in pull request lands, the screens behind the session cannot be reached by hand; the tests use a mock (see [Sign-in](#sign-in-and-the-mock)).

## Scripts

| Command                             | Does                                                                                                                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm dev`                          | Vite dev server with the `/api` proxy                                                                                                                                                       |
| `pnpm build`                        | `tsc -b`, then the production bundle in `dist/`                                                                                                                                             |
| `pnpm preview`                      | Serve `dist/` with the same proxy                                                                                                                                                           |
| `pnpm lint`                         | ESLint (type-aware, React hooks, accessibility, import boundaries)                                                                                                                          |
| `pnpm format` / `pnpm format:check` | Prettier, with Tailwind class sorting                                                                                                                                                       |
| `pnpm typecheck`                    | `tsc -b` with `strict` and `noUncheckedIndexedAccess`                                                                                                                                       |
| `pnpm test`                         | Vitest and Testing Library; `pnpm test --coverage` enforces 80% of lines                                                                                                                    |
| `pnpm size`                         | Bundle budget after a build: entry chunk at most 250 KiB gzipped, other chunks at most 400 KiB, KaTeX out of the entry                                                                      |
| `pnpm gen:api`                      | Regenerate `src/shared/api/schema.d.ts` from `src/api/openapi.json`                                                                                                                         |
| `pnpm contract`                     | Fail when `openapi.json` or `schema.d.ts` differ from what the API and the generator produce now (needs uv)                                                                                 |
| `pnpm capture:circuits`             | Record the circuit endpoints' answers from a running API as test fixtures (see [Circuit check](#circuit-check))                                                                             |
| `pnpm e2e`                          | Playwright tests against the built app and the API in fake mode                                                                                                                             |
| `scripts/check.sh [--e2e]`          | All of the above except `e2e` unless asked; the repository's `scripts/check.sh` calls it when pnpm is on the PATH. `pnpm audit` is not part of it: it needs the network and runs in CI only |

Playwright needs a browser: `pnpm exec playwright install chromium`, or `E2E_CHANNEL=chrome pnpm e2e` to use an installed Google Chrome. Traces and screenshots are off on purpose, so no corpus text is ever written to a report.

## The API contract and the generated client

The API's OpenAPI 3.1 document is committed as `src/api/openapi.json` and the types made from it as `src/shared/api/schema.d.ts` (generated, never edited). The `contract` CI job regenerates both and fails on any difference. After a change in `research-api`:

```bash
uv run research-api openapi -o apps/web/src/api/openapi.json
cd apps/web && pnpm gen:api
```

`src/shared/api/client.ts` builds one client with `openapi-fetch` and `openapi-react-query`. Two middlewares run on every call: one adds the `X-CSRF-Token` header (from the `re_csrf` cookie) to writes, the other turns any error response into an `ApiError` (RFC 9457 problem details), so callers handle one error type.

## Sign-in and the mock

The sign-in endpoints (`/auth/login`, `/auth/logout`, `/auth/me`, `/auth/password`) are in the generated types like any other; `src/shared/api/types.ts` gives `Me`, `LoginRequest` and `PasswordChangeRequest` their short names.

`src/test/auth-mock.ts` is a small state machine for those endpoints (two made-up accounts, one of them with a temporary password, a failed-login limit, a CSRF check). The Vitest tests serve it through MSW and the Playwright test through route interception. In the end-to-end smoke test `/status` is answered by the fixture too, because the API gives it to signed-in members only and the mock makes no real session; `/health` comes from the real API in fake mode. Its accounts (`ana`, and `temp` with a temporary password) copy two of the API's seeded test users for fake mode, so the mock and the real API answer to the same names. The mock stays so that most tests need no account state; `e2e/real-api.spec.ts` signs in against the real API in fake mode with no mock (a wrong password, `ana` signing in and out with the status badge, `temp` changing its temporary password). Each of its accounts is used by one test only, because the fake server keeps its accounts until the run ends and a changed password stays changed; so `pnpm e2e` does not reuse an API left running on port 8091 (stop it first), and that file runs with no retry.

## Circuit check

`/circuits` reads a circuit, simulates it and checks it. It uses the generated client for `/circuits/parse`, `run`, `check` and `conventions`, and needs no model server: in fake mode the API runs the real parser and simulator. The four endpoints need a signed-in member, so `e2e/circuits.spec.ts` signs in against the real API as the seeded member `dani`, which no other end-to-end file uses (a test that talks to the API directly gets its own session, with the CSRF header and `Origin` the API checks, from the `api` fixture in `e2e/fixtures.ts`).

| Part        | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Input       | A LaTeX tab (quantikz, Qcircuit, yquant, tikz-cd) and a `circuit/1` JSON tab, ten examples, Ctrl+Enter to read. LaTeX goes to `/circuits/parse`. A `circuit/1` document is read in the browser, and a key written twice is refused there with its name (`strictJson.ts`); the API refuses it too and stays the authority, and the JSON typed by the person is sent as typed, never re-written.                                                                                                                                                                                                                                    |
| Gate list   | The status (`parsed`, `partial`, `unparsed`), one table per circuit and per side of a drawn equality (step, gate, targets, controls, open controls), the wire legends, the gate list in words, and a list of every part the reader did not understand. A block with several circuits offers a choice.                                                                                                                                                                                                                                                                                                                             |
| Simulate    | Input state (basis label or amplitudes), parameter values as `name = value` lines, the unitary as a switch. Shows the amplitudes and a bar list of probabilities (first wire most significant), the unitary (a 16 by 16 corner of a large one, the whole matrix as a download), the measured wires and the notes. A table wider than its card scrolls sideways inside it, in a region a keyboard can focus. Screen readers get one short sentence when a reading or a result arrives (the tables are not live regions). A circuit with a symbol and no value says which names are missing and adds them to the values on a click. |
| Check       | Against a state, a unitary, a second circuit (LaTeX or `circuit/1`) or the equality drawn in the circuit. The verdict is _Equal up to global phase_ or _Not equal_ with the largest deviation, and a table of the comparisons (method, exact, phase).                                                                                                                                                                                                                                                                                                                                                                             |
| Refusals    | A circuit the simulator refuses is a `200` with `status: refused`. The screen shows it as **Refused**, in a different style from _Not equal_, with every reason word for word, and never as a result. A `400`, `413`, `422` or `503` from the API is a separate problem panel with its detail, the field it points at and `Retry-After`.                                                                                                                                                                                                                                                                                          |
| Conventions | A card with the conventions of the last result (or the simulator's own before any), the limits, and the gate table. The screen takes up to 12 wires, or 8 for a unitary or for a comparison of two circuits (the API's caps).                                                                                                                                                                                                                                                                                                                                                                                                     |

State: what was typed, what was read and the last answer are in one Zustand store (`store.ts`), in memory only. Anything that a request depends on clears the answer, so the screen never shows a result for text that is no longer there; an answer that arrives after the circuit text changed is dropped, but a reading survives edits of the options. The three requests (`flows.ts`) go through TanStack mutations, so a `401` sends the person to sign in.

The API passes `quantum-sim-result/1` and `circuit/1` through as open objects, so `types.ts` types them by hand and reads every answer through guards; an answer of another shape is a problem panel, not a crash. `src/features/circuits/fixtures/captured.json` holds 35 real answers of the API (parse, run and check, refusals and errors). The unit tests replay them through MSW (`src/test/circuits-api.ts`; a request with no recording fails the test), and the end-to-end test asks the API for every request again and fails when an answer differs, so the file cannot go stale. After a change in the API or in quantum-sim, start the API in fake mode and run `pnpm capture:circuits http://127.0.0.1:8091`.

## Structure

```text
src/
  app/            router, providers, layout, top bar, error and not-found screens
  features/       one folder per screen: auth, status, ask, benchmarks, qsets, circuits (built)
  shared/
    api/          generated types, the client, ApiError
    i18n/         es.json, en.json, language detection
    layout/       page heading, placeholder page
    forms/        labelled text field
    lib/          storage, theme, class-name helper
    stores/       Zustand store for the client-only choices (theme, language)
    ui/           shadcn/ui components (generated)
  api/openapi.json   the committed contract
  test/           test setup, MSW server, the sign-in mock
e2e/              Playwright tests
```

The same rules as the API's import contracts, enforced by ESLint (`no-restricted-imports`):

- A feature never imports another feature or `app`, and stays inside its own folder (no `../`). What two features need goes to `shared`.
- `shared` never imports `features` or `app`.
- Components do not call `fetch`. Only the hooks in `features/*/api.ts` do, through the client.

Server state lives in TanStack Query, client-only choices in Zustand; nothing the server owns goes into a store. Screens are lazy routes, so heavy libraries load only where they are used.

## Sessions and routes

A route loader on `/` asks `/auth/me` through the query client. No session sends the visitor to `/login?next=<path>` (the path is kept only if it stays inside the app); a session that must change its password is held on `/login`, which then shows the change form instead of the sign-in form. The return path must be a plain path on this origin: a scheme, `//host`, a backslash or a control character (a browser drops a tab inside a URL, so `/<tab>/host` would read as `//host`) discards it. A 401 on any later request ends the session in the page: the cache is cleared and the user returns to `/login`. Signing out clears the cache and leaves only after the server has answered; if the call fails the page stays and a message says so. The session check itself is exempt from that rule (`EXPECTS_UNAUTHORIZED`), because its 401 is what the guards read.

After a navigation the page heading takes focus and sets the tab title (`useFocusPageTitle`); the login screen does the same when the sign-in form gives way to the password change and when it opens after signing out. A skip link jumps to the content.

Every screen sits under one error boundary inside the layout, so an error in a screen, or a lazy chunk that no longer exists after a new release, replaces that screen only. "Try again" asks the loaders again after a failed request and reloads the page after anything else.

## Languages and themes

The language is the saved choice (`localStorage` key `re.lang`), else the browser's first Spanish or English language, else Spanish. `es.json` and `en.json` must have the same keys and placeholders; a test fails otherwise. The terms of the data formats (`bare`, `harness`, `oracle`, `qset/1`, metric names) will stay as they are in both languages.

The theme is `system` (default), `light` or `dark` (key `re.theme`). `public/theme-init.js` sets the `dark` class before the first paint. It is an external file, not an inline script, so the API's Content-Security-Policy can stay at `script-src 'self'`. Every `localStorage` access is guarded: the app works with storage blocked.

## UI components

The components in `src/shared/ui` come from [shadcn/ui](https://ui.shadcn.com) (`radix-nova` style, Radix primitives, Tailwind CSS 4). `components.json` is set up for `pnpm dlx shadcn add <name>`. The CLI does not read the `@` alias from this project's split tsconfig, so it writes files under a folder literally named `@/` and imports `cn` from a package of that name; move the files into `src/shared/ui` and point the import at `@/shared/lib/utils` (the `cn` package it adds is not wanted).

## Dependencies

Versions are exact in `package.json` (`@types/node` included) and locked in `pnpm-lock.yaml`; `.nvmrc` names one Node release. `pnpm-workspace.yaml` sets `minimumReleaseAge` to 7 days, so a release younger than a week is never installed; a few packages are therefore one patch behind the newest (for example React Router 8.4.0, TanStack Query 5.104.3). TypeScript is 6.0.3: typescript-eslint does not support 7 yet. Two peer ranges are widened there on purpose, after checking that the tools run: `eslint-plugin-jsx-a11y` under ESLint 10 and `openapi-typescript` under TypeScript 6.

pnpm 12 runs no dependency build script unless it is allowed, and `onlyBuiltDependencies` is left unset on purpose: no package in the lock file declares an install script, so none can run code at install time.

New runtime dependencies need the lead's approval.

## Tests

Vitest runs the unit and component tests next to the code (the circuit screen has most of them: strict JSON, value parsing, number formatting, the gate list, the answer guards, every panel and every problem) (`*.test.ts(x)`): the client and its middlewares, error mapping, the theme and language rules, the forms, the router guards, the whole app on an in-memory router (sign-in, forced change, sign-out, session ending under an open page, navigation, theme and language switches, the status badge) and the translation parity. Playwright runs the smoke test (`e2e/smoke.spec.ts`, sign-in answered by the mock), the real sign-in test (`e2e/real-api.spec.ts`) and the circuit screen's acceptance checks (`e2e/circuits.spec.ts`: C1 to C6 by hand, C7 as every LaTeX source of quantum-sim's bridge folder sent through the screen and compared with what the API says, the recorded answers, both themes, Spanish, a phone width, and 4 and 8 wire unitaries kept inside their cards at phone and desktop widths; it signs in against the real API first) in Chromium against the production bundle, with an axe scan (WCAG 2.2 A and AA) of the login and forced-change pages in light and in dark (each page loads in its scheme) and of the app layout, also after a theme switch. The scan first waits for running transitions to end, so it never reads a color halfway through a fade; the focus moves after signing in and out are checked too.

## CI

Two jobs in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml): `web` (lint, format check, typecheck, unit tests with coverage, build, bundle budget, `pnpm audit --prod --audit-level high`) and `contract` (the drift check, then the Playwright smoke test against `research-api serve --fake`).
