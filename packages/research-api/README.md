# research-api

The HTTP API of the research engine. A FastAPI service built in the same hexagonal style as [docingest](../docingest/README.md): the rules sit in a pure core, the libraries sit in adapters, and import-linter contracts keep it that way. It serves the web app with cited answers, the papers they cite, the circuit check, question sets and benchmark runs, using docingest and [quantum-sim](../quantum-sim/README.md) as libraries.

It has the layers, the import contracts, the settings loader, a fake mode, the error format, the OpenAPI export, the circuit check endpoints, the sign-in part (accounts in SQLite, Argon2id password hashes, cookie sessions with a CSRF check, a login rate limit, two roles, the `users` command and the private data folder), the ask path (questions answered one at a time by docingest, followed as server-sent events, with the cited papers readable at a page: see [Asking and reading papers](#asking-and-reading-papers)) and the question sets: a catalog of `qset/1` files with drafts, items edited under `If-Match`, validation, import, export, reviews and freezing, and the benchmark jobs: runs of the arms `bare`, `harness` and `oracle` on a frozen set (see [Benchmark jobs](#benchmark-jobs)), one at a time in the lane the asks use, with progress, a log, cancel, resume and recovery after a stop, and scorings that compare runs with the qa-bench scorer. It also serves the built web app from its own origin ([The web app](#the-web-app)). The web screens arrive in their own pull requests, each adding use cases, ports and adapters inside this structure.

## Run it

```bash
uv sync --locked --all-extras             # from the repository root
cd packages/research-api
uv run research-api serve --fake          # test data, 127.0.0.1:8090 (--port N for another port)
uv run research-api serve --fake --ask-pause 0   # the same, with the fake engine answering at once
uv run research-api serve --fake --bench-pause 0 # and the fake benchmark answering at once
curl -s http://127.0.0.1:8090/api/v1/health
curl -s http://127.0.0.1:8090/api/v1/status
```

`--fake` builds the service from stand-ins: no settings file, no network, a clock fixed at 2026-01-01 so documents repeat exactly, and a throwaway SQLite database in a private temporary folder that is removed when the server stops. It answers on loopback only. It creates the test accounts of [`tests/fixtures/seed-users.json`](tests/fixtures/seed-users.json) (made-up names and test passwords: `ana` admin, `beto` and `dani` members, `temp` a member with a temporary password that must be changed at the first sign-in), or those of the file named by `--seed FILE`. It is what the end-to-end tests of the web app run against. The clock of fake mode is fixed, so a session never expires and a rate-limit block never lifts: a test that fails five sign-ins on purpose should use its own account (`dani`) and a restart clears it. Fake mode also accepts any loopback `Origin` and `Host`, because the web app's development and test servers run on another loopback port and forward `/api` here. It also fills the question sets with a few made-up ones (see [Question sets](#question-sets)): a frozen version, a draft with a review in every state, and a draft of circuit questions.

A real run needs a settings file, a database and at least one account:

```bash
export RESEARCH_API_CONFIG=/path/to/research-api.toml    # or pass --config FILE to each command
uv run research-api db upgrade                           # makes the private folders and the tables
uv run research-api users add ana --name "Ana Prueba" --role admin    # prints a one-time password
uv run research-api serve
```

Start from [`config.example.toml`](config.example.toml). Keep your copy outside the repository.

| Key | Default | Rule |
|---|---|---|
| `bind_host` | `127.0.0.1` | A loopback address or a Tailscale address (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`). `0.0.0.0`, a LAN address or a host name is refused by the settings loader. |
| `port` | `8090` | An integer from 1 to 65535 (`"8090"` and `8090.0` are refused). |
| `public_origin` | `http://<bind_host>:<port>` | The origin people type in the browser: scheme, host and port only (no path, query, fragment or credentials). It is rewritten the way a browser sends it in the `Origin` header: scheme and host in lowercase, an IPv6 host in its short form, no leading zeros in the port, and no port when it is the default one (`HTTP://Tool.Example:080` becomes `http://tool.example`). A value no browser sends is refused: an empty or zero port, a space or other stray character, a trailing dot, a numeric host that is not a plain IPv4 address. A write request must send exactly this as its `Origin`, and the `Host` header must name this host. |
| `data_dir` | none | Absolute path of the private data folder (sets, runs and scores will live here). Needed to run for real. |
| `db_path` | none | Absolute path of the SQLite file. Needed to run for real. Keep it in its own folder, apart from `data_dir`, so each can be backed up on its own. |
| `log_dir` | none | Absolute path of the log folder. Without it the log lines go to standard error. |
| `web_dir` | none | Absolute path of the built web app (`apps/web/dist`, or a link to it). When set, the API serves it from its own origin, see [The web app](#the-web-app). |
| `session_idle_hours` | `12` | A session ends after this many hours without a request (1 to 720). |
| `session_max_days` | `7` | A session ends this many days after sign-in whatever it does (1 to 90). Not shorter than the idle time. |
| `login_max_failures` | `5` | Failed sign-ins allowed per username in the window (1 to 100). One client address gets four times as many. |
| `login_window_minutes` | `15` | The window of the two limits above (1 to 1440). |
| `[circuit_caps]` | see [Circuit check](#circuit-check) | A table with `max_body_bytes`, `max_ops` and `timeout_s`. Each is checked against a range, and a key that is not one of the three is an error. |
| `[[models]]` | none | The models a benchmark run may ask: `name` (what the server serves it as), `endpoint` (the address up to `/v1`, no credentials, query or fragment: a key never goes here; plain `http` only for a loopback or Tailscale address, `https` for any other host; the client ignores the proxy variables of the environment), and optionally `label` and `revision`. See [Benchmark jobs](#benchmark-jobs). A name listed twice is an error. |
| `docingest_config` | none | Absolute path of the docingest settings file that names the papers (`output_dir`), the index and the model servers. A file that does not exist or cannot be read stops the start. Without the key everything works except asking questions and reading papers: `/status` says so and an ask ends with a `503`. The file is read once, so a change needs a restart (the index itself is reloaded by itself when a build commits). |

A key that is not in the table is an error. Later pull requests add their keys here (backups).

To run under another process manager: `uvicorn research_api.entrypoints.http:create_app --factory`, with `RESEARCH_API_CONFIG` (or `RESEARCH_API_FAKE=1`) in the environment. `RESEARCH_API_WEB_DIR` in the environment sets `web_dir` for the factory, in fake mode too. [`deploy/research-api.sh`](../../deploy/research-api.sh) is that process manager for a shared server without sudo: [Run the web app on a shared server](../../docs/run-guide.md#run-the-web-app-on-a-shared-server). The `serve` command turns uvicorn's access log off (a raw access log would hold paths and query strings) and its forwarded-address handling off (uvicorn would otherwise believe an `X-Forwarded-For` header sent from this machine, and so let any account on a shared server pick the address the rate limit sees). The factory sets up the service's log file and silences the access log itself, but only the command line of uvicorn can turn off the forwarded headers: pass `--no-proxy-headers`.

The address rule does not depend on how the server is started. The app checks the local address of every connection (`scope["server"]`, set by uvicorn) and answers `403` with a problem document when it is not allowed: loopback only in fake mode; loopback or exactly the `bind_host` address in live mode (the rest of the Tailscale block, `100.64.0.0/10`, is also carrier-grade NAT space that other hosts use, so it is not accepted). So `uvicorn ... --factory --host 0.0.0.0` listens on every interface but serves nothing on the ones it must not. Prefer `--host` set to the address you mean anyway, so the port is not open on the others. A server without an IP address (a Unix socket) is refused too. The check is in `entrypoints/http/guard.py`.

## The web app

With `web_dir` set, the service also answers every path outside `/api` with the built web app, so the browser meets one origin: the page, the API and the cookies share it, and the API needs no CORS. Nothing is served when `web_dir` is not set (every path outside `/api/v1` is then a 404 problem).

| Request | Answer |
|---|---|
| A path that names a file in the folder | That file, with its type, `ETag` and `Last-Modified` (`304` for a request that already holds it). Files under `/assets/` carry a content hash in their names and are cached for a year; everything else (`index.html`, `theme-init.js`, `version.json`) must be revalidated. The file served decides, not the path asked for: `index.html` reached through `/assets/` or `/assets/%2e%2e/index.html` is revalidated too. |
| Any other path (`/login`, `/sets/x`, `/sets/demo/v1/items/q1.2`) | `index.html`: these are routes of the single-page app, whatever the last segment looks like. An item id may hold dots, and reloading its page must show the app. |
| A path under `/assets/` with no such file (`/assets/app-old.js`) | `404` problem. A page from an older release asking for a script that no longer exists must fail, not receive HTML. A missing file outside `/assets/` (`/missing.css`) is a route of the app and gets `index.html`. |
| Anything under `/api` that no route matched | `404` problem, never the home page. |
| A wrong method on a route of the API (`DELETE /api/v1/health`) | `405` with `Allow`, the same answer as without a web app: the web app is the router's fallback and sees only paths no route matched. |
| A method other than `GET` and `HEAD` outside `/api` | `405` problem with `Allow`. |
| The folder or its `index.html` is missing | `503` problem: "The web app is not installed on this server." |
| A path that leaves the folder (`..`, a backslash, a NUL, a symbolic link that points out), or a segment longer than 255 bytes | `404` problem. |

Every answer of the web app carries `Content-Security-Policy` (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`), `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`. Only styles may be inline (the UI libraries set style attributes); scripts and connections are limited to the origin itself.

`web_dir` may be a symbolic link: it is resolved on every request, so pointing the link at a new build changes what is served with no restart. The bind, `Host` and `Origin` checks and the access log (which records the route as `-`, never the path) apply to these paths like to the API's. The code is `entrypoints/http/webapp.py`; the page is not part of the OpenAPI contract.

## What it serves now

All paths are under `/api/v1`.

| Method and path | Who | Does |
|---|---|---|
| `GET /health` | anyone | `{"ok": true}`, for a start script or a load check. |
| `GET /status` | member | `mode` (`live` or `fake`, an enum in the contract), `api_version`, `started_at`, `uptime_s`, `ready`, and `ask`: `state` (`warming_up`, `ready` or `unavailable`), a fixed-text `reason` when unavailable, `index_generation`, `queued` (asks waiting for the GPU lane) and `busy`; and `bench`: `running_job`, `queued_runs`, `scoring` and `queued_scores`. It is for members because it shows when someone is asking (`/health` is the open one). In live mode `index_generation` is empty until the first question has been answered. Model-server probes join it later. |
| `POST /auth/login` | anyone | `{username, password}`. `204` and two cookies. `401` with one message for a wrong password, an unknown name and a disabled account; `429` with `Retry-After` past the limit. |
| `POST /auth/logout` | signed in | Ends the session, clears both cookies, `204`. |
| `GET /auth/me` | signed in | `{username, display_name, role, must_change_password, language}`. Works while a password change is pending. |
| `POST /auth/password` | signed in | `{current, new}`. `204`; the response sets fresh cookies and every other session of the user ends. `403` for a wrong current password, `422` (pointer `/new`) for a refused new one, `429` after repeated wrong ones. Works while a password change is pending. |
| `GET /admin/sessions` | admin | Signed-in browsers: `id`, `username`, `created_at`, `last_seen_at`, `expires_at`, `current`. Cookie values are never shown. |
| `DELETE /admin/sessions/{session_id}` | admin | Ends one session, `204`, or `404`. |
| `POST /asks` | member | `{question, use_index?, bm25?, whole_blocks?, return_contexts?}`. `202 {ask_id, position}` at once; the question waits in the GPU lane. `422` for a bad body, `429` past three open asks of one member, `503` (with `Retry-After`) while the engine starts or when sixty asks are open. |
| `GET /asks/{ask_id}/events` | owner | `text/event-stream`: `queued`, `retrieving`, `answering`, then `answer`, `error` or `cancelled`. See below. `204` once everything was seen. |
| `GET /asks/{ask_id}` | owner | `{ask_id, state, position, phase, response, error}`: where the ask is and, when done, its `AskResponse`. |
| `DELETE /asks/{ask_id}` | owner | `204`. Takes a waiting ask out of the queue, marks a running one so its answer is dropped, forgets a finished one. |
| `GET /documents?query=&limit=` | member | `{items, total}`: papers by part of a title or the start of an id, `limit` 1 to 100 (default 20). |
| `GET /documents/{doc_id}` | member | Title, source, authors, year, and each page with how its text was made. `doc_id` is the full id or the 16 or more hex characters a citation prints; `409` if two papers match. |
| `GET /documents/{doc_id}/pages/{n}` | member | `{doc_id, page, pages, kind, title, markdown}`: page `n` (1-based, as the page markers of the paper). `kind` is `section` for a LaTeX source, whose markers number sections. |
| `GET /sets` | signed in | The catalog: each set with its versions (`status` `draft` or `frozen`, `md5`, `etag`, `items`, `owner`, dates) and the review count of each. |
| `POST /sets` | signed in | `{name, corpus?, copy_from?}`. `201` and a new draft: empty under a new name, or the next version of an existing set as a copy of `copy_from`. `409` when the name exists and no `copy_from` is given. |
| `POST /sets/validate` | signed in | `{item}` or `{text}`: a dry run against `qset/1`. Every problem with its place; nothing is stored. |
| `GET /sets/import/shapes` | signed in | The older file shapes that can be converted. |
| `POST /sets/import` | signed in | The body is the file (`qset/1`, plain or gzip, or an older shape named by `?shape=`); `?name=`, `?version=`, `?corpus=`, `?source_file=`. `201` and a new draft. All or nothing: a refused file is a `422` that lists the lines and fields at fault. At most 20 MiB, counted again once unzipped (`413`). |
| `GET /sets/{name}/versions/{version}` | signed in | The header, review counts and a page of item summaries. Filters `form`, `content`, `lang`, `split`, `tag`, `review`, `q`; `offset`, `limit` (at most 200). `ETag` header. |
| `GET /sets/{name}/versions/{version}/items/{id}` | signed in | One item, its decisions, its review state and what `qset/1` says about it. |
| `PUT /sets/{name}/versions/{version}/items/{id}` | owner or admin | Replace an item of a draft. `If-Match` required (`428` without it or with `*`, `409` when the draft changed). `422` with a JSON pointer per problem. `409` of type `item-has-decisions` when people have already decided on the item and the text would change (see Reviews). |
| `POST /sets/{name}/versions/{version}/items` | owner or admin | Add an item to a draft (`If-Match` as above). `201`. `409` when the id belongs to a deleted item that has decisions. |
| `DELETE /sets/{name}/versions/{version}/items/{id}` | owner or admin | Remove an item (`If-Match` as above). `409` when it is the original of a mirror. |
| `GET /sets/{name}/versions/{version}/export` | signed in | The file exactly as stored (`application/x-ndjson`): its md5 is the version's md5. |
| `GET /sets/{name}/versions/{version}/reviews` | signed in | The decisions on a version, oldest first (`?item_id=` for one item). |
| `POST /sets/{name}/versions/{version}/reviews` | signed in | `{item_id, verdict, note?, edit?}`: accept, edit or reject one item of a draft. `If-Match` with the `etag` of the draft you read is required: `428` without it or with `*`, `409` when the draft changed since (read it again). A list of tags is allowed as for item writes (see Drafts, versions and freezing): the review goes through when one of them is the current tag, and the stored md5 is then the current one. The server fills in the reviewer (you), the role `human`, the date and the set md5 (the one you read). |
| `GET /sets/{name}/versions/{version}/freeze` | signed in | What freezing would do, by item, and the `etag` of that plan (`ETag` header and `plan.etag`). Nothing changes. Refuses what the freeze would refuse (`422`, naming the item by id). |
| `POST /sets/{name}/versions/{version}/freeze` | owner or admin | Applies the reviews and closes the version for good. `If-Match` with the plan's `etag` is required: `428` without it, `409` when a decision or an item changed since the plan was read. |
| `GET /models` | signed in | The models of the settings, each probed with a two second timeout: `{name, label, revision, available}`. |
| `POST /runs` | signed in | `{set: {name, version}, arms: [{name, variant?, overrides?}], models: [name], repeats?, notes?}`. `202` and one queued job for each arm and model. `409` for a draft, `404` for an unknown set, `422` for a bad arm, model or repeats (or a `harness` or `oracle` arm on a model that is not docingest's, or an `oracle` arm on a set with a gold passage that has no `md_text`, see [The arms](#the-arms)), `503` for a model or an arm that is not available, `429` past twelve open jobs, `413` for a body over 32 KiB, `400` for a key written twice. |
| `GET /jobs` | signed in | Jobs, newest first. Filters `state`, `kind`, `mine`; `limit` (at most 200), `offset`. |
| `GET /jobs/{id}` | signed in | One job: `state`, `settled`, `progress {done, total}`, its times, `error`, and for a queued run its `position` and `waiting_for`. Poll it every two seconds until `settled`. |
| `GET /jobs/{id}/log?offset=` | signed in | Plain text lines with times, from a byte offset; the headers `X-Log-Next-Offset` and `X-Log-Size` say where to go on. |
| `POST /jobs/{id}/cancel` | owner or admin | A queued job is cancelled at once; a running run becomes `cancelling` and stops before its next question. `409` for a job that has ended. |
| `POST /jobs/{id}/resume` | owner or admin | Queues a `cancelled` or `interrupted` job again; a run continues after the answers it has. `409` for any other state. |
| `GET /runs` | signed in | Every `qa-run/1` file in the runs folder, whoever made it: set, arm, model, times, `answers`, `errors`, `complete`, the `md5` of the file and the job that made it. A file is listed only when its header is a valid `qa-run/1` header and its `run_id` is the name of the file (`r1.qa-run.jsonl` holds run `r1`), because that id is what the other routes and `qa-bench score` use; any other file is left out and logged once (`runs.skipped`). |
| `GET /runs/{run_id}` | signed in | One run, with the same fields (the run of `GET /runs/{run_id}/items` has them too). |
| `GET /runs/{run_id}/items` | signed in | The lines of a run with the question and the reference answer of the set. Filters `filter` (`all`, `errors`, `answered`), `id`; `offset`, `limit`, `contexts=false` to leave the chunks out. A line of a run file dropped in by hand that does not follow `qa-run/1` (a wrong type in `answer`, `citations`, `timings`, `contexts` or `error`) is left out of the page and the total, and logged once per change of the file (`runs.lines_skipped`); the run's `answers` count still counts it. |
| `POST /scores` | signed in | `{set, runs: [run_id], pairs?: [[base, variant]], noise?: [a, b], group_by?, groups?, bootstrap_engine?, number_words?}`. `202` and a job for the CPU lane that writes one `qa-score/1` file. `409` for a run that has not ended (also when it was resumed while the scoring waited for its lane), `422` for a run that does not fit the set or is not a run listed by `GET /runs`, `413` and `400` as for `POST /runs`. |
| `GET /scores` | signed in | The scores made, newest first. |
| `GET /scores/{score_id}` | signed in | The header, the `group` records (mean and 95% interval per run, group and metric) and the `compare` records (difference, interval, up and down, noise floor). |
| `GET /scores/{score_id}/items` | signed in | The `item` records: the metrics of each answer and the keys it hit. Filters `run_id`, `id`; `offset`, `limit`. |

Accounts are made, disabled, reset and given a role with the `users` command, never over HTTP (see Sign-in below).

The four circuit endpoints need a signed-in member and are described [below](#circuit-check).

No documentation pages and no schema URL are served (the stock pages load scripts from a CDN). The contract is exported instead:

```bash
uv run research-api openapi > openapi.json     # OpenAPI 3.1, keys sorted, same text every run and every hash seed
```

The export is built in fake mode, so it needs no settings file. The [web app](../../apps/web/README.md) commits it (`apps/web/src/api/openapi.json`) and generates its TypeScript client from it, and CI fails when the two drift: after a change to the API, run `uv run research-api openapi -o ../../apps/web/src/api/openapi.json` and then `pnpm gen:api` in `apps/web`.

### Errors

Every error leaves as `application/problem+json` ([RFC 9457](https://www.rfc-editor.org/rfc/rfc9457)):

```json
{"type": "urn:research-api:problem:not-found", "title": "Not Found", "status": 404,
 "detail": "No such set.", "instance": "/api/v1/sets/x"}
```

`errors` (a list of `{path, message}`, `path` a JSON pointer) appears on 422. Fields that do not apply are left out, never sent as `null`, and the published schema says so. `Retry-After` is sent on 429 and, when known, on 503. A 500 carries `error_id`, which is also in the server log together with the stack trace (a domain error with no status of its own is a 500 too); the response never holds a stack trace, and a validation problem never repeats the rejected input.

| Domain error | Status | `type` ends with |
|---|---|---|
| `BadRequestError` (also a body that is not valid JSON) | 400 | `bad-request` |
| `UnauthenticatedError` | 401 | `unauthenticated` |
| `ForbiddenError` | 403 | `forbidden` |
| `NotFoundError` | 404 | `not-found` |
| `ConflictError` | 409 | `conflict` |
| `ItemHasDecisionsError` (a `ConflictError`) | 409 | `item-has-decisions` |
| `PreconditionRequiredError` | 428 | `precondition-required` |
| `TooLargeError` | 413 | `too-large` |
| `InvalidInputError` (also a request that fails validation) | 422 | `invalid-input` |
| `RateLimitedError` | 429 | `rate-limited` |
| `UpstreamError` | 502 | `upstream-error` |
| `UnavailableError` | 503 | `unavailable` |
| anything else | 500 | `internal` |

Framework errors (unknown path, wrong method) use the same format with the status phrase as the slug, for example `method-not-allowed`. Domain errors know nothing about HTTP; `entrypoints/http/problems.py` holds the mapping.

## Sign-in

### Accounts

Accounts exist only because the person who runs the server made them, in their own terminal. There is no sign-up and no HTTP endpoint for it.

```bash
research-api users add marta --name "Marta Pérez" [--role admin|member] [--language es|en]
research-api users list
research-api users disable marta | enable marta
research-api users reset marta            # a new one-time password; ends her sessions
research-api users role marta admin
```

All of them take `--config FILE` (or use `RESEARCH_API_CONFIG`). `add` and `reset` print a random one-time password of 20 characters (no look-alike letters, `secrets`) alone on standard output and say what it is on standard error, so `password=$(research-api users add ...)` captures it. It is shown once and stored only as a hash. Give it to the person privately; the first sign-in forces a change (`must_change_password`). A username is 1 to 32 characters (`a-z`, `0-9`, `.`, `_`, `-`), stored lowercase. The last active admin cannot be disabled or made a member. Every command writes a line to the audit log with `actor: cli`; no line holds a password.

Password rule, for every password a person chooses: at least 12 characters, not the username, not one of a short list of common passwords, not one character repeated. No composition rules. `422` answers name the field (`/new`) and say why.

### Passwords

Argon2id through [pwdlib](https://frankie567.github.io/pwdlib/) (`PasswordHash.recommended()`: 64 MiB, 3 passes, 4 lanes). `verify_and_update` replaces a stored hash with a current one after a good sign-in when the cost settings change. An unknown username is checked against a dummy hash, so the answer takes as long as for a wrong password. At most four hashes run at once (each holds 64 MiB), the rest wait.

### Sessions, cookies and CSRF

A successful sign-in sets two cookies, `Path=/`, `SameSite=Strict`, `Secure` when `public_origin` is `https`:

| Cookie | Holds | Script can read it |
|---|---|---|
| `re_session` | a random 256-bit token; only its sha256 is stored, so a copy of the database is not a session | no (`HttpOnly`) |
| `re_csrf` | a random per-session token | yes, on purpose: the web app copies it into a header |

Every `POST`, `PUT`, `PATCH` and `DELETE` must carry the `re_csrf` value in `X-CSRF-Token` (compared in constant time with the one stored for the session) and an `Origin` header equal to `public_origin` (a missing or different one is a `403` before the request body is read). Reads never change state, so they need neither. Sign-in itself needs the `Origin` check, which keeps another site from signing a browser in as someone else. Behind both is `SameSite=Strict`.

A session ends after `session_idle_hours` without a request, or `session_max_days` after sign-in. A new token is issued at sign-in and at every password change. The user is read again on every request, so disabling an account, changing a role or resetting a password takes effect at the next request. The last-use time is written at most once a minute. A `401` for a request that sent a `re_session` cookie that no longer works (expired, ended, unknown, or its account disabled) also deletes both cookies, so the browser stops sending them; a `401` for a request with no cookie, and a wrong sign-in, touch no cookie. Signing in from a browser that still holds a live session ends that session first, so no unused row stays until it expires.

### Rate limit

A failed sign-in is recorded in the `login_attempts` table (the name it is counted under, and the client address), so a restart does not reset the count. The failure is taken before the password is checked and given back if the password turns out right, in one database step (`BEGIN IMMEDIATE`), so a burst of parallel requests cannot get more password checks than the limit allows: with the default limits, 40 wrong passwords sent at once for one username are checked five times and answered `401`, the other 35 get `429`. At `login_max_failures` failures for a username in the window, or four times as many for one address, the answer is `429` with `Retry-After` (seconds until enough old failures leave the window) even for the right password. Blocked attempts are not recorded, so hammering does not extend the block. A success clears that username's failures. A name that no account has is counted too, so that its answers look like those of a real name, but never under the text that was typed, which may be a password put in the wrong box: it is counted under `~` and a digest of the name made with a random key that lives in the process's memory only, so the table holds nothing that can be turned back into it (a restart forgets those counts). The account itself is never locked. A wrong current password on `POST /auth/password` counts against the same budget. The address is the peer address of the connection; `X-Forwarded-For` is ignored (`serve` starts uvicorn with `proxy_headers=False`), because nothing sits in front of the service.

### Roles and protecting a route

| | member | admin |
|---|---|---|
| The circuit check and everything the later pull requests give a member | yes | yes |
| `GET /admin/sessions`, `DELETE /admin/sessions/{id}` | no | yes |

A route takes its caller from `entrypoints/http/security.py`:

```python
from fastapi import APIRouter, Depends
from research_api.entrypoints.http.security import admin, member

router = APIRouter(dependencies=[Depends(member)])      # every route of the router needs a member
@router.get("/x")
def x(who: Authenticated = Depends(admin)): ...         # one route that needs an admin
```

`member` and `admin` answer `401` without a session, `403` when the CSRF header is missing on a write, and `403` ("Change your password first.") while `must_change_password` is true. `signed_in` is the same without that last rule; only `/auth/me`, `/auth/password` and `/auth/logout` use it. `signed_in` keeps the caller on the request, so a route class that checks the caller before it reads the body shares the session lookup with the router's dependency: one lookup per request. The contract lists the cookie as the `sessionCookie` security scheme.

### Host and Origin checks

The `Host` header must name this service: `localhost`, a loopback address, the host of `public_origin` or the `bind_host` address (fake mode: loopback names only). Anything else is a `403` problem, whatever the path. This closes the DNS-rebinding path: a web page on a name the attacker controls, pointed at this machine's address, reaches the service with `Host: that-name` and the browser would let that page read the answers. A request with no `Host` or two of them is refused too. The check is in `entrypoints/http/hosts.py`; the `Origin` check described above is in `origin.py`. In live mode a person must open the app at `public_origin` (set it to the name they type, for example the MagicDNS name and port); the bare Tailscale address passes the `Host` check but its `Origin` does not match, so sign-in is refused with a `403`. Fake mode accepts any loopback `Origin`.

### Files, database and migrations

| What | Rule |
|---|---|
| `data_dir`, the folder of `db_path`, `log_dir` | made with mode `0700` if missing. If one exists and any group or other permission is set, or it belongs to another account, the service (and every `users` and `db` command) refuses with a message that names the `chmod` to run. It never changes an existing folder by itself. |
| The database file and its `-wal` and `-shm` files | made with mode `0600` by `db upgrade`; refused when open to others. |
| The settings file | refused by `serve`, `db`, `users` and the factory when any group or other permission is set or it belongs to another account, with the `chmod 600` to run. It holds no secret, but whoever can edit it can move `public_origin`, and with it the `Origin` check. |
| `api.log` | made `0600`; a log kept from an earlier run is checked like the database and refused when open to others. |

The folders matter because the disk of the server is shared and its parent folder is open to everyone.

SQLite runs in WAL mode with foreign keys on for every connection and a 5 s wait on a busy file. Tables: `users`, `sessions`, `login_attempts` (migration `0001`), `artifacts`, the catalog of question-set versions (migration `0002`), and `jobs` (migration `0003`). Migration `0004` drops the `path` column of `artifacts`, which nothing read: a file is found from the name and version. The tables are made by the Alembic migrations (`adapters/persistence/migrations/`, batch mode for SQLite). `research-api db upgrade` creates the file and applies migrations; `serve` and `users` refuse to start on a database that is behind the code and say to run it. `research-api db status` prints the revision and exits 1 when behind. A change to a table needs a model change and a migration; `tests/test_persistence.py` fails when the two disagree. Fake mode builds its throwaway database straight from the models, and the same test checks that this gives the same tables as the migrations.

### Logging

JSON lines, to `<log_dir>/api.log` (`0600`, rotated at 10 MiB, five kept) or standard error. Each request: time, request id (also sent back as `X-Request-Id`), username when signed in, method, route template (`/admin/sessions/{session_id}`, never the path as sent), status, duration. Audit events: `login.success`, `login.failure`, `login.blocked` (the username of the account, or `(unknown)`, and the address), `logout`, `password.changed`, `password.refused`, `session.ended`, and the `users` commands. Only a fixed list of fields is ever copied into a line, and no body, query string, header or cookie is read, so a password, token, question or answer cannot get in. A name that no account has is logged as `(unknown)`, never as typed, in case it was a password typed in the wrong box. uvicorn's own access log stays off.

## Circuit check

Four endpoints read a circuit drawn in LaTeX, simulate a circuit and compare it with a state, a unitary or another circuit. They use docingest's `gate_list` and [quantum-sim](../quantum-sim/README.md) as libraries, in the same process, on the CPU. They need no model server and no GPU, so fake mode runs the real engine.

| Method and path | Body | Does |
|---|---|---|
| `POST /circuits/parse` | `{"latex": "..."}` | docingest reads the code of one block (quantikz, Qcircuit, yquant, tikz-cd). Returns `status` (`parsed`, `partial` or `unparsed`), `text` (the gate list in words) and `circuits` (each one a `circuit/1` document, `CircuitSteps.to_dict()`). |
| `POST /circuits/run` | `{"circuit_json": "...", "input"?, "values"?, "unitary"?}` | The state and the outcome probabilities, and the unitary when `unitary` is true. `input` is a basis label (`"010"`, wire 1 first) or a list of amplitudes. `values` gives each symbol a number or an angle written as text, such as `\pi/2` (or `pi/2`: quantum-sim's `parse_value`, the rule of `docingest circuits export --set` too); a key is one symbol (`theta`, `\theta`, `\theta_{1}`), and any other key (`thetta`, `pi`) is refused. |
| `POST /circuits/check` | `{"circuit_json": "...", "target"?, "input"?, "values"?}` | Equal up to global phase? `target` is exactly one of `{"state": ...}`, `{"unitary": [[...]]}` or `{"circuit_json": "..."}` (a second circuit). With no `target` (leave it out), the two sides of an equality drawn in the circuit are compared; `"target": null` is a `422`, as in quantum-sim. |
| `GET /circuits/conventions` | none | What `quantum-sim conventions` prints: the conventions, the gate table and quantum-sim's own limits. |

**The circuit is text.** `circuit_json` holds the JSON of a `circuit/1` document as a string, not as an object. quantum-sim refuses a key written twice (`"controls"` twice in one item), because the last value would win and the document does not say which is meant. A JSON body parsed by FastAPI would keep the last value without a word, so the API reads the text itself with a parser that refuses repeats. The same check covers the request body (`"unitary": true, "unitary": false`) and the second circuit of a check (`target.circuit_json`). A repeated key, JSON that does not parse, `NaN` and `Infinity` are a `400` that names the input and the key and never repeats the text. The web app sends `JSON.stringify(circuit)` of a circuit that `/circuits/parse` returned, or the text a person pasted.

**The answer is the simulator's.** `run` and `check` return the `quantum-sim-result/1` document as it is (`status`, `conventions`, `input`, `circuits`, `comparisons`, `equal_to_target`, `notes`, `refused`); the API does not re-model it. A circuit the simulator refuses (an unknown gate label, a postselection, a bundle wire, an unparsed part) is a `200` with `"status": "refused"` and the reasons, like the command's exit status 1. A check that finds the circuits different is a `200` with `"equal_to_target": false`. The OpenAPI type of these bodies is a plain object.

**Caps.** The endpoints are synchronous and meant for small inputs. Beyond the caps the answer is a `422` whose `detail` points to the `quantum-sim` command, and `errors` holds the JSON pointer of the circuit field (`/circuit_json`, `/target/circuit_json`). quantum-sim keeps its own, larger limits inside (20 wires, 10,000 operations, 32 Mi characters).

| Cap | Value | Answer | Why |
|---|---|---|---|
| Request body | `max_body_bytes`, 1 MiB | `413`, decided from `Content-Length` or while the body streams in, before it is held in memory | memory |
| Wires for a simulation or a check against a state | 12 | `422` | a state is 2^n amplitudes |
| Wires for anything that builds a unitary: `unitary: true`, a check against a unitary, against a second circuit, or a drawn equality | 8 | `422` | a unitary is 4^n entries and each operation costs 4^n. Measured on 2,000 operations: 12 wires of state take 0.1 s, an exact comparison of 8 wires 0.3 s and of 10 wires 41 s |
| Operations in the circuits of one request | `max_ops`, 2,000 | `422` | time. A gate over several targets counts once for each, barriers and text count none, so the count errs high |
| Time | `timeout_s`, 30 | `422` | a backstop for the caps |
| Jobs at once | 2 | `503` with `Retry-After: 5` | the CPU of a shared machine |

The three settings are in the `[circuit_caps]` table of the settings file ([`config.example.toml`](config.example.toml)). `max_body_bytes` goes up to 32 MiB, `max_ops` up to 10,000 and `timeout_s` up to 600, which are quantum-sim's limits and ten minutes. The wire caps and the number of jobs are constants of the code.

**Time limit, honestly.** A Python thread cannot be stopped. The work runs in a daemon thread; when it outlives `timeout_s` the request is released with the `422` and the thread finishes on its own, still holding one of the two slots. So a slow circuit cannot pile up work without bound (the caps keep each job short, the slots keep them few), and a stopping server does not wait for them. If this ever matters, the next step is a worker process that can be killed.

**Sign-in.** The four endpoints need a signed-in member (`Depends(member)` on the router): no session is a `401`, a pending password change is a `403`, and a write without the CSRF header is a `403` (see [Sign-in](#sign-in)). The check comes before the body is read, so a caller with no session gets the `401` whatever it sends and costs no body read. They log no request body and no circuit text.

## Asking and reading papers

An ask goes through four steps, all in the API process.

1. `POST /asks` checks the caller (member, CSRF, `Origin`), the engine (`503` while it warms up) and the limits (three open asks per member, sixty in all), makes a random ask id and puts the question in the **GPU lane**. The question is in the body only: it is never in a URL or a log.
2. The lane runs one job at a time, so the model servers see one question at a time, as a person at the command line would give them (a model server that batches requests changes the numbers). A job of a higher priority goes before a waiting one of a lower priority; asks have the highest, benchmark runs will join with a lower one. `position` counts the jobs ahead, the running one included.
3. The engine answers. In live mode it is docingest's `AskSession`: the index and the corpus listing stay open between questions and reload when an index build commits. The session is opened in a worker thread at start-up (`warming_up` in `/status`) and one search warms it. Warming up has a limit of 90 seconds: an embedder that never answers leaves the engine `unavailable` ("did not finish starting in time") instead of holding every ask at a `503`, and the next question that works makes it `ready` again. The answer text is split the way a `qa-run/1` item holds it: `answer` without the References list, `references_text`, and `citations` (`{key, doc_id, first_page, last_page}`, the doc id completed from the sources). The same reader works on the fake engine's text.
4. The ask is kept in memory for 30 minutes after it ends, then forgotten (by the system clock, in fake mode too; a test can give the container its own clock). Nothing is written to disk.

`GET /asks/{ask_id}/events` sends each event with an `id` (0, 1, 2, ...), so a browser that reconnects resumes with `Last-Event-ID`:

| Event | Data | When |
|---|---|---|
| `queued` | `{ask_id, position}` | at once, and again whenever the position changes (0 is next) |
| `retrieving` | `{ask_id}` | the search and the reranking start (not sent with `use_index: false`) |
| `answering` | `{ask_id}` | the chunks are ready and the answer model is called |
| `answer` | `AskResponse` | the end: `{ask_id, answer, references_text, citations, contexts, sources, warnings, index_generation, timings}` |
| `error` | `{ask_id, kind, status, message, retry_after_s?}` | the end: `status` is the HTTP status the failure would have had (503 try again, 502 a model server refused, 422 the question cannot be answered, 500 a surprise with an error id) |
| `cancelled` | `{ask_id}` | the end |

A comment line goes out every 15 seconds, so a proxy does not close a quiet connection. A reader that comes back after the last event gets `204`, which tells `EventSource` to stop reconnecting. The stream is a plain `GET`: `EventSource` sends the session cookie, and nothing changes, so no CSRF header is needed. Another member's ask is a `404`, an admin's included: questions are private. A reader that hangs up does not stop the question; the answer is there when it comes back (`GET /asks/{ask_id}`). A stream is authorised when it connects, so the session is checked again before the `answer` is sent: a browser that signed out, or whose session ended, while the question was being answered gets no answer and its stream just ends.

`GET /asks/{ask_id}` answers with an envelope, `{ask_id, state, position, phase, response, error}`, not the bare `AskResponse` the scope names: a client that reads it before the end needs to know the state. `response` is the `AskResponse` once the ask is done, and `error` is the `AskErrorOut` of a failed one (an `AskResponse` has no error field). `contexts[].score` is a field of the `qa-run/1` context that docingest's chunks do not fill yet, so it is always `null`.

What a member is told when something fails is written in `adapters/docingest_ask/errors.py`: docingest's own errors and a few families of library errors (matched by class name, so litellm is not imported) become a sentence and, when it helps, a `Retry-After`. The message of a library exception is never passed on, because it can hold the prompt. A surprise becomes `500` with an error id; the log line has the id, the exception type and the file and line where it was raised, never the message. `/status` and the log never hold a question or an answer, and the log holds an ask only as a short hash of its id.

Answers and papers carry `Cache-Control: no-store` (one middleware for `/asks` and `/documents`).

### The papers a citation names

A citation prints the first 16 characters of a document id and a page range. `GET /documents/{id}/pages/{n}` returns that page as Markdown with its LaTeX math, so the web app can show the paper at the cited page. The reader lists the corpus through docingest's store (the listing is kept for 30 seconds, because it reads every manifest) and splits a paper's `document.md` at its page markers (the last 8 papers read are kept split). The page count is the manifest's everywhere (the listing, the detail and a page), and a page the manifest does not list is a `404`. A papers folder that cannot be read is a `503` with `Retry-After`; a paper whose text file is gone is a `404`. An id is checked against `^[0-9a-f]{16,64}$` and then looked up among the papers of the corpus, never turned into a path. For a LaTeX source `kind` is `section` and `title` is the section title.

### Fake mode

`serve --fake` answers from six invented papers (titles end in "(synthetic)"; nothing in them comes from a real paper) with canned answers in English and Spanish that cite real pages of those papers, so the web app can be shown without a GPU: ask about T1 or transmons, the surface code, the teleportation circuit, Grover search, VQE and barren plateaus, or cross-resonance gates. The answers go through the same reader as the real ones, so the citations, the References list, the contexts and the page links behave the same. A question about anything else gets a polite "not in this corpus" answer with no sources. One answer carries a warning, to show that screen. The engine pauses between phases (`--ask-pause`, only with `--fake`, default 0.6 s; `RESEARCH_API_FAKE_ASK_PAUSE` for the factory) so a screen shows them. A question that contains `simulate-unavailable`, `simulate-upstream` or `simulate-crash` fails on purpose, for the error screens.

## Question sets

A question set is a [`qset/1`](../qa-bench/README.md) file; its reviews are a `reviews/1` file. The API reads, checks and writes both with qa-bench, so there is one description of an item: the format's JSON Schema. An item travels as a JSON object and is never copied into a second model.

### What is stored where

| What | Where | Rule |
|---|---|---|
| The set | `<data_dir>/sets/<name>/<version>.qset.jsonl` | Mode `0600` in a `0700` folder. Replaced all at once (written beside it, flushed, renamed over it, the folder flushed), so a reader never sees half of a file. |
| The decisions | `<data_dir>/sets/<name>/<version>.reviews.jsonl` | `reviews/1`, append only; its header names `<name>/<version>`, as the format wants. |
| The draft before it was frozen | `<data_dir>/sets/<name>/<version>.prefreeze.qset.jsonl` | Kept when a version is frozen, so an item that a reviewer rejected is never lost for good. It is also how an interrupted freeze is undone (below). |
| Owner, status, md5, item count, corpus, dates | the `artifacts` table | A catalog. For a draft the file is the truth: if a crash left the two apart, the file wins and the next write repairs the row. A frozen version is the other way round: its md5 is its identity, so a file that no longer has it is a `500` (logged) and the row is never rewritten. |

The header's `set` field is `<name>/<version>` (for example `demo-papers/v2`), which is what runs and scores repeat. A name is letters, digits, `.`, `_` and `-`, starting with a letter or digit (64 at most); a version is `v1`, `v2`... Names are compared ignoring capitals when a set is made: `Demo` cannot be created beside `demo` (`409`), because on a case-insensitive disk (macOS, Windows) both are one folder and the second would write over the first. Reading is by the spelling that exists. A name that is a path never reaches the file system.

### Drafts, versions and freezing

A version is a **draft** until it is **frozen**. Its identity is the md5 of its file, and that md5 is also the draft's ETag. Only the owner or an admin changes a draft; any member may review any item of a draft. Nothing changes a frozen version: its items, its reviews and its md5 are fixed, and a run of the benchmark can name it by that md5.

- `POST /sets` makes an empty draft under a new name, or, with `copy_from`, the next version of a set as a copy of one of its versions (the way to continue from a frozen one).
- `POST /sets/import` makes a draft from a file. A `qset/1` file whose header already names the target is stored byte for byte, so its md5 is the one it came with; if the name or version differs the header's `set` is rewritten and the rest is kept. An older shape (`mathcirc-v1`, `circuit-tasks-v1`, `circuit-claims-v1`, `qubitqa-pilot`, `qasper`) is converted by qa-bench and its header records the source file and md5. A file with any problem is refused whole, with every problem (up to 100) as `/lines/<n>/<field>`.
- Editing: `PUT`, `POST` and `DELETE` on items need `If-Match` with the `etag` the client read. Without it (or with `*`, which does not say which version was read) the answer is `428`; with a stale one `409` ("changed since you opened it") and nothing is written. The header may list several tags (RFC 9110, `"old", "current"`): the write goes through when any listed tag is the current one, and what is stored is always the current md5, never one of the others; a weak tag (`W/"..."`) never matches a write. This is on purpose: a client that lists tags it once read wants the write if the draft is still any of them, and the answer to a stale list is the same `409`. Every write rebuilds the whole file through qa-bench, so what is stored is always a valid `qset/1` file (the ids are unique and every `mirror_of` names an item). All writes of this process take one lock.
- `POST .../freeze` applies the latest human decision of each item, as `reviews/1` says: an accepted or edited item stays (an `edit` replaces the fields it names), a rejected item goes, an item nobody decided stays as it is. The frozen file gets a new header time and md5 and refuses everything from then on. A version with no item left cannot be frozen. `GET .../freeze` shows the same plan first, by item id, with an `etag` that names the draft and exactly these decisions; the freeze must send it back in `If-Match`, so a decision added after the plan was read is a `409` and is never applied unseen. The plan builds the frozen file as the freeze does, so it refuses the same things (for example a mirror whose original was rejected) and names the item by id.
- A freeze writes the frozen file and then saves the row. If the second step fails the draft is put back; if the process stops between the two, the next load sees a draft row that matches the `prefreeze` copy and serves (and, on a write, restores) that draft. The version is never a half-frozen draft.

### Reviews

A decision is `accept`, `edit` or `reject` on one item (`reviews/1` also has decisions on a model's answer; those belong to the benchmark runs and are not served here). `edit` and `reject` need a `note`; an `edit` lists the fields to change and is applied at the freeze (it does not change the draft before). The line in the file is made by the server: the reviewer is the signed-in user, the role is always `human`, the date is the server's, and `set_md5` is the md5 of the draft that the reviewer read: the request must carry it in `If-Match`, and a draft that has changed since is a `409` and nothing is stored, so a decision is never recorded against a text its author did not see (a reviewer who opened an item, whose text the owner then replaced, reads it again before deciding). A change to any item changes the draft's etag (it covers the whole draft, not one item), so any owner edit to any item gives every open reviewer a `409` on their next decision, and every decision made before that edit shows up as `stale` in the freeze plan: it still applies, because the text of an item with decisions did not change (see below); `stale` only says that the draft moved on after the decision. A client answers that `409` by reading the item again and asking again. An `edit` that would leave the item invalid is refused when it is made, so it cannot stop a freeze later.

**An item with decisions keeps its text.** An accept is about the text the reviewer read. If the owner could replace that text, an accept given before the change would count towards the two people that make an item gold, and nobody would have read what is now there. So once an item has any decision, replacing it with a different text is a `409` of type `item-has-decisions` (the same text is fine), and the change is proposed with an `edit` decision instead, which is applied at the freeze. An item can still be deleted, and its decisions are then listed as `unknown` in the plan; its id cannot be used again in that version. A new version (`copy_from`) starts with no decisions.

Two consequences. An edited text can never collect two accepts in the same version: an accept given after an `edit` decision is an accept of the text in the draft, and the freeze applies the edit over it, so the edit replaces what was accepted; to have people accept the edited text, freeze and start the next version. And the frozen file does not tell gold from the rest: an item with one accept, an unreviewed item and an edited item are all kept, and `reviewer` on an item is the person of the decision that was applied, so it does not mean gold. Read the plan (`accepted`, `needs_second`, `unreviewed`) before freezing, and do not take `reviewer` as proof of gold.

The file of decisions is append only. A last line without its line feed is an append that stopped half way and is not a decision; the next review rewrites the file without it.

Per item, the review state is computed, not stored: `none`, `needs_second` (accepted by one person), `accepted` (the latest decision is an accept and two different people have accept as their own latest word: gold), `edited`, `rejected`. The latest decision by date holds, and on equal dates the line written last, as in the format.

### The demo sets of fake mode

`serve --fake` and the OpenAPI export start with three made-up sets, built through the same use cases as real ones (`bootstrap/demo_sets.py`; every paper, question and name in them is invented):

| Set | State | Shows |
|---|---|---|
| `demo-papers` `v1` | frozen, owner `ana` | Six items in English and Spanish (a mirror pair, a two-paper chain, an unanswerable question, a near duplicate); one item was rejected before the freeze. |
| `demo-papers` `v2` | draft, owner `beto` | A copy of `v1` plus a new item, with reviews in each state: `accepted`, `needs_second`, `edited`, `rejected` and `none`. |
| `demo-circuits` `v1` | draft, owner `ana` | A circuit question to verify, one to create and one claim read from a paper. |

### Limits

An import is at most 20 MiB (the unzipped size for a gzip file: zeros shrink a thousand to one, so the upload size says nothing; the unzipping is linear in the upload and a file of more than 1000 gzip members is a `422`, since empty members of 20 bytes would otherwise make a 20 MiB upload cost minutes of the single process), an item body 1 MiB and a review 256 KiB; a body over its limit is a `413` before it is read in full. The cap follows the route, not the end of the path, so an item called `import` gets the item cap. The caller is checked first: a request with no session is a `401` whatever its body, and costs no body read. A JSON body is read as text and a key written twice is a `400`, as qa-bench refuses it in a file. An item id is at most 200 characters, since the routes cannot name a longer one. The service runs one process, so the lock above is enough; a second process on the same data folder would need a database transaction around each write. A catalog listing reads each version's file for its review count, which is cheap for the sets of hundreds of items this is made for.

## Benchmark jobs

A benchmark job is one run of one arm on one model over one frozen question set, or one scoring of runs. The files are the [qa-bench](../qa-bench/README.md) formats: a run is a `qa-run/1` file, a scoring a `qa-score/1` file, and a person who drops a run file made elsewhere into the runs folder sees it in `GET /runs` like any other.

### The arms

| Arm | Asks | Needs |
|---|---|---|
| `bare` | The model alone, no papers, with the closed-book prompt of R-281 word for word (its md5 `9ebdae30...` is in the header, so a run made here compares with that one). Temperature 0, 1024 tokens. | A model of `[[models]]` whose server answers. |
| `harness` | The same engine as a person's question, so the run sees what an ask sees. `overrides` set `use_index`, `bm25` and `whole_blocks` for every question. | The answer engine ready (`docingest_config`), and a request on docingest's model (see below). |
| `oracle` | The same engine, handed the gold passages of the question instead of retrieved chunks: no index, no embedder, no reranker (`AskEngine.answer_from`). | The answer engine ready, a request on docingest's model, and a `md_text` on every gold passage of the set (see below). |

**The model of a `harness` or `oracle` run is docingest's, not the one asked for.** These arms answer through docingest, which writes its answers with the model its settings name: `DOCINGEST_LLM`, else `[qa] llm`, else the pinned `[ocr]` model (docingest's `Container.answer_model`). A request names a model too (`models`), and the two must be the same one, or two runs would carry different names for one model, or one name for different models. So the service compares two strings:

* the requested name, which is a `[[models]]` `name` (what the model server serves it as, for example `qwen-local`);
* the **last `/` part of docingest's model name**: for a litellm string such as `openai/qwen-local` that is `qwen-local` (the part after the provider); for the pinned model, whose name is its Hugging Face repository `Owner/Repo`, it is `Repo`.

They are compared as written, character for character, with no case folding and no other normalising. A `harness` or `oracle` arm on any other model is a `422` with the problem detail at `/arms/{i}` (the arm that cannot run there), whose message names the right model. The `bare` arm asks the model of the request and is not checked. The check reads docingest's settings only and asks no server, so the arms are checked before any job is made. A service whose docingest settings cannot name the model (no `docingest_config`, or `[adapters] qa` is not `paperqa`) answers `503` for these arms.

The run header records what docingest uses: `model.id` is that served name (the requested name when they agree, which they must), `model.revision` is docingest's commit of the pinned model, and for a litellm model (which docingest does not pin) the `revision` of the `[[models]]` entry of that name, if it has one; `model.endpoint` is that entry's label. The model is read when the run starts, and it must still be the one the request named: a service restarted on another docingest model before the run starts ends the job `failed` with a `conflict` ("This run asked for X, and docingest now answers with Y. Start a new run.") and no file. A resume does the same check against the header: the run goes on only when docingest answers with the `model.id` it started on (and the same `model.revision`, when both sides have one); otherwise the job ends `failed` with a `conflict` ("This run was started on X, and docingest now answers with Y. Start a new run.") and the file gets no line from the other model, so one header never covers two models.

**The oracle arm.** The contexts of an `oracle` answer are exactly the `md_text` of the gold passages of the question, in set order: the rule `qa-bench` checks an oracle run against its set with, so the file passes `check_against_set` (an error line has no answer and no contexts, and the check skips it). docingest adds nothing to them (no whole blocks, no gate lists, no equation lookup), and the `harness` arm gets those, so the pair `harness:oracle` also measures what they add.

* **A set where a gold passage has no `md_text` is refused.** Every set but the math and circuit one has passages without it (a quote, a page, a block number), and a question skipped or written as an error line would count against the oracle in every comparison. So `POST /runs` answers `422` at `/arms/{i}` for the `oracle` arm, with the number of such questions and the first five ids in the message, and the same text in `detail`. No job is made, for any arm of the request. The check is the same in fake mode and live.
* **A question with no gold paper at all is allowed.** It is unanswerable by design (the demo set's `DP-003`). The oracle asks the engine with an empty list of passages, and the item is written with `contexts: []`, which is what the `qa-bench` rule asks (the contexts equal the gold texts, and there are none), so `check_against_set` accepts it. docingest does not make an answer up: PaperQA2, given nothing to read, answers with its own text, `I cannot answer this question due to having no papers.`, and sends no request to the model (so the item has almost no `answer_s`). The fake engine answers the same words.
* **The model reads the passages as they are only with `[qa] skip_summary = true`** in docingest's settings (the example `lab-server.toml` has it). Without it PaperQA2 summarizes each passage and the model reads the summaries, while the contexts written to the file are still the `md_text`, so the file looks the same.
* **A passage docingest cannot place is still answered.** docingest looks for each passage in its paper, to cite pages. One it cannot find is cited with all the pages of its paper, and the item says so: `ext.warnings` holds docingest's warning, which starts `a given passage was not found in <16 hex id>`. A reader of such an item knows its citation pages are the whole paper, not the passage. The item has no `ext.warnings` when every passage was placed.
* **A cut of the sources is recorded** as `usage.truncated: true` on the item. docingest's answer model reads at most `[qa] answer_max_sources` sources (3 when the setting is absent, 10 in the example `lab-server.toml`), and it drops the rest without a warning, so the service compares the number of distinct passages with that limit; it also records a cut when the prompt did not fit the model and docingest asked again with fewer sources. A question of the math and circuit set has at most two passages. `false` means no cut was seen. The count is exact with `[qa] skip_summary = true`, where PaperQA2 drops repeated texts and keeps the first `answer_max_sources`; without it PaperQA2 summarizes every passage and cuts by summary score, so the flag can miss a cut or report one near the limit. Items of the other arms have no `usage.truncated`.
* **`timings.retrieve_s` is not a retrieval time.** On this path it is the time docingest took to list the corpus and place the passages, and it lists the corpus again for every question, so it is not comparable with the `retrieve_s` of a `harness` item. Each oracle item says so in `ext.timings_note`.
* **A gold paper that is not in the corpus** makes that question an item with an `error` of kind `other` (the message names the paper by its first 16 characters) and the run goes on. An unreadable paper is an unexpected error: the item says `Unexpected error <id>`, the log has the id, and the run goes on.

### The lanes

Runs go through the same lane as the asks. A run is one job of the scheduler and its questions are tickets of the lane, one at a time, with a lower priority than an ask:

* two runs never share the GPU: the second stays `queued` (with its `position`) until the first ends, first in first out;
* an ask that arrives while a run goes on waits for the question in flight (an LLM request is never cut), then goes before the next question of the run. The run header's `notes` say how many it let through (`Interleaved asks: 2.`), because that run shared the GPU;
* a scoring goes through the CPU lane, in a worker thread, at the same time as a run;
* before a run starts, its model server must answer (or the engine be ready): else it waits `queued` and says why in `waiting_for`, and starts when the server is back.

### States

```text
queued -> running -> done | failed | interrupted
queued -> cancelled
running -> cancelling -> cancelled | done | failed
cancelled | interrupted -> queued                  (resume)
```

The desk moves a job only through this table (`move_allowed` in `domain/jobs.py`): a pair it does not allow raises instead of being stored, and a test lists every move the desk makes. A run whose cancel is pending can still end `failed` (the disk is full while the last answer is written): it did not stop because it was asked to.

`interrupted` is a run that stopped without being told to: the model server stopped answering (`error.kind` `unavailable`), or the service stopped while it ran (found at the next start, or at a clean shutdown). A job found `running` at start-up becomes `interrupted`; one found `cancelling` becomes `cancelled`; a `queued` one runs. A cancel that arrives while the last question is in flight ends the run `done`, because nothing is missing.

### The run file

The header is written when the run starts and the answers are appended one line at a time, flushed, so a stop leaves whole lines and at most one half line. A resume of a `harness` or `oracle` run first checks that docingest still answers with the model in the header (see [The arms](#the-arms)). It cuts the half line off, reads which `(question, repeat)` pairs the file has, and asks the first missing one: the questions go through the set once for each repeat, so a run that stops early has complete earlier repeats, and a resumed run ends with every pair exactly once. The header gets `ended` only when the run is complete; a cancelled, interrupted or stopped run has no `ended` and its `notes` end with `Partial: Cancelled at <time> with 3 of 6 answers.` (`Stopped` for a run found `running` at start-up, `Interrupted` for a model server that went away). The `notes` are the member's words followed by the service's own sentences, always last: `<words> Interleaved asks: 3. Partial: ... .` Only that ending is read back (the count, to add to it on a resume), so a member who writes `Interleaved asks: 7.` or `Partial: ...` in the words changes nothing and loses nothing: the words are kept exactly as written. A question that fails (the model refuses it, or takes too long) is recorded as an item with an `error` and the run goes on; a model server that goes away interrupts the run instead. Messages in the file and the log are fixed text: a library's own message can hold the prompt. The log of a job (`jobs/<id>.log`) never holds a question or an answer.

The `md5` of a run in `GET /runs` is the md5 of the file's bytes as the reader got them (unzipped when the file is gzip, a half line still being written included): the value `ScoreRunOut.md5` records when the run is scored. Both come from the same function (`qa_bench.formats.jsonl.md5_hex`, called by the run reader and by the scorer's loader), so there is one definition. A score is of this version of the run when the two are equal, and any other value means the file changed after the score was made (a resume, a file replaced by hand). The list already reads and unzips each file to make its summary, so the md5 costs one more pass over the bytes in memory (about 6 ms for 5 MB, no second read) and is kept with the summary, which is made again only when the file's stamp changes: modification time, size, inode and change time. The inode is new when a file is replaced, and the change time moves when the modification time is set back by hand (`cp -p`, `touch -r`), so a replaced file or a copy that keeps the time is seen; only an in-place rewrite of the same size inside one clock tick of the last write is not. The field is optional in the contract, but this service always fills it, also for a file dropped in by hand.

```text
<data_dir>/runs/<run_id>.qa-run.jsonl        run_id: <arm>-<set>-<version>-<tail of the job id>
<data_dir>/scores/<score_id>.qa-score.jsonl  score_id: the id of its job
<data_dir>/jobs/<job_id>.log
```

The job table (`jobs`, migration `0003`) holds who started each job, its state, its parameters as JSON and its progress; the files hold the results.

### Scoring

`POST /scores` names a set version, the runs to score (at most twelve, all on that version, none still running), the pairs to compare and optionally a noise pair. The job runs `qa_bench.score` and writes the file it returns, so a score made here equals the one `qa-bench score` makes from the same files (a test compares the bytes). A run that does not fit the set (another set, another md5) fails the job with qa-bench's own words. A scoring that a clean stop interrupts leaves no score file: its worker thread checks that the job is still the running one before it writes (a stop that lands in the instant between that check and the write can still leave a complete file for an `interrupted` job; a resume writes it again). The recovery at start-up (`JobRepo.recover`) writes `interrupted` and `cancelled` itself, outside the state machine's table; `test_jobs_repo.py` pins that both moves are in the table.

### Fake mode

`serve --fake` has the three arms and two models: `fake-model`, which answers, and `fake-offline`, which is down (so the unavailable state can be seen). The answers are made up from the key facts of each question and are deterministic: each key fact has a fixed hardness and an arm finds the facts below its level (plus a little noise that changes with the repeat), so the closed-book arm finds fewest, the harness more and the oracle most, a second repeat may differ, and a score over the three has something to show. The harness cites the gold papers and the oracle's contexts are the gold passages, as `qa-bench` checks. Each answer takes `--bench-pause` seconds (only with `--fake`, default 0.25; `RESEARCH_API_FAKE_BENCH_PAUSE` for the factory), so a run of the six demo questions takes a couple of seconds. A question that contains `simulate-unavailable`, `simulate-upstream` or `simulate-crash` fails on purpose.

### Limits

Twelve jobs open (queued, running or being cancelled) per member (`429` with `Retry-After`), one to three arms and one to four models in a request, repeats 1 to 10, notes 1000 characters, twelve runs and twelve pairs in a scoring. A request body is at most 32 KiB (`413`); a model name is at most 128 characters, a run id has the pattern of a run id, a scoring has at most 8 `group_by` fields of 200 characters and 32 `groups` of 400.

## Layout

```text
src/research_api/
  domain/          errors, ServerStatus, auth (User, Session, the password and session rules),
                   ask (the question, the answer, the reader of a formatted answer, the life of one
                   ask), documents (papers, pages, the short-id rule), the circuit rules (strict
                   JSON, caps), sets (names, versions, who may edit, review states, filters), jobs (the state
                   machine, ids, the order of the questions) and runs (the arms' answers, the
                   closed-book prompt, the lines of a run file).
                   Standard library only.
  ports/           Protocols (Clock, CircuitEngine; UserRepo, SessionRepo, LoginAttempts,
                   PasswordHasher; AskEngine; DocumentReader; SetCatalog, SetFiles, SetFormats; JobRepo,
                   BenchFiles, RunFormats, Scorer, Arms; ModelServer) and the values that cross them
  application/     use cases (ReportStatus; SignIn, SignOut, Authenticate, ChangePassword,
                   ListSessions, EndSession, ManageUsers; AskDesk and the GpuLane; ListDocuments,
                   GetDocument, ReadPage; ParseCircuit, RunCircuit, CheckCircuit,
                   ShowCircuitConventions; ManageSets, ReviewItems, FreezeSets; JobDesk, LiveArms,
                   RunsDesk, ScoresDesk)
  adapters/        clock, fake (fake mode: clock, the invented corpus, the fake engine and reader),
                   persistence (SQLAlchemy, Alembic; users, sessions, login attempts, the set
                   catalog, the jobs), hashing (Argon2id), files (private folders, the set, run,
                   score and log files), circuits
                   (docingest gate lists and quantum-sim, with the thread that bounds the work),
                   docingest_ask (the engine on AskSession, the paper reader, the wording of
                   failures, the stand-ins for a service without a docingest file), bench (qa-bench:
                   sets, run files, the scorer), models (OpenAI-compatible servers, httpx)
  bootstrap/       settings.py (the TOML loader and its rules), storage.py (private folders, the
                   database), seed.py (fake-mode accounts), demo_sets.py (fake-mode sets),
                   container.py (wiring, fake mode)
  entrypoints/     http/ (FastAPI app, routes, auth routes, circuit routes, ask and document routes,
                   set routes and schemas, benchmark routes and schemas, security dependencies, Host and Origin checks, access log,
                   no-store, schemas, problem details), cli.py, logs.py
```

The six layers import downward only: `entrypoints`, `bootstrap`, `adapters`, `application`, `ports`, `domain`. Only `bootstrap` and `entrypoints` import adapters. Use cases take their ports as keyword-only constructor arguments, as in docingest.

## Import contracts

Seven rules, enforced by import-linter (`uv run lint-imports`, a gate in `scripts/check.sh` and in the Linux CI lane). Rule 5 is written as six `forbidden` contracts, one per library (or pair of libraries). Each one covers the whole package (`source_modules = ["research_api"]`) and lists as exceptions only the adapters that own the library, so the package root, `adapters/__init__.py` and any adapter added later are checked too.

| # | Rule | Type |
|---|---|---|
| 1 | The six layers above, outer to inner | `layers` |
| 2 | The nine adapter areas (`clock`, `fake`, `persistence`, `docingest_ask`, `circuits`, `bench`, `hashing`, `files`, `models`) do not import each other (so `bootstrap/storage.py`, not an adapter, joins `files` and `persistence`); a test fails when a folder or module under `adapters/` is missing from the list | `independence` |
| 3 | `domain` imports no `fastapi`, `starlette`, `sqlalchemy`, `alembic`, `pwdlib`, `argon2`, `httpx`, `uvicorn`, `docingest`, `docingest_index`, `quantum_sim`, `qa_bench` or `jsonschema` | `forbidden` |
| 4 | `application` and `ports` import none of those: they reach docingest, quantum-sim and qa-bench only through ports | `forbidden` |
| 5 | Only `adapters.docingest_ask` and `adapters.circuits` import `docingest`; only `adapters.circuits` imports `quantum_sim`; only `adapters.bench` imports `qa_bench`; only `adapters.models` imports `httpx`; only `adapters.hashing` imports `pwdlib` and `argon2`; only `adapters.persistence` imports `sqlalchemy` and `alembic` | `forbidden` (six) |
| 6 | Only `bootstrap` and `entrypoints` import `adapters` | `protected` |
| 7 | `research_api` never imports `docingest_index` (docingest finds its plugin by entry point) | `forbidden` |

The reverse direction is a contract in each of the other packages: docingest, docingest-index and quantum-sim never import `research_api` or `qa_bench`.

Rules 3 to 5 hold for every library the package imports: docingest and quantum_sim only in `adapters.circuits`, qa_bench only in `adapters.bench`. The bench adapter reaches into `qa_bench.formats.jsonl` for `dumps_line` and `parse_lines`, which qa-bench does not list in `qa_bench.formats.__all__`. `pwdlib` and `argon2` are kept out of the domain, application and ports by rules 3 and 4 and out of everything but `adapters.hashing` by rule 5; the migration scripts are checked too. `tests/test_contracts.py` copies the package to a temporary folder, adds one forbidden import per case and checks that the right contract breaks, with controls for the imports that must stay allowed. A contract that silently stopped working would otherwise pass forever.

When a contract fails, fix the import; do not relax the contract. Adding an adapter area means adding it to rule 2 (`tests/test_contracts.py` fails until you do) and, if it owns one of the libraries of rule 5, to that contract's `ignore_imports` (both the `adapters.<name> -> lib` and the `adapters.<name>.** -> lib` form), and saying so in the pull request.

## Tests and gates

```bash
./scripts/check.sh        # ruff, ruff format, lint-imports, pyright, pytest with coverage (floor 85%)
```

`tests/` holds one file per concern: `test_settings.py`, `test_container.py`, `test_status.py`, `test_http.py` (routes, problem details, the OpenAPI text), `test_guard.py` (the address rule), `test_cli.py` (serve, openapi), `test_contracts.py`, and for sign-in `test_auth_domain.py` (rules), `test_auth_usecases.py` (use cases over a real SQLite file and a clock the test moves), `test_auth_http.py` (cookies, CSRF, roles, the contract), `test_hosts.py`, `test_origin.py`, `test_logs.py` (including a search of the log for every secret used), `test_persistence.py` (migrations, repositories), `test_storage.py` and `test_private_files.py` (modes and ownership), `test_hashing.py`, `test_seed.py`, `test_users_cli.py`, `test_real_server.py` (a real uvicorn over a socket), and for the circuit check `test_circuits_domain.py` (strict JSON, counting, caps), `test_circuits_lane.py` (the thread, the timeout, the adapter), `test_circuits_http.py` (the four routes, duplicate keys, refusals, caps, the contract) and `test_circuits_bridge.py` (every LaTeX source of `packages/quantum-sim/tests/fixtures/bridge/` sent through `/circuits/parse`, `/circuits/run` and `/circuits/check`, expecting what quantum-sim's `test_bridge.py` expects: the saved document, the refusals, the hand-written unitaries, the drawn identities and the three-wire QFT equal to the DFT), and for asking `test_ask_domain.py` (the reader of a formatted answer, the state of an ask), `test_lanes.py`, `test_ask_desk.py` (queue, limits, cancel, retention, failures, the log), `test_asks_http.py` and `test_documents_http.py` (the routes over the fake engine), `test_asks_real_server.py` (the stream over a real socket: phases arrive while the answer is made, keep-alive, a reader that hangs up), `test_asks_openapi.py`, `test_fake_ask.py` (the invented corpus is consistent), `test_docingest_ask.py` (the engine on a real `AskSession`, the paper reader on a real docingest store, the wording of failures), `test_ask_wiring.py` and `test_ask_edges.py`; and for the question sets `test_sets_domain.py` (rules, review states, filters), `test_sets_catalog.py`, `test_sets_files.py`, `test_sets_formats.py` (qa-bench adapter, every older shape), `test_sets_usecases.py` (use cases over a real database and real files, including parallel writers), `test_sets_http.py` (roles, `If-Match`, import, reviews and the race of a reviewer who read an old text, case-only names, many-member gzip, freeze, caps, the log) and `test_demo_sets.py`. The sets fixtures under `tests/fixtures/sets/` are made up. For the benchmark: `test_jobs_domain.py` (every move of the state machine, ids, parameters), `test_runs_domain.py` (the lines of a run file against the `qa-run/1` schema, the R-281 prompt md5), `test_jobs_repo.py` (the job table, racing moves, recovery), `test_bench_files.py` (private files, half lines, log offsets, the run reader), `test_models_adapter.py` (the model servers over a fake transport), `test_arms.py`, `test_fake_bench.py`, `test_job_desk.py` and `test_job_desk_edges.py` (one run at a time, asks between questions, cancel, resume exactness, recovery, model servers that go away, scorings), `test_results.py` (runs dropped in by hand, under another name or with a broken header, files still growing, the md5 of a run), `test_bench_scores.py` (a score equals qa-bench's, and the md5 a run lists is the one its score records), `test_jobs_http.py`, `test_jobs_openapi.py`, `test_jobs_real_server.py` (progress over a real socket, an ask between two questions, a stop and a restart) and `test_bench_wiring.py`. `tests/bench_helpers.py` has `ScriptedArms`, whose questions a test holds and fails by hand, and `quotes-only/v1`, a frozen set whose gold passages have no `md_text`, which the tests of the oracle's `422` run on. The async tests run with `helpers.arun` (no async pytest plugin is installed); the stream tests that must hold a question use `ask_helpers.ScriptedEngine`. HTTP tests use FastAPI's `TestClient` over the fake container. Password hashing uses a cheap Argon2id cost in tests (`cheap_hashing=True`); one test checks the real default cost. Helpers are in `tests/helpers.py`. Test dependencies come from the `dev` group; `TestClient` needs `httpx`.

CI runs the package in the Linux lane (ruff, format, lint-imports, pytest) and the macOS lane (pyright, pytest), like the other packages.
