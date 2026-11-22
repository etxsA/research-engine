import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createAuthMock, type AuthMock } from "./auth-mock";
import { circuitHandlers } from "./circuits-api";
import { askHandlers } from "./ask-api";
import { setsHandlers } from "./sets-api";
import { benchHandlers } from "./benchmarks-api";

export let authMock: AuthMock = createAuthMock();

export function resetAuthMock(): AuthMock {
  authMock = createAuthMock();
  return authMock;
}

export const STATUS_BODY = {
  mode: "fake",
  api_version: "0.1.0",
  started_at: "2026-01-01T00:00:00Z",
  uptime_s: 12.5,
  ready: true,
  ask: { state: "ready", reason: null, index_generation: null, queued: 0, busy: false },
  bench: { running_job: null, queued_runs: 0, scoring: false, queued_scores: 0 },
  speech: { stt: "ready", tts: "ready" },
} as const;

export const server = setupServer(
  ...circuitHandlers,
  ...setsHandlers,
  http.get("*/api/v1/status", () => HttpResponse.json(STATUS_BODY)),
  http.all("*/api/v1/auth/*", async ({ request }) => {
    const path = new URL(request.url).pathname;
    const text = await request.text();
    const answer = authMock.handle({
      method: request.method,
      path,
      body: text ? JSON.parse(text) : undefined,
      csrfHeader: request.headers.get("x-csrf-token"),
    });
    if (answer === null) return new HttpResponse(null, { status: 404 });
    const headers = new Headers(answer.headers);
    return answer.body === undefined
      ? new HttpResponse(null, { status: answer.status, headers })
      : HttpResponse.json(answer.body, { status: answer.status, headers });
  }),
  ...askHandlers,
  ...benchHandlers,
);
