import type { vi as Vi } from "vitest";

/** Stand-ins for `speechSynthesis` and `SpeechSynthesisUtterance`, which jsdom does not have. */

export interface FakeVoice {
  name: string;
  lang: string;
  localService: boolean;
  default: boolean;
  voiceURI: string;
}

export class FakeUtterance {
  text: string;
  lang = "";
  voice: FakeVoice | null = null;
  onend: (() => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  constructor(text: string) {
    this.text = text;
  }
}

export interface FakeSpeech {
  /** Utterances queued and not yet finished, the first one is "speaking". */
  queue: FakeUtterance[];
  /** Everything ever passed to `speak`. */
  spoken: FakeUtterance[];
  cancel: ReturnType<typeof Vi.fn>;
  voices: FakeVoice[];
  /** Sets the voices and fires `voiceschanged`. */
  loadVoices: (voices: FakeVoice[]) => void;
  /** The first utterance in the queue ends. */
  finishOne: () => void;
  /** The first utterance in the queue fails. */
  failOne: (error?: string) => void;
  restore: () => void;
}

export function voice(name: string, lang: string, localService = true): FakeVoice {
  return { name, lang, localService, default: false, voiceURI: name };
}

export function installFakeSpeech(vi: typeof Vi, voices: FakeVoice[] = []): FakeSpeech {
  const listeners = new Set<() => void>();
  const fake: FakeSpeech = {
    queue: [],
    spoken: [],
    voices,
    cancel: vi.fn(() => {
      const cancelled = fake.queue.splice(0);
      for (const utterance of cancelled) utterance.onerror?.({ error: "canceled" });
    }),
    loadVoices: (next) => {
      fake.voices = next;
      for (const listener of [...listeners]) listener();
    },
    finishOne: () => {
      fake.queue.shift()?.onend?.();
    },
    failOne: (error = "synthesis-failed") => {
      fake.queue.shift()?.onerror?.({ error });
    },
    restore: () => {
      vi.unstubAllGlobals();
      Reflect.deleteProperty(window, "speechSynthesis");
    },
  };
  const synth = {
    speak: (utterance: FakeUtterance) => {
      fake.queue.push(utterance);
      fake.spoken.push(utterance);
    },
    cancel: fake.cancel,
    getVoices: () => fake.voices,
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
  vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
  return fake;
}
