import { http, HttpResponse } from "msw";
import captured from "@/features/circuits/fixtures/captured.json";

/**
 * Answers of the circuit endpoints, and of a paper's circuits, recorded from the real API
 * (`pnpm capture:circuits`, see scripts/capture-circuit-fixtures.mjs). A request that equals a
 * recorded one (the same path under /api/v1 and the same JSON body) gets its recorded answer; any
 * other request to these endpoints fails loudly, so a test never passes on an invented answer.
 */
interface Captured {
  name: string;
  /** The path under /api/v1, such as `/circuits/parse` or `/documents/<id>/circuits`. */
  path: string;
  /** The JSON body; `{ raw }` for a body sent as text; null for a GET. */
  request: unknown;
  status: number;
  response: Record<string, unknown>;
}

const ENTRIES = captured as Captured[];

export function capturedAnswer(name: string): Captured {
  const entry = ENTRIES.find((item) => item.name === name);
  if (!entry) throw new Error(`no captured answer named ${name}`);
  return entry;
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

const PROBLEM = { "content-type": "application/problem+json" };

function replay(request: Request, body: unknown) {
  const path = new URL(request.url).pathname.replace(/^\/api\/v1/, "");
  const entry = ENTRIES.find((item) => item.path === path && sameJson(item.request, body));
  if (!entry) {
    return HttpResponse.json(
      {
        type: "about:blank",
        title: "No recording",
        status: 500,
        detail: `No recorded answer for ${request.method} ${path}`,
        instance: "",
      },
      { status: 500, headers: PROBLEM },
    );
  }
  const failure = entry.status >= 400;
  return HttpResponse.json(entry.response, {
    status: entry.status,
    ...(failure ? { headers: PROBLEM } : {}),
  });
}

export const circuitHandlers = [
  http.get("*/api/v1/circuits/conventions", ({ request }) => replay(request, null)),
  http.post("*/api/v1/circuits/:operation", async ({ request }) =>
    replay(request, await request.json()),
  ),
  http.get("*/api/v1/documents/:docId/circuits", ({ request }) => replay(request, null)),
  http.post("*/api/v1/documents/:docId/qasm", async ({ request }) =>
    replay(request, await request.json()),
  ),
];
