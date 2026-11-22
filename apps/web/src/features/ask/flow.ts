import { isApiError } from "@/shared/api/errors";
import { followStream, type EventSourceFactory, type Stream } from "./stream";
import {
  activeEntry,
  isActive,
  releaseStream,
  setActiveStream,
  useAskStore,
  type AskSettings,
  type Failure,
} from "./store";
import type { AskAccepted, AskRequest, AskView, StreamEvent } from "./types";

/** How long to wait for a browser that is reconnecting by itself before asking the API where the question is. */
export const RECOVER_AFTER_MS = 2_000;
/** Between two tries to learn where a question is, when the network is down. */
export const RECOVER_RETRY_MS = 3_000;
export const MAX_RECOVERIES = 4;
export const MAX_REOPENS = 5;

/** What the flow needs from the outside: the API calls and the event source. Tests pass their own. */
export interface FlowDeps {
  create: (body: AskRequest) => Promise<AskAccepted>;
  get: (askId: string) => Promise<AskView>;
  cancel: (askId: string) => Promise<unknown>;
  /** Called when an answer has arrived, whichever way it was learned. */
  answered?: () => void;
  eventSource?: EventSourceFactory;
}

export interface AskFlow {
  /** Sends the question with its options and follows it to the end. Returns the entry's key, or null when nothing was sent. */
  submit: (question: string, settings: AskSettings, spoken?: boolean) => Promise<string | null>;
  /** Stops waiting for the active question; the API drops or forgets it. */
  cancel: () => Promise<void>;
}

export function requestFor(question: string, settings: AskSettings): AskRequest {
  return {
    question: question.trim(),
    use_index: settings.useIndex,
    bm25: settings.bm25,
    whole_blocks: settings.wholeBlocks,
    return_contexts: true,
  };
}

/** A question that cannot be sent: nothing but blanks. */
export function isBlank(question: string): boolean {
  return question.trim().length === 0;
}

function failureOf(error: unknown): Failure {
  return isApiError(error) ? { kind: "api", error } : { kind: "network" };
}

/**
 * Sends a question and follows its events until the answer, a failure or a cancel. The state is
 * in the ask store; this holds only the steps. Leaving the screen does not stop it: the stream
 * is registered with the store and is closed when the question ends or the session does.
 *
 * When the connection breaks, the API is asked where the question is (`GET /asks/{id}`): the
 * answer is read from there if it is over, and the stream is opened again if it is not.
 */
export function createAskFlow(deps: FlowDeps): AskFlow {
  const store = useAskStore;

  function follow(key: string, askId: string): void {
    let recoveries = 0;
    let reopens = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stream: Stream | undefined;

    const over = () => {
      const entry = store.getState().entries.find((candidate) => candidate.key === key);
      return !entry || !isActive(entry.status);
    };
    const finish = () => {
      clearTimeout(timer);
      timer = undefined;
      if (stream) {
        releaseStream(stream);
        stream.close();
      }
    };
    const end = (change: () => void) => {
      change();
      finish();
    };
    const fail = (failure: Failure) => end(() => store.getState().fail(key, failure));

    const apply = (event: StreamEvent) => {
      if (over()) return finish();
      clearTimeout(timer);
      timer = undefined;
      switch (event.kind) {
        case "queued":
          return store.getState().advance(key, "queued", event.position);
        case "retrieving":
        case "answering":
          return store.getState().advance(key, event.kind);
        case "answer":
          return end(() => {
            store.getState().succeed(key, event.response);
            deps.answered?.();
          });
        case "error":
          return fail({ kind: "server", failure: event.failure });
        case "cancelled":
          return end(() => store.getState().cancelled(key));
      }
    };

    const open = () => {
      const opened = followStream(
        askId,
        {
          onEvent: apply,
          onMalformed: () => fail({ kind: "malformed" }),
          onBroken: (closed) => {
            if (over()) return finish();
            schedule(closed ? 0 : RECOVER_AFTER_MS);
          },
        },
        deps.eventSource,
      );
      stream = opened;
      setActiveStream(opened);
    };

    const schedule = (delay: number) => {
      clearTimeout(timer);
      timer = setTimeout(() => void recover(), delay);
    };

    const recover = async () => {
      if (over()) return finish();
      let view: AskView;
      try {
        view = await deps.get(askId);
      } catch (error) {
        if (over()) return finish();
        if (isApiError(error, 404)) return fail({ kind: "lost" });
        if (isApiError(error)) return fail(failureOf(error));
        recoveries += 1;
        if (recoveries >= MAX_RECOVERIES) return fail({ kind: "network" });
        return schedule(RECOVER_RETRY_MS);
      }
      if (over()) return finish();
      const { response, error } = view;
      switch (view.state) {
        case "done":
          return response
            ? end(() => {
                store.getState().succeed(key, response);
                deps.answered?.();
              })
            : fail({ kind: "lost" });
        case "failed":
          return fail(error ? { kind: "server", failure: error } : { kind: "lost" });
        case "cancelled":
          return end(() => store.getState().cancelled(key));
        case "queued":
          store.getState().advance(key, "queued", view.position ?? undefined);
          break;
        case "running":
          store.getState().advance(key, view.phase ?? "retrieving");
          break;
      }
      if (stream === undefined || stream.closed) {
        reopens += 1;
        if (reopens > MAX_REOPENS) return fail({ kind: "network" });
        open();
      }
    };

    open();
  }

  return {
    async submit(question, settings, spoken = false) {
      if (isBlank(question) || activeEntry(store.getState().entries)) return null;
      const key = store.getState().begin(question.trim(), settings, spoken);
      try {
        const accepted = await deps.create(requestFor(question, settings));
        // Signed out while the request was on its way: there is nothing left to show it in.
        if (!store.getState().entries.some((entry) => entry.key === key)) return key;
        store.getState().accepted(key, accepted.ask_id, accepted.position);
        follow(key, accepted.ask_id);
      } catch (error) {
        store.getState().fail(key, failureOf(error));
      }
      return key;
    },
    async cancel() {
      const entry = activeEntry(store.getState().entries);
      if (!entry?.askId) return;
      const { key, askId } = entry;
      setActiveStream(null);
      store.getState().cancelled(key);
      try {
        await deps.cancel(askId);
      } catch {
        // the API drops it by itself; the screen has already stopped waiting
      }
    },
  };
}
