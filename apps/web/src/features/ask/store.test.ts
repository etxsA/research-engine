import { endSession } from "@/shared/lib/session";
import { answerOf } from "@/test/ask-api";
import {
  activeEntry,
  changedSettings,
  DEFAULT_SETTINGS,
  isActive,
  MAX_ENTRIES,
  setActiveStream,
  useAskStore,
} from "./store";

const store = useAskStore;

beforeEach(() => {
  store.getState().reset();
});

describe("the entries of the ask store", () => {
  it("starts a question as sending and selects it", () => {
    const key = store.getState().begin("What?", DEFAULT_SETTINGS);
    const [entry] = store.getState().entries;
    expect(entry).toMatchObject({ key, question: "What?", status: "sending", askId: null });
    expect(store.getState().selectedKey).toBe(key);
    expect(activeEntry(store.getState().entries)?.key).toBe(key);
  });

  it("moves a question forward and never back", () => {
    const key = store.getState().begin("What?", DEFAULT_SETTINGS);
    store.getState().accepted(key, "a1", 2);
    expect(store.getState().entries[0]).toMatchObject({
      askId: "a1",
      status: "queued",
      position: 2,
    });
    store.getState().advance(key, "queued", 1);
    expect(store.getState().entries[0]).toMatchObject({ status: "queued", position: 1 });
    store.getState().advance(key, "answering");
    expect(store.getState().entries[0]).toMatchObject({ status: "answering", position: null });
    store.getState().advance(key, "retrieving");
    store.getState().advance(key, "queued", 0);
    expect(store.getState().entries[0]?.status).toBe("answering");
  });

  it("keeps the last position when a queued update has none", () => {
    const key = store.getState().begin("What?", DEFAULT_SETTINGS);
    store.getState().accepted(key, "a1", 3);
    store.getState().advance(key, "queued");
    expect(store.getState().entries[0]?.position).toBe(3);
  });

  it("ends a question with its answer and then ignores late events", () => {
    const key = store.getState().begin("What?", DEFAULT_SETTINGS);
    store.getState().accepted(key, "a1", 0);
    const response = answerOf("transmon");
    store.getState().succeed(key, response);
    expect(store.getState().entries[0]).toMatchObject({ status: "done", response });
    expect(isActive("done")).toBe(false);
    expect(activeEntry(store.getState().entries)).toBeUndefined();

    store.getState().advance(key, "retrieving");
    store.getState().fail(key, { kind: "network" });
    store.getState().cancelled(key);
    store.getState().accepted(key, "a2", 0);
    expect(store.getState().entries[0]).toMatchObject({ status: "done", response, askId: "a1" });
  });

  it("records a failure and a cancel", () => {
    const failed = store.getState().begin("One", DEFAULT_SETTINGS);
    store.getState().fail(failed, { kind: "lost" });
    expect(store.getState().entries[0]).toMatchObject({
      status: "failed",
      failure: { kind: "lost" },
    });
    const cancelled = store.getState().begin("Two", DEFAULT_SETTINGS);
    store.getState().cancelled(cancelled);
    expect(store.getState().entries[0]?.status).toBe("cancelled");
  });

  it("keeps the newest ten questions", () => {
    for (let n = 1; n <= MAX_ENTRIES + 3; n += 1) {
      const key = store.getState().begin(`Question ${n}`, DEFAULT_SETTINGS);
      store.getState().fail(key, { kind: "lost" });
    }
    const questions = store.getState().entries.map((entry) => entry.question);
    expect(questions).toHaveLength(MAX_ENTRIES);
    expect(questions[0]).toBe(`Question ${MAX_ENTRIES + 3}`);
    expect(questions.at(-1)).toBe("Question 4");
  });

  it("never puts a question back in the line behind the ones it has passed", () => {
    const key = store.getState().begin("Where am I?", DEFAULT_SETTINGS);
    store.getState().accepted(key, "a1", 3);
    store.getState().advance(key, "queued", 1);
    // A stream that reopens replays from its first event, with the position it had then.
    store.getState().advance(key, "queued", 3);
    expect(store.getState().entries[0]).toMatchObject({ status: "queued", position: 1 });
    store.getState().advance(key, "queued", 0);
    expect(store.getState().entries[0]).toMatchObject({ position: 0 });
    store.getState().advance(key, "retrieving");
    store.getState().advance(key, "queued", 2);
    expect(store.getState().entries[0]).toMatchObject({ status: "retrieving", position: null });
  });

  it("ignores an event for a question that is gone", () => {
    store.getState().advance("nothing", "answering");
    store.getState().succeed("nothing", answerOf("transmon"));
    expect(store.getState().entries).toEqual([]);
  });
});

describe("the options and the side panel", () => {
  it("counts the options that differ from the server's", () => {
    expect(changedSettings(DEFAULT_SETTINGS)).toBe(0);
    expect(changedSettings({ useIndex: false, bm25: "always", wholeBlocks: true })).toBe(3);
    store.getState().setSettings({ bm25: "never" });
    expect(changedSettings(store.getState().settings)).toBe(1);
    store.getState().resetSettings();
    expect(store.getState().settings).toEqual(DEFAULT_SETTINGS);
  });

  it("opens, moves and closes a page", () => {
    store.getState().moveSource(4);
    expect(store.getState().source).toBeNull();
    store.getState().openSource({ docId: "d", page: 2, first: 2, last: 3, opener: "q1:chip:0" });
    store.getState().moveSource(3);
    expect(store.getState().source).toMatchObject({ docId: "d", page: 3, first: 2, last: 3 });
    store.getState().closeSource();
    expect(store.getState().source).toBeNull();
  });
});

describe("forgetting everything", () => {
  it("clears questions, answers, options and the panel, and closes the stream", () => {
    const close = vi.fn();
    setActiveStream({ close });
    store.getState().setQuestion("draft");
    store.getState().setSettings({ useIndex: false });
    const key = store.getState().begin("What?", DEFAULT_SETTINGS);
    store.getState().succeed(key, answerOf("transmon"));
    store.getState().openSource({ docId: "d", page: 1, first: 1, last: 1, opener: "x" });
    store.getState().reset();
    const state = store.getState();
    expect(state.question).toBe("");
    expect(state.entries).toEqual([]);
    expect(state.settings).toEqual(DEFAULT_SETTINGS);
    expect(state.source).toBeNull();
    expect(state.selectedKey).toBeNull();
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("happens when the session ends", () => {
    store.getState().begin("What?", DEFAULT_SETTINGS);
    store.getState().setQuestion("draft");
    endSession();
    expect(store.getState().entries).toEqual([]);
    expect(store.getState().question).toBe("");
  });

  it("replaces and closes the stream of the question before", () => {
    const first = { close: vi.fn() };
    const second = { close: vi.fn() };
    setActiveStream(first);
    setActiveStream(second);
    expect(first.close).toHaveBeenCalledTimes(1);
    expect(second.close).not.toHaveBeenCalled();
    setActiveStream(null);
    expect(second.close).toHaveBeenCalledTimes(1);
  });
});

describe("putting a sent question back in the box", () => {
  it("counts as spoken only when it was, and never when empty", () => {
    store.getState().speak("dictated words");
    store.getState().restore("a typed question", false);
    expect(store.getState()).toMatchObject({ question: "a typed question", spoken: false });
    store.getState().restore("dictated again", true);
    expect(store.getState()).toMatchObject({ question: "dictated again", spoken: true });
    store.getState().restore("  ", true);
    expect(store.getState().spoken).toBe(false);
  });
});
