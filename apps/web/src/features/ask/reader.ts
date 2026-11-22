import { fetchClient } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import { playbackLevel, usePlayback } from "@/shared/voice/playback";
import {
  CHUNK_CHARS,
  chunkText,
  LOCALE,
  pickVoice,
  readingPlan,
  splitSentences,
  type SpeechLanguage,
} from "./speakable";

/**
 * Reads an answer aloud. With the speech server (`/status` speech.tts ready) each sentence is
 * synthesized by `POST /speech/synthesis` and played with Web Audio, the next one being fetched
 * while one plays; without it, or when the server is down, the browser's own voices speak
 * (`speechSynthesis`). One answer speaks at a time: starting another stops the first. Nothing is
 * stored. The speech server is our own API; the voices of the browser are the browser's, and some
 * of them (a network voice) send the text to the browser vendor, which this code cannot see.
 */

export type Engine = "server" | "browser";
/** What went wrong, when the reading could not go on. */
export type ReadProblem = "busy" | "failed";

export interface ReaderState {
  /** The key of the answer being read, or null. */
  owner: string | null;
  /** Which voice is reading it. */
  engine: Engine;
  /** The first sentence has not started yet. */
  loading: boolean;
  /** The speech server was down, so the browser's voice took over. */
  fellBack: boolean;
  /** The answer whose reading broke off by itself, and why. */
  failed: { owner: string; problem: ReadProblem } | null;
}

const IDLE: ReaderState = {
  owner: null,
  engine: "server",
  loading: false,
  fellBack: false,
  failed: null,
};
const VOICES_WAIT_MS = 1500;
/** A context that is not running this soon after `resume()` is waiting for a click (Safari). */
const RESUME_WAIT_MS = 500;
/** The RMS of speech is about 0.05 to 0.2; the bar of the pet wants 0 to 1. */
const LEVEL_GAIN = 4;

let state: ReaderState = IDLE;
let run = 0; // identity of the current reading; a stale callback sees another number and stops
const listeners = new Set<() => void>();

/** What the current reading holds, so a stop can let go of all of it. */
interface Session {
  abort: AbortController;
  context: AudioContext | null;
  source: AudioBufferSourceNode | null;
  frame: number | null;
  browser: boolean;
}
let session: Session | null = null;

/** Told of an error that means the session ended, so the app can send the person to sign in. */
let sessionEnded: ((error: unknown) => void) | null = null;

export function onReadSessionEnded(handler: ((error: unknown) => void) | null): void {
  sessionEnded = handler;
}

export function serverSupported(): boolean {
  return typeof AudioContext !== "undefined";
}

export function browserSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "speechSynthesis" in window &&
    typeof SpeechSynthesisUtterance !== "undefined"
  );
}

/** Whether there is a voice to read with, for the engine the status asks for. */
export function readerSupported(engine: Engine): boolean {
  return (engine === "server" && serverSupported()) || browserSupported();
}

export function getReaderState(): ReaderState {
  return state;
}

export function subscribeReader(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function setState(next: ReaderState) {
  state = next;
  for (const listener of [...listeners]) listener();
}

function setPlayback(next: "idle" | "speaking") {
  if (usePlayback.getState().state !== next) usePlayback.getState().setState(next);
}

/** Lets go of everything the reading held. */
function release(held: Session | null) {
  if (!held) return;
  held.abort.abort();
  if (held.frame !== null) cancelAnimationFrame(held.frame);
  if (held.source) {
    held.source.onended = null;
    try {
      held.source.stop();
    } catch {
      // it had not started
    }
  }
  if (held.context) void held.context.close().catch(() => undefined);
  if (held.browser && browserSupported()) window.speechSynthesis.cancel();
}

function onPageHide() {
  stopReading();
}

export function stopReading(): void {
  run++;
  const held = session;
  session = null;
  release(held);
  window.removeEventListener("pagehide", onPageHide);
  playbackLevel.current = 0;
  setPlayback("idle");
  if (state.owner !== null || state.failed !== null || state.fellBack) setState(IDLE);
}

/** The reading ended by itself, or broke off: nothing is playing any more. */
function finish(mine: number, next: ReaderState = IDLE) {
  if (mine !== run) return;
  run++;
  const held = session;
  session = null;
  release(held);
  window.removeEventListener("pagehide", onPageHide);
  playbackLevel.current = 0;
  setPlayback("idle");
  setState(next);
}

function breakOff(mine: number, owner: string, problem: ReadProblem) {
  finish(mine, { ...IDLE, failed: { owner, problem } });
}

/** A gentle pulse for the voices of the browser, which give no audio to measure. */
function pulse(held: Session) {
  const tick = (now: number) => {
    playbackLevel.current = 0.3 + 0.2 * Math.sin(now / 130);
    held.frame = requestAnimationFrame(tick);
  };
  held.frame = requestAnimationFrame(tick);
}

/** Voices load by themselves in some browsers: wait for `voiceschanged`, but not for ever. */
function loadVoices(synth: SpeechSynthesis): Promise<SpeechSynthesisVoice[]> {
  const now = synth.getVoices();
  if (now.length > 0) return Promise.resolve(now);
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      synth.removeEventListener("voiceschanged", done);
      resolve(synth.getVoices());
    };
    const timer = setTimeout(done, VOICES_WAIT_MS);
    synth.addEventListener("voiceschanged", done);
  });
}

async function readWithBrowser(
  mine: number,
  owner: string,
  text: string,
  language: SpeechLanguage,
  fellBack: boolean,
): Promise<void> {
  const held = session;
  if (!held || !browserSupported()) return breakOff(mine, owner, "failed");
  held.browser = true;
  const synth = window.speechSynthesis;
  setState({ owner, engine: "browser", loading: true, fellBack, failed: null });
  const voices = await loadVoices(synth);
  if (mine !== run) return;
  const chunks = chunkText(text, CHUNK_CHARS);
  if (chunks.length === 0) return finish(mine);
  const voice = pickVoice(voices, language);
  const last = chunks.length - 1;
  const fail = () => breakOff(mine, owner, "failed");
  try {
    chunks.forEach((chunk, index) => {
      const utterance = new SpeechSynthesisUtterance(chunk);
      utterance.lang = voice?.lang ?? LOCALE[language];
      if (voice) utterance.voice = voice;
      utterance.onend = () => {
        if (index === last) finish(mine);
      };
      utterance.onerror = (event) => {
        // A stop (`cancel`) reports "canceled" or "interrupted": that is not a failure.
        if (event.error === "canceled" || event.error === "interrupted") return;
        fail();
      };
      synth.speak(utterance);
    });
  } catch {
    return fail();
  }
  setState({ owner, engine: "browser", loading: false, fellBack, failed: null });
  setPlayback("speaking");
  pulse(held);
}

/** One sentence to audio: the API's WAV, decoded. */
async function synthesize(
  context: AudioContext,
  text: string,
  language: SpeechLanguage,
  signal: AbortSignal,
): Promise<AudioBuffer> {
  const { data } = await fetchClient.POST("/api/v1/speech/synthesis", {
    body: { text, language },
    parseAs: "arrayBuffer",
    signal,
  });
  if (!data) throw new Error("The API answered without audio.");
  return context.decodeAudioData(data);
}

/** Plays `buffer` through the analyser; resolves when it ends (or is stopped). */
function play(held: Session, context: AudioContext, analyser: AnalyserNode, buffer: AudioBuffer) {
  return new Promise<void>((resolve) => {
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser);
    source.onended = () => resolve();
    held.source = source;
    source.start();
  });
}

/** Measures the loudness of what plays, every frame, into `playbackLevel`. */
function measure(held: Session, analyser: AnalyserNode) {
  const samples = new Float32Array(analyser.fftSize);
  const tick = () => {
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) sum += sample * sample;
    playbackLevel.current = Math.min(1, Math.sqrt(sum / samples.length) * LEVEL_GAIN);
    held.frame = requestAnimationFrame(tick);
  };
  held.frame = requestAnimationFrame(tick);
}

/** Starts the context and waits a moment for it to run; a context that needs a click never does. */
async function resumed(context: AudioContext): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const wait = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, RESUME_WAIT_MS);
  });
  await Promise.race([context.resume().catch(() => undefined), wait]);
  clearTimeout(timer);
  return context.state === "running";
}

async function readWithServer(
  mine: number,
  owner: string,
  text: string,
  language: SpeechLanguage,
): Promise<void> {
  const held = session;
  if (!held) return;
  const sentences = splitSentences(text);
  const context = new AudioContext();
  held.context = context;
  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;
  analyser.connect(context.destination);
  setState({ owner, engine: "server", loading: true, fellBack: false, failed: null });

  // A sentence is requested once; a failure waits in its promise until that sentence is needed.
  const requests = new Map<number, Promise<AudioBuffer>>();
  const request = (index: number): Promise<AudioBuffer> | undefined => {
    const sentence = sentences[index];
    if (sentence === undefined) return undefined;
    let pending = requests.get(index);
    if (!pending) {
      pending = synthesize(context, sentence, language, held.abort.signal);
      pending.catch(() => undefined);
      requests.set(index, pending);
    }
    return pending;
  };

  void request(0); // fetched while the context starts
  if (!(await resumed(context))) {
    // Started outside a click (an answer read by itself), the browser may keep it silent.
    if (mine !== run) return;
    return breakOff(mine, owner, "failed");
  }
  if (mine !== run) return;

  let played = 0;
  let skipped = 0;
  for (let index = 0; index < sentences.length; index++) {
    let buffer: AudioBuffer;
    try {
      buffer = await request(index)!;
    } catch (error) {
      if (mine !== run) return;
      if (isApiError(error, 401)) {
        sessionEnded?.(error);
        return finish(mine);
      }
      // Our cleaning and the server's are not the same: a sentence it refuses is left out.
      if (isApiError(error, 422)) {
        skipped++;
        continue;
      }
      if (isApiError(error, 429)) return breakOff(mine, owner, "busy");
      if (isApiError(error, 503) && browserSupported()) {
        // The speech server is down: the browser's voice goes on from this sentence, once.
        release(held);
        const next: Session = {
          abort: new AbortController(),
          context: null,
          source: null,
          frame: null,
          browser: true,
        };
        session = next;
        return readWithBrowser(mine, owner, sentences.slice(index).join(" "), language, true);
      }
      return breakOff(mine, owner, "failed");
    }
    if (mine !== run) return;
    if (played === 0) {
      setState({ owner, engine: "server", loading: false, fellBack: false, failed: null });
      setPlayback("speaking");
      measure(held, analyser);
    }
    void request(index + 1); // fetched while this one plays
    played++;
    await play(held, context, analyser, buffer);
    if (mine !== run) return;
  }
  if (played === 0 && skipped > 0) return breakOff(mine, owner, "failed");
  finish(mine);
}

/**
 * Starts reading `answer` for the entry `owner`, after stopping whatever was being read. The
 * engine is the one `/status` asks for; a browser without Web Audio uses its own voice instead.
 */
export function startReading(owner: string, answer: string, engine: Engine): void {
  stopReading();
  const plan = readingPlan(answer);
  if (plan.text === "" || !readerSupported(engine)) return;
  const mine = run;
  session = {
    abort: new AbortController(),
    context: null,
    source: null,
    frame: null,
    browser: false,
  };
  window.addEventListener("pagehide", onPageHide);
  const useServer = engine === "server" && serverSupported();
  const reading = useServer
    ? readWithServer(mine, owner, plan.text, plan.language)
    : readWithBrowser(mine, owner, plan.text, plan.language, false);
  reading.catch(() => breakOff(mine, owner, "failed"));
}
