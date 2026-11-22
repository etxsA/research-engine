import { vi } from "vitest";

/** A stand-in for Web Audio, which jsdom does not have: sources that play until a test ends them. */

export interface FakeBuffer {
  duration: number;
  /** What the "audio" says: the test server answers with the text it was asked for. */
  text: string;
}

export interface FakeSource {
  buffer: FakeBuffer | null;
  started: boolean;
  stopped: boolean;
  onended: (() => void) | null;
  start: () => void;
  stop: () => void;
  connect: (node: unknown) => void;
}

export interface FakeAudio {
  /** Every source ever created, in order. */
  sources: FakeSource[];
  /** The loudness the analyser reports (amplitude of a constant signal). */
  amplitude: { value: number };
  closed: () => boolean;
  /** The source that is playing now ends. */
  endCurrent: () => void;
  restore: () => void;
}

export function installFakeAudio(): FakeAudio {
  const sources: FakeSource[] = [];
  const amplitude = { value: 0.1 };
  let closed = false;
  class FakeContext {
    destination = {};
    state = "running";
    resume = vi.fn(() => Promise.resolve());
    close = vi.fn(() => {
      closed = true;
      return Promise.resolve();
    });
    createAnalyser() {
      return {
        fftSize: 2048,
        connect: vi.fn(),
        getFloatTimeDomainData: (target: Float32Array) => target.fill(amplitude.value),
      };
    }
    createBufferSource(): FakeSource {
      const source: FakeSource = {
        buffer: null,
        started: false,
        stopped: false,
        onended: null,
        connect: vi.fn(),
        start: () => {
          source.started = true;
        },
        stop: () => {
          source.stopped = true;
          source.onended?.();
        },
      };
      sources.push(source);
      return source;
    }
    decodeAudioData(data: ArrayBuffer): Promise<FakeBuffer> {
      return Promise.resolve({ duration: 1, text: new TextDecoder().decode(data) });
    }
  }
  vi.stubGlobal("AudioContext", FakeContext);
  return {
    sources,
    amplitude,
    closed: () => closed,
    endCurrent: () => {
      const playing = [...sources].reverse().find((s) => s.started && !s.stopped);
      if (!playing) throw new Error("nothing is playing");
      playing.stopped = true;
      playing.onended?.();
    },
    restore: () => vi.unstubAllGlobals(),
  };
}
