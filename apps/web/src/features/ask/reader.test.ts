import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { playbackLevel, usePlayback } from "@/shared/voice/playback";
import { installFakeAudio, type FakeAudio } from "@/test/audio";
import { installFakeSpeech, voice, type FakeSpeech } from "@/test/speech";
import { serveSynthesis } from "@/test/synthesis";
import { getReaderState, onReadSessionEnded, startReading, stopReading } from "./reader";

const THREE = "The first sentence is here. The second one follows it. The third closes it.";
const SENTENCES = [
  "The first sentence is here.",
  "The second one follows it.",
  "The third closes it.",
];

let audio: FakeAudio;
beforeEach(() => {
  audio = installFakeAudio();
});
/** Lets answered requests finish: MSW cannot abort one it has already answered. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

afterEach(async () => {
  await settle();
  stopReading();
  audio.restore();
});

/** The texts the sources were given, in the order they were created. */
const played = () =>
  audio.sources.map((source) => (source.buffer as unknown as { text: string }).text);

describe("reading with the speech server", () => {
  it("sends one request per sentence and plays them in order", async () => {
    const synthesis = serveSynthesis();
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    audio.endCurrent();
    await vi.waitFor(() => expect(audio.sources).toHaveLength(2));
    audio.endCurrent();
    await vi.waitFor(() => expect(audio.sources).toHaveLength(3));
    audio.endCurrent();
    await vi.waitFor(() => expect(getReaderState().owner).toBeNull());
    expect(played()).toEqual(SENTENCES);
    expect(synthesis.calls.map((call) => call.text)).toEqual(SENTENCES);
    expect(synthesis.calls[0]).toMatchObject({ language: "en", csrf: "csrf-test-token" });
    expect(audio.closed()).toBe(true);
  });

  it("asks for the next sentence while one plays, and not before", async () => {
    const synthesis = serveSynthesis({ hold: true });
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(synthesis.calls).toHaveLength(1));
    // Nothing plays, so nothing is prefetched yet.
    expect(getReaderState().loading).toBe(true);
    synthesis.calls[0]?.release();
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    await vi.waitFor(() => expect(synthesis.calls).toHaveLength(2));
    expect(getReaderState().loading).toBe(false);
    expect(audio.sources[0]?.started).toBe(true);
    // The second sentence is held: the first still plays, and the third is not asked for.
    expect(synthesis.calls).toHaveLength(2);
    audio.endCurrent();
    synthesis.calls[1]?.release();
    await vi.waitFor(() => expect(audio.sources).toHaveLength(2));
    await vi.waitFor(() => expect(synthesis.calls).toHaveLength(3));
    synthesis.calls[2]?.release();
  });

  it("writes the loudness every frame and tells the pet when it speaks", async () => {
    serveSynthesis();
    audio.amplitude.value = 0.1;
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(usePlayback.getState().state).toBe("speaking"));
    await vi.waitFor(() => expect(playbackLevel.current).toBeCloseTo(0.4, 5));
    audio.amplitude.value = 0.5;
    await vi.waitFor(() => expect(playbackLevel.current).toBe(1));
    await settle();
    stopReading();
    expect(playbackLevel.current).toBe(0);
    expect(usePlayback.getState().state).toBe("idle");
  });

  it("stops everything: the source, the requests in flight and the context", async () => {
    const synthesis = serveSynthesis({ hold: true });
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(synthesis.calls).toHaveLength(1));
    synthesis.calls[0]?.release();
    await vi.waitFor(() => expect(synthesis.calls).toHaveLength(2));
    const source = audio.sources[0];
    stopReading();
    expect(source?.stopped).toBe(true);
    expect(audio.closed()).toBe(true);
    expect(getReaderState().owner).toBeNull();
    expect(usePlayback.getState().state).toBe("idle");
    await vi.waitFor(() => expect(synthesis.calls[1]?.signal.aborted).toBe(true));
    synthesis.calls[1]?.release();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(audio.sources).toHaveLength(1);
    expect(synthesis.calls).toHaveLength(2);
  });

  it("starting another answer stops the first", async () => {
    serveSynthesis();
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    await settle();
    startReading("b", "A different answer entirely.", "server");
    expect(audio.sources[0]?.stopped).toBe(true);
    await vi.waitFor(() => expect(audio.sources).toHaveLength(2));
    expect(getReaderState().owner).toBe("b");
    expect(played()).toEqual([SENTENCES[0], "A different answer entirely."]);
  });

  it("stops when the page is hidden", async () => {
    serveSynthesis();
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    await settle();
    window.dispatchEvent(new Event("pagehide"));
    expect(getReaderState().owner).toBeNull();
    expect(audio.sources[0]?.stopped).toBe(true);
  });

  it("says the server is busy on a 429", async () => {
    serveSynthesis({ failAt: { index: 0, status: 429 } });
    startReading("a", THREE, "server");
    await vi.waitFor(() =>
      expect(getReaderState().failed).toEqual({ owner: "a", problem: "busy" }),
    );
    expect(getReaderState().owner).toBeNull();
    expect(audio.sources).toHaveLength(0);
    expect(usePlayback.getState().state).toBe("idle");
  });

  it("says it failed on another error, after the sentences that did play", async () => {
    serveSynthesis({ failAt: { index: 1, status: 500 } });
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    audio.endCurrent();
    await vi.waitFor(() =>
      expect(getReaderState().failed).toEqual({ owner: "a", problem: "failed" }),
    );
    expect(audio.sources).toHaveLength(1);
    expect(usePlayback.getState().state).toBe("idle");
  });

  it("leaves out a sentence the server refuses (422) and reads the others", async () => {
    const synthesis = serveSynthesis({ failAt: { index: 1, status: 422 } });
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    audio.endCurrent();
    await vi.waitFor(() => expect(audio.sources).toHaveLength(2));
    audio.endCurrent();
    await vi.waitFor(() => expect(getReaderState().owner).toBeNull());
    expect(played()).toEqual([SENTENCES[0], SENTENCES[2]]);
    expect(synthesis.calls).toHaveLength(3);
    expect(getReaderState().failed).toBeNull();
  });

  it("says it failed when the server refuses every sentence", async () => {
    serveSynthesis({ failAt: { index: 0, status: 422, from: true } });
    startReading("a", THREE, "server");
    await vi.waitFor(() =>
      expect(getReaderState().failed).toEqual({ owner: "a", problem: "failed" }),
    );
    expect(audio.sources).toHaveLength(0);
  });

  it("hands a 401 to the session handler and stops quietly", async () => {
    const ended = vi.fn();
    onReadSessionEnded(ended);
    try {
      serveSynthesis({ failAt: { index: 0, status: 401 } });
      startReading("a", THREE, "server");
      await vi.waitFor(() => expect(ended).toHaveBeenCalledTimes(1));
      expect(ended.mock.calls[0]?.[0]).toMatchObject({ status: 401 });
      expect(getReaderState()).toMatchObject({ owner: null, failed: null });
      expect(usePlayback.getState().state).toBe("idle");
    } finally {
      onReadSessionEnded(null);
    }
  });

  it("says it failed, and plays nothing, when the browser keeps the audio suspended", async () => {
    audio.needsClick.value = true;
    serveSynthesis();
    startReading("a", THREE, "server");
    await vi.waitFor(
      () => expect(getReaderState().failed).toEqual({ owner: "a", problem: "failed" }),
      { timeout: 2000 },
    );
    expect(audio.sources).toHaveLength(0);
    expect(audio.closed()).toBe(true);
    expect(usePlayback.getState().state).toBe("idle");
  });

  it("says it failed when the audio cannot be decoded", async () => {
    serveSynthesis();
    vi.spyOn(AudioContext.prototype, "decodeAudioData").mockRejectedValue(new Error("bad"));
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(getReaderState().failed?.problem).toBe("failed"));
  });

  it("does nothing for an answer with nothing to say", () => {
    serveSynthesis();
    startReading("a", "(40a228002007ca7f pages 3-3)", "server");
    expect(getReaderState().owner).toBeNull();
  });
});

describe("reading with the voice of the browser", () => {
  let speech: FakeSpeech;
  beforeEach(() => {
    speech = installFakeSpeech(vi, [voice("Samantha", "en-US", true)]);
  });
  afterEach(() => {
    stopReading();
    speech.restore();
  });

  it("falls back once, from the failed sentence, when the server is down (503)", async () => {
    serveSynthesis({ failAt: { index: 1, status: 503, from: true } });
    startReading("a", THREE, "server");
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    audio.endCurrent();
    await vi.waitFor(() => expect(speech.spoken).toHaveLength(1));
    expect(speech.spoken[0]?.text).toBe(`${SENTENCES[1]} ${SENTENCES[2]}`);
    expect(getReaderState()).toMatchObject({ owner: "a", engine: "browser", fellBack: true });
    expect(usePlayback.getState().state).toBe("speaking");
    await vi.waitFor(() => expect(playbackLevel.current).toBeGreaterThan(0));
    expect(playbackLevel.current).toBeLessThanOrEqual(1);
    speech.finishOne();
    expect(getReaderState().owner).toBeNull();
    expect(playbackLevel.current).toBe(0);
    expect(usePlayback.getState().state).toBe("idle");
  });

  it("is a failure when the server is down and the browser cannot speak", async () => {
    Reflect.deleteProperty(window, "speechSynthesis");
    serveSynthesis({ failAt: { index: 0, status: 503 } });
    startReading("a", THREE, "server");
    await vi.waitFor(() =>
      expect(getReaderState().failed).toEqual({ owner: "a", problem: "failed" }),
    );
  });

  it("speaks with the browser at once when the status says the server is not ready", async () => {
    const synthesis = serveSynthesis();
    startReading("a", THREE, "browser");
    await vi.waitFor(() => expect(speech.spoken).toHaveLength(1));
    expect(synthesis.calls).toHaveLength(0);
    expect(getReaderState()).toMatchObject({ owner: "a", engine: "browser", fellBack: false });
    expect(speech.spoken[0]?.voice?.name).toBe("Samantha");
    expect(usePlayback.getState().state).toBe("speaking");
    stopReading();
    expect(speech.cancel).toHaveBeenCalled();
    expect(usePlayback.getState().state).toBe("idle");
    expect(playbackLevel.current).toBe(0);
  });

  it("says so when the browser breaks off", async () => {
    startReading("a", THREE, "browser");
    await vi.waitFor(() => expect(speech.spoken).toHaveLength(1));
    speech.failOne();
    expect(getReaderState().failed).toEqual({ owner: "a", problem: "failed" });
    expect(usePlayback.getState().state).toBe("idle");
  });

  it("waits for the voices, and a stop meanwhile speaks nothing", async () => {
    speech.voices = [];
    startReading("a", THREE, "browser");
    expect(speech.spoken).toHaveLength(0);
    stopReading();
    speech.loadVoices([voice("Samantha", "en-US", true)]);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(speech.spoken).toHaveLength(0);
  });
});
