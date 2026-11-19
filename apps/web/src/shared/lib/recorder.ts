import { downsample, encodeWav, SPEECH_RATE } from "./wav";

/** Served from `public/`, so from our own origin. */
export const WORKLET_URL = "/mic-worklet.js";
export const MAX_SECONDS = 60; // the API's limit
export const MIN_SECONDS = 0.3;

/** Why the microphone cannot be used. Each has a translated message on the screen. */
export type MicProblem = "insecure" | "unsupported" | "denied" | "missing" | "busy" | "other";

export class MicError extends Error {
  readonly problem: MicProblem;
  constructor(problem: MicProblem) {
    super(problem);
    this.name = "MicError";
    this.problem = problem;
  }
}

/** Can this page record at all? Checked before the button is offered. */
export function micSupport(): "ok" | "insecure" | "unsupported" {
  if (!window.isSecureContext) return "insecure";
  const ready =
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof AudioContext !== "undefined" &&
    typeof AudioWorkletNode !== "undefined";
  return ready ? "ok" : "unsupported";
}

export function micProblem(error: unknown): MicProblem {
  if (error instanceof MicError) return error.problem;
  const name = error instanceof DOMException || error instanceof Error ? error.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "denied";
    case "NotFoundError":
    case "OverconstrainedError":
      return "missing";
    case "NotReadableError":
    case "AbortError":
      return "busy";
    default:
      return "other";
  }
}

export interface Clip {
  /** A 16 kHz mono 16-bit WAV file. */
  wav: Uint8Array;
  seconds: number;
}

export interface Recording {
  /** Ends the recording, releases the microphone and returns the clip. */
  stop: () => Promise<Clip>;
  /** Ends the recording and releases the microphone; nothing is kept. */
  cancel: () => void;
}

interface RecordingOptions {
  /** Called about ten times a second with the loudness, 0 to 1. */
  onLevel?: (level: number) => void;
  /** Called once when the recording reaches the limit; the owner then calls `stop`. */
  onLimit?: () => void;
  maxSeconds?: number;
}

function rms(block: Float32Array): number {
  let sum = 0;
  for (const sample of block) sum += sample * sample;
  return Math.sqrt(sum / Math.max(1, block.length));
}

/** Opens the microphone. The audio stays in memory until `stop`, and is never written anywhere. */
export async function startRecording(options: RecordingOptions = {}): Promise<Recording> {
  const support = micSupport();
  if (support !== "ok") throw new MicError(support);
  const maxSeconds = options.maxSeconds ?? MAX_SECONDS;

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (error) {
    throw new MicError(micProblem(error));
  }

  let context: AudioContext | undefined;
  let node: AudioWorkletNode | undefined;
  const chunks: Float32Array[] = [];
  let total = 0;
  let limitSent = false;
  let closed = false;

  const release = () => {
    if (closed) return;
    closed = true;
    if (node) {
      node.port.onmessage = null;
      node.disconnect();
    }
    for (const track of stream.getTracks()) track.stop();
    void context?.close();
  };

  try {
    context = new AudioContext();
    await context.resume();
    await context.audioWorklet.addModule(WORKLET_URL);
    const source = context.createMediaStreamSource(stream);
    node = new AudioWorkletNode(context, "mic-capture", { numberOfOutputs: 0 });
    const rate = context.sampleRate;
    node.port.onmessage = (event: MessageEvent<Float32Array>) => {
      if (closed) return;
      chunks.push(event.data);
      total += event.data.length;
      options.onLevel?.(Math.min(1, rms(event.data) * 4));
      if (!limitSent && total >= maxSeconds * rate) {
        limitSent = true;
        options.onLimit?.();
      }
    };
    source.connect(node);
  } catch {
    release();
    throw new MicError("other");
  }
  const rate = context.sampleRate;

  return {
    cancel: release,
    stop: () => {
      release();
      const all = new Float32Array(total);
      let at = 0;
      for (const chunk of chunks) {
        all.set(chunk, at);
        at += chunk.length;
      }
      const speech = downsample(all, rate, SPEECH_RATE).subarray(0, maxSeconds * SPEECH_RATE);
      return Promise.resolve({ wav: encodeWav(speech), seconds: speech.length / SPEECH_RATE });
    },
  };
}
