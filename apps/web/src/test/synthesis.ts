import { http, HttpResponse } from "msw";
import { server } from "./server";

export interface SynthesisCall {
  text: string;
  language: string | null;
  csrf: string | null;
  contentType: string | null;
  signal: AbortSignal;
  /** Answers this call (when held) or lets it answer by itself. */
  release: () => void;
}

export interface FakeSynthesis {
  calls: SynthesisCall[];
}

interface Options {
  /** Answer with this status for the call at this index (0 based), or for every call from it on. */
  failAt?: { index: number; status: number; from?: boolean };
  /** Hold every answer until the call's `release` is called. */
  hold?: boolean;
}

/**
 * The speech endpoint of the API: the "audio" is the text asked for, so a test can tell which
 * sentence a source plays.
 */
export function serveSynthesis(options: Options = {}): FakeSynthesis {
  const fake: FakeSynthesis = { calls: [] };
  server.use(
    http.post("*/api/v1/speech/synthesis", async ({ request }) => {
      const body = (await request.json()) as { text: string; language: string | null };
      const index = fake.calls.length;
      let release: () => void = () => undefined;
      const gate = options.hold
        ? new Promise<void>((resolve) => {
            release = resolve;
          })
        : Promise.resolve();
      fake.calls.push({
        text: body.text,
        language: body.language,
        csrf: request.headers.get("x-csrf-token"),
        contentType: request.headers.get("content-type"),
        signal: request.signal,
        release,
      });
      await gate;
      const fail = options.failAt;
      if (fail && (fail.from ? index >= fail.index : index === fail.index)) {
        return new HttpResponse(null, { status: fail.status });
      }
      return new HttpResponse(new TextEncoder().encode(body.text), {
        headers: { "Content-Type": "audio/wav", "Cache-Control": "no-store" },
      });
    }),
  );
  return fake;
}
