import { vi } from "vitest";

/** Stand-ins for the microphone, AudioContext and AudioWorklet, which jsdom does not have. */
export interface FakeMic {
  tracks: { stop: ReturnType<typeof vi.fn> }[];
  getUserMedia: ReturnType<typeof vi.fn>;
  /** Plays a block of samples from the microphone into the open recording. */
  emit: (samples: Float32Array) => void;
  contexts: { closed: boolean; sampleRate: number; modules: string[] }[];
  restore: () => void;
}

interface Options {
  secure?: boolean;
  /** Make getUserMedia fail with this error name. */
  failWith?: string;
  sampleRate?: number;
}

class FakeNode {
  port: { onmessage: ((event: { data: Float32Array }) => void) | null } = { onmessage: null };
  disconnect = vi.fn();
}

export function installFakeMic(options: Options = {}): FakeMic {
  const { secure = true, failWith, sampleRate = 48_000 } = options;
  const nodes: FakeNode[] = [];
  const contexts: FakeMic["contexts"] = [];
  const tracks = [{ stop: vi.fn() }];
  const getUserMedia = vi.fn(() => {
    if (failWith) {
      return Promise.reject(Object.assign(new Error(failWith), { name: failWith }));
    }
    return Promise.resolve({ getTracks: () => tracks } as unknown as MediaStream);
  });

  class FakeAudioContext {
    sampleRate = sampleRate;
    state = { closed: false, sampleRate, modules: [] as string[] };
    audioWorklet = {
      addModule: (url: string) => {
        this.state.modules.push(url);
        return Promise.resolve();
      },
    };
    constructor() {
      contexts.push(this.state);
    }
    createMediaStreamSource() {
      return { connect: vi.fn() };
    }
    close() {
      this.state.closed = true;
      return Promise.resolve();
    }
  }
  class FakeAudioWorkletNode extends FakeNode {
    constructor() {
      super();
      nodes.push(this);
    }
  }

  const saved = {
    secure: Object.getOwnPropertyDescriptor(window, "isSecureContext"),
    media: Object.getOwnPropertyDescriptor(navigator, "mediaDevices"),
    context: globalThis.AudioContext,
    node: globalThis.AudioWorkletNode,
  };
  Object.defineProperty(window, "isSecureContext", { value: secure, configurable: true });
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia },
    configurable: true,
  });
  globalThis.AudioContext = FakeAudioContext as unknown as typeof AudioContext;
  globalThis.AudioWorkletNode = FakeAudioWorkletNode as unknown as typeof AudioWorkletNode;

  return {
    tracks,
    getUserMedia,
    contexts,
    emit: (samples) => {
      for (const node of nodes) node.port.onmessage?.({ data: samples });
    },
    restore: () => {
      if (saved.secure) Object.defineProperty(window, "isSecureContext", saved.secure);
      if (saved.media) Object.defineProperty(navigator, "mediaDevices", saved.media);
      else Reflect.deleteProperty(navigator, "mediaDevices");
      globalThis.AudioContext = saved.context;
      globalThis.AudioWorkletNode = saved.node;
    },
  };
}
