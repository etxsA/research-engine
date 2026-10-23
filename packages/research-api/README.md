# research-api

The HTTP API of the research engine. A FastAPI service built in the same hexagonal style as [docingest](../docingest/README.md): the rules sit in a pure core, the libraries sit in adapters, and import-linter contracts keep it that way. It serves the web app with cited answers, the papers they cite and the circuit check, and will serve question sets and benchmark runs, using docingest and [quantum-sim](../quantum-sim/README.md) as libraries.

It has the layers, the import contracts, the settings loader, a fake mode, the error format, the OpenAPI export, the circuit check endpoints and the sign-in part: accounts in SQLite, Argon2id password hashes, cookie sessions with a CSRF check, a login rate limit, two roles, the `users` command and the private data folder, and the ask path: questions answered one at a time by docingest, followed as server-sent events, with the cited papers readable at a page (see [Asking and reading papers](#asking-and-reading-papers)). It also serves the built web app from its own origin ([The web app](#the-web-app)). Everything else (sets, benchmarks) arrives in later pull requests, each adding use cases, ports and adapters inside this structure.

## Run it

```bash
uv sync --locked --all-extras             # from the repository root
cd packages/research-api
uv run research-api serve --fake          # test data, 127.0.0.1:8090 (--port N for another port)
uv run research-api serve --fake --ask-pause 0   # the same, with the fake engine answering at once
curl -s http://127.0.0.1:8090/api/v1/health
curl -s http://127.0.0.1:8090/api/v1/status
```

`--fake` builds the service from stand-ins: no settings file, no network, a clock fixed at 2026-01-01 so documents repeat exactly, and a throwaway SQLite database in a private temporary folder that is removed when the server stops. It answers on loopback only. It creates the test accounts of [`tests/fixtures/seed-users.json`](tests/fixtures/seed-users.json) (made-up names and test passwords: `ana` admin, `beto` and `dani` members, `temp` a member with a temporary password that must be changed at the first sign-in), or those of the file named by `--seed FILE`. It is what the end-to-end tests of the web app run against. The clock of fake mode is fixed, so a session never expires and a rate-limit block never lifts: a test that fails five sign-ins on purpose should use its own account (`dani`) and a restart clears it. Fake mode also accepts any loopback `Origin` and `Host`, because the web app's development and test servers run on another loopback port and forward `/api` here.

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
| `docingest_config` | none | Absolute path of the docingest settings file that names the papers (`output_dir`), the index and the model servers. A file that does not exist or cannot be read stops the start. Without the key everything works except asking questions and reading papers: `/status` says so and an ask ends with a `503`. The file is read once, so a change needs a restart (the index itself is reloaded by itself when a build commits). |

A key that is not in the table is an error. Later pull requests add their keys here (model servers, circuit limits, backups).

To run under another process manager: `uvicorn research_api.entrypoints.http:create_app --factory`, with `RESEARCH_API_CONFIG` (or `RESEARCH_API_FAKE=1`) in the environment. `RESEARCH_API_WEB_DIR` in the environment sets `web_dir` for the factory, in fake mode too. The `serve` command turns uvicorn's access log off (a raw access log would hold paths and query strings) and its forwarded-address handling off (uvicorn would otherwise believe an `X-Forwarded-For` header sent from this machine, and so let any account on a shared server pick the address the rate limit sees). The factory sets up the service's log file and silences the access log itself, but only the command line of uvicorn can turn off the forwarded headers: pass `--no-proxy-headers`.

The address rule does not depend on how the server is started. The app checks the local address of every connection (`scope["server"]`, set by uvicorn) and answers `403` with a problem document when it is not allowed: loopback only in fake mode; loopback or exactly the `bind_host` address in live mode (the rest of the Tailscale block, `100.64.0.0/10`, is also carrier-grade NAT space that other hosts use, so it is not accepted). So `uvicorn ... --factory --host 0.0.0.0` listens on every interface but serves nothing on the ones it must not. Prefer `--host` set to the address you mean anyway, so the port is not open on the others. A server without an IP address (a Unix socket) is refused too. The check is in `entrypoints/http/guard.py`.

## The web app

With `web_dir` set, the service also answers every path outside `/api` with the built web app, so the browser meets one origin: the page, the API and the cookies share it, and the API needs no CORS. Nothing is served when `web_dir` is not set (every path outside `/api/v1` is then a 404 problem).

| Request | Answer |
|---|---|
| A path that names a file in the folder | That file, with its type, `ETag` and `Last-Modified` (`304` for a request that already holds it). Files under `/assets/` carry a content hash in their names and are cached for a year; everything else (`index.html`, `theme-init.js`, `version.json`) must be revalidated. |
| Any other path without an extension (`/login`, `/sets/x`) | `index.html`: these are routes of the single-page app. |
| A path with an extension and no such file (`/assets/app-old.js`) | `404` problem. A page from an older release asking for a script that no longer exists must fail, not receive HTML. |
| Anything under `/api` that no route matched | `404` problem, never the home page. |
| A wrong method on a route of the API (`DELETE /api/v1/health`) | `405` with `Allow`, the same answer as without a web app: the web app is the router's fallback and sees only paths no route matched. |
| A method other than `GET` and `HEAD` outside `/api` | `405` problem with `Allow`. |
| The folder or its `index.html` is missing | `503` problem: "The web app is not installed on this server." |
| A path that leaves the folder (`..`, a backslash, a NUL, a symbolic link that points out) | `404` problem. |

Every answer of the web app carries `Content-Security-Policy` (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`), `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`. Only styles may be inline (the UI libraries set style attributes); scripts and connections are limited to the origin itself.

`web_dir` may be a symbolic link: it is resolved on every request, so pointing the link at a new build changes what is served with no restart. The bind, `Host` and `Origin` checks and the access log (which records the route as `-`, never the path) apply to these paths like to the API's. The code is `entrypoints/http/webapp.py`; the page is not part of the OpenAPI contract.

## What it serves now

All paths are under `/api/v1`.

| Method and path | Who | Does |
|---|---|---|
| `GET /health` | anyone | `{"ok": true}`, for a start script or a load check. |
| `GET /status` | member | `mode` (`live` or `fake`, an enum in the contract), `api_version`, `started_at`, `uptime_s`, `ready`, and `ask`: `state` (`warming_up`, `ready` or `unavailable`), a fixed-text `reason` when unavailable, `index_generation`, `queued` (asks waiting for the GPU lane) and `busy`. It is for members because it shows when someone is asking (`/health` is the open one). In live mode `index_generation` is empty until the first question has been answered. Model-server probes join it later. |
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

`member` and `admin` answer `401` without a session, `403` when the CSRF header is missing on a write, and `403` ("Change your password first.") while `must_change_password` is true. `signed_in` is the same without that last rule; only `/auth/me`, `/auth/password` and `/auth/logout` use it. The contract lists the cookie as the `sessionCookie` security scheme.

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

SQLite runs in WAL mode with foreign keys on for every connection and a 5 s wait on a busy file. Tables: `users`, `sessions`, `login_attempts`, made by the Alembic migration `0001` (`adapters/persistence/migrations/`, batch mode for SQLite). `research-api db upgrade` creates the file and applies migrations; `serve` and `users` refuse to start on a database that is behind the code and say to run it. `research-api db status` prints the revision and exits 1 when behind. A change to a table needs a model change and a migration; `tests/test_persistence.py` fails when the two disagree. Fake mode builds its throwaway database straight from the models, and the same test checks that this gives the same tables as the migrations.

### Logging

JSON lines, to `<log_dir>/api.log` (`0600`, rotated at 10 MiB, five kept) or standard error. Each request: time, request id (also sent back as `X-Request-Id`), username when signed in, method, route template (`/admin/sessions/{session_id}`, never the path as sent), status, duration. Audit events: `login.success`, `login.failure`, `login.blocked` (the username of the account, or `(unknown)`, and the address), `logout`, `password.changed`, `password.refused`, `session.ended`, and the `users` commands. Only a fixed list of fields is ever copied into a line, and no body, query string, header or cookie is read, so a password, token, question or answer cannot get in. A name that no account has is logged as `(unknown)`, never as typed, in case it was a password typed in the wrong box. uvicorn's own access log stays off.

## Circuit check

Four endpoints read a circuit drawn in LaTeX, simulate a circuit and compare it with a state, a unitary or another circuit. They use docingest's `gate_list` and [quantum-sim](../quantum-sim/README.md) as libraries, in the same process, on the CPU. They need no model server and no GPU, so fake mode runs the real engine.

| Method and path | Body | Does |
|---|---|---|
| `POST /circuits/parse` | `{"latex": "..."}` | docingest reads the code of one block (quantikz, Qcircuit, yquant, tikz-cd). Returns `status` (`parsed`, `partial` or `unparsed`), `text` (the gate list in words) and `circuits` (each one a `circuit/1` document, `CircuitSteps.to_dict()`). |
| `POST /circuits/run` | `{"circuit_json": "...", "input"?, "values"?, "unitary"?}` | The state and the outcome probabilities, and the unitary when `unitary` is true. `input` is a basis label (`"010"`, wire 1 first) or a list of amplitudes. `values` gives each symbol a number or an angle written as text, such as `\pi/2`. |
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

## Layout

```text
src/research_api/
  domain/          errors, ServerStatus, auth (User, Session, the password and session rules),
                   ask (the question, the answer, the reader of a formatted answer, the life of one
                   ask), documents (papers, pages, the short-id rule), the circuit rules (strict
                   JSON, caps). Standard library only.
  ports/           Protocols (Clock, CircuitEngine; UserRepo, SessionRepo, LoginAttempts,
                   PasswordHasher; AskEngine; DocumentReader) and the values that cross them
  application/     use cases (ReportStatus; SignIn, SignOut, Authenticate, ChangePassword,
                   ListSessions, EndSession, ManageUsers; AskDesk and the GpuLane; ListDocuments,
                   GetDocument, ReadPage; ParseCircuit, RunCircuit, CheckCircuit,
                   ShowCircuitConventions)
  adapters/        clock, fake (fake mode: clock, the invented corpus, the fake engine and reader),
                   persistence (SQLAlchemy, Alembic), hashing (Argon2id), files (private folders),
                   circuits (docingest gate lists and quantum-sim, with the thread that bounds the
                   work), docingest_ask (the engine on AskSession, the paper reader, the wording of
                   failures, the stand-ins for a service without a docingest file), and empty
                   folders for the adapters to come: bench, models
  bootstrap/       settings.py (the TOML loader and its rules), storage.py (private folders, the
                   database), seed.py (fake-mode accounts), container.py (wiring, fake mode)
  entrypoints/     http/ (FastAPI app, routes, auth routes, circuit routes, ask and document routes,
                   security dependencies, Host and Origin checks, access log, no-store, schemas,
                   problem details), cli.py, logs.py
```

The six layers import downward only: `entrypoints`, `bootstrap`, `adapters`, `application`, `ports`, `domain`. Only `bootstrap` and `entrypoints` import adapters. Use cases take their ports as keyword-only constructor arguments, as in docingest.

## Import contracts

Seven rules, enforced by import-linter (`uv run lint-imports`, a gate in `scripts/check.sh` and in the Linux CI lane). Rule 5 is written as five `forbidden` contracts, one per library (or pair of libraries). Each one covers the whole package (`source_modules = ["research_api"]`) and lists as exceptions only the adapters that own the library, so the package root, `adapters/__init__.py` and any adapter added later are checked too.

| # | Rule | Type |
|---|---|---|
| 1 | The six layers above, outer to inner | `layers` |
| 2 | The nine adapter areas (`clock`, `fake`, `persistence`, `docingest_ask`, `circuits`, `bench`, `hashing`, `files`, `models`) do not import each other (so `bootstrap/storage.py`, not an adapter, joins `files` and `persistence`); a test fails when a folder or module under `adapters/` is missing from the list | `independence` |
| 3 | `domain` imports no `fastapi`, `starlette`, `sqlalchemy`, `alembic`, `pwdlib`, `argon2`, `httpx`, `uvicorn`, `docingest`, `docingest_index`, `quantum_sim`, `qa_bench` or `jsonschema` | `forbidden` |
| 4 | `application` and `ports` import none of those: they reach docingest, quantum-sim and qa-bench only through ports | `forbidden` |
| 5 | Only `adapters.docingest_ask` and `adapters.circuits` import `docingest`; only `adapters.circuits` imports `quantum_sim`; only `adapters.bench` imports `qa_bench`; only `adapters.hashing` imports `pwdlib` and `argon2`; only `adapters.persistence` imports `sqlalchemy` and `alembic` | `forbidden` (five) |
| 6 | Only `bootstrap` and `entrypoints` import `adapters` | `protected` |
| 7 | `research_api` never imports `docingest_index` (docingest finds its plugin by entry point) | `forbidden` |

The reverse direction is a contract in each of the other packages: docingest, docingest-index and quantum-sim never import `research_api` or `qa_bench`.

Rules 3 to 5 also name libraries the package does not import yet (qa_bench); the circuits adapter is the first real user of the docingest and quantum_sim exceptions. The rule is in place before the first line that would break it. `pwdlib` and `argon2` are kept out of the domain, application and ports by rules 3 and 4 and out of everything but `adapters.hashing` by rule 5; the migration scripts are checked too. `tests/test_contracts.py` copies the package to a temporary folder, adds one forbidden import per case and checks that the right contract breaks, with controls for the imports that must stay allowed. A contract that silently stopped working would otherwise pass forever.

When a contract fails, fix the import; do not relax the contract. Adding an adapter area means adding it to rule 2 (`tests/test_contracts.py` fails until you do) and, if it owns one of the libraries of rule 5, to that contract's `ignore_imports` (both the `adapters.<name> -> lib` and the `adapters.<name>.** -> lib` form), and saying so in the pull request.

## Tests and gates

```bash
./scripts/check.sh        # ruff, ruff format, lint-imports, pyright, pytest with coverage (floor 85%)
```

`tests/` holds one file per concern: `test_settings.py`, `test_container.py`, `test_status.py`, `test_http.py` (routes, problem details, the OpenAPI text), `test_guard.py` (the address rule), `test_cli.py` (serve, openapi), `test_contracts.py`, and for sign-in `test_auth_domain.py` (rules), `test_auth_usecases.py` (use cases over a real SQLite file and a clock the test moves), `test_auth_http.py` (cookies, CSRF, roles, the contract), `test_hosts.py`, `test_origin.py`, `test_logs.py` (including a search of the log for every secret used), `test_persistence.py` (migrations, repositories), `test_storage.py` and `test_private_files.py` (modes and ownership), `test_hashing.py`, `test_seed.py`, `test_users_cli.py`, `test_real_server.py` (a real uvicorn over a socket), and for the circuit check `test_circuits_domain.py` (strict JSON, counting, caps), `test_circuits_lane.py` (the thread, the timeout, the adapter), `test_circuits_http.py` (the four routes, duplicate keys, refusals, caps, the contract) and `test_circuits_bridge.py` (every LaTeX source of `packages/quantum-sim/tests/fixtures/bridge/` sent through `/circuits/parse`, `/circuits/run` and `/circuits/check`, expecting what quantum-sim's `test_bridge.py` expects: the saved document, the refusals, the hand-written unitaries, the drawn identities and the three-wire QFT equal to the DFT), and for asking `test_ask_domain.py` (the reader of a formatted answer, the state of an ask), `test_lanes.py`, `test_ask_desk.py` (queue, limits, cancel, retention, failures, the log), `test_asks_http.py` and `test_documents_http.py` (the routes over the fake engine), `test_asks_real_server.py` (the stream over a real socket: phases arrive while the answer is made, keep-alive, a reader that hangs up), `test_asks_openapi.py`, `test_fake_ask.py` (the invented corpus is consistent), `test_docingest_ask.py` (the engine on a real `AskSession`, the paper reader on a real docingest store, the wording of failures), `test_ask_wiring.py` and `test_ask_edges.py`. The async tests run with `helpers.arun` (no async pytest plugin is installed); the stream tests that must hold a question use `ask_helpers.ScriptedEngine`. HTTP tests use FastAPI's `TestClient` over the fake container. Password hashing uses a cheap Argon2id cost in tests (`cheap_hashing=True`); one test checks the real default cost. Helpers are in `tests/helpers.py`. Test dependencies come from the `dev` group; `TestClient` needs `httpx`.

CI runs the package in the Linux lane (ruff, format, lint-imports, pytest) and the macOS lane (pyright, pytest), like the other packages.
