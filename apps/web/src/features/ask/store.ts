import { create } from "zustand";
import type { ApiError } from "@/shared/api/errors";
import { onSessionEnd } from "@/shared/lib/session";
import type { AskFailure, AskResponse, KeywordMode } from "./types";

/** Answers hold text of the papers: they live in this tab's memory and nowhere else (D-197). */
export const MAX_ENTRIES = 10;

/** The steps a question goes through, in order. */
export type Progress = "sending" | "queued" | "retrieving" | "answering";
export type EntryStatus = Progress | "done" | "failed" | "cancelled";

const RANK: Record<Progress, number> = { sending: 0, queued: 1, retrieving: 2, answering: 3 };

export function isActive(status: EntryStatus): status is Progress {
  return status in RANK;
}

/** Why a question has no answer. Each has a translated message on the screen. */
export type Failure =
  | { kind: "server"; failure: AskFailure }
  | { kind: "api"; error: ApiError }
  | { kind: "network" }
  | { kind: "lost" }
  | { kind: "malformed" };

/** The options of one question. `null` means the server's own setting. */
export interface AskSettings {
  useIndex: boolean;
  bm25: KeywordMode | null;
  wholeBlocks: boolean | null;
}

export const DEFAULT_SETTINGS: AskSettings = { useIndex: true, bm25: null, wholeBlocks: null };

export function changedSettings(settings: AskSettings): number {
  return (
    Number(settings.useIndex !== DEFAULT_SETTINGS.useIndex) +
    Number(settings.bm25 !== DEFAULT_SETTINGS.bm25) +
    Number(settings.wholeBlocks !== DEFAULT_SETTINGS.wholeBlocks)
  );
}

export interface AskEntry {
  /** Local identity, before and after the server gives the ask an id. */
  key: string;
  askId: string | null;
  question: string;
  settings: AskSettings;
  status: EntryStatus;
  /** Asks ahead of this one while it is queued. */
  position: number | null;
  response: AskResponse | null;
  failure: Failure | null;
  /** The question came from the Speak button, so its answer is read aloud when that is on. */
  spoken: boolean;
}

/** A paper page open in the side panel. */
export interface SourceTarget {
  docId: string;
  page: number;
  /** The cited range the panel was opened for. */
  first: number;
  last: number;
  /** Names the element that opened it, so focus can return there when it closes. */
  opener: string;
}

interface AskState {
  question: string;
  /** The question box holds words from the Speak button (maybe edited). Emptying the box clears it. */
  spoken: boolean;
  settings: AskSettings;
  /** Newest first, at most MAX_ENTRIES. */
  entries: AskEntry[];
  selectedKey: string | null;
  source: SourceTarget | null;
}

interface AskActions {
  setQuestion: (question: string) => void;
  /** Adds dictated words to the box. */
  speak: (question: string) => void;
  setSettings: (settings: Partial<AskSettings>) => void;
  resetSettings: () => void;
  begin: (question: string, settings: AskSettings, spoken?: boolean) => string;
  accepted: (key: string, askId: string, position: number) => void;
  advance: (key: string, progress: Progress, position?: number) => void;
  succeed: (key: string, response: AskResponse) => void;
  fail: (key: string, failure: Failure) => void;
  cancelled: (key: string) => void;
  select: (key: string | null) => void;
  openSource: (target: SourceTarget) => void;
  moveSource: (page: number) => void;
  closeSource: () => void;
  reset: () => void;
}

const initialState: AskState = {
  question: "",
  spoken: false,
  settings: DEFAULT_SETTINGS,
  entries: [],
  selectedKey: null,
  source: null,
};

let counter = 0;

/** The stream of the question being answered. It belongs to the store, not to a screen, so leaving the screen does not lose the answer. */
let activeStream: { close: () => void } | null = null;

export function setActiveStream(stream: { close: () => void } | null): void {
  activeStream?.close();
  activeStream = stream;
}

/** Forgets `stream` if it is the registered one (it has ended by itself and closes itself). */
export function releaseStream(stream: { close: () => void }): void {
  if (activeStream === stream) activeStream = null;
}

function update(entries: AskEntry[], key: string, change: Partial<AskEntry>): AskEntry[] {
  return entries.map((entry) => (entry.key === key ? { ...entry, ...change } : entry));
}

/** A question that is already over is never moved back to a step by a late event. */
function stillActive(entries: AskEntry[], key: string): AskEntry | undefined {
  const entry = entries.find((candidate) => candidate.key === key);
  return entry && isActive(entry.status) ? entry : undefined;
}

export const useAskStore = create<AskState & AskActions>()((set) => ({
  ...initialState,
  setQuestion: (question) =>
    set((state) => ({ question, spoken: question.trim() === "" ? false : state.spoken })),
  speak: (question) => set({ question, spoken: question.trim() !== "" }),
  setSettings: (settings) => set((state) => ({ settings: { ...state.settings, ...settings } })),
  resetSettings: () => set({ settings: DEFAULT_SETTINGS }),
  begin: (question, settings, spoken = false) => {
    counter += 1;
    const key = `q${counter}`;
    const entry: AskEntry = {
      key,
      askId: null,
      question,
      settings,
      status: "sending",
      position: null,
      response: null,
      failure: null,
      spoken,
    };
    set((state) => ({
      entries: [entry, ...state.entries].slice(0, MAX_ENTRIES),
      selectedKey: key,
    }));
    return key;
  },
  accepted: (key, askId, position) =>
    set((state) =>
      stillActive(state.entries, key)
        ? { entries: update(state.entries, key, { askId, status: "queued", position }) }
        : {},
    ),
  advance: (key, progress, position) =>
    set((state) => {
      const entry = stillActive(state.entries, key);
      if (!entry || !isActive(entry.status) || RANK[progress] < RANK[entry.status]) return {};
      // A place in the line only moves up. A stream that reopens replays from the first event,
      // and its old `queued` must not put the question back behind the ones it has passed.
      if (
        progress === "queued" &&
        entry.status === "queued" &&
        position !== undefined &&
        entry.position !== null &&
        position > entry.position
      ) {
        return {};
      }
      return {
        entries: update(state.entries, key, {
          status: progress,
          position: progress === "queued" ? (position ?? entry.position) : null,
        }),
      };
    }),
  succeed: (key, response) =>
    set((state) =>
      stillActive(state.entries, key)
        ? {
            entries: update(state.entries, key, {
              status: "done",
              response,
              position: null,
              failure: null,
            }),
          }
        : {},
    ),
  fail: (key, failure) =>
    set((state) =>
      stillActive(state.entries, key)
        ? { entries: update(state.entries, key, { status: "failed", failure, position: null }) }
        : {},
    ),
  cancelled: (key) =>
    set((state) =>
      stillActive(state.entries, key)
        ? { entries: update(state.entries, key, { status: "cancelled", position: null }) }
        : {},
    ),
  select: (selectedKey) => set({ selectedKey }),
  openSource: (source) => set({ source }),
  moveSource: (page) => set((state) => (state.source ? { source: { ...state.source, page } } : {})),
  closeSource: () => set({ source: null }),
  reset: () => {
    setActiveStream(null);
    set({ ...initialState });
  },
}));

/** The question being answered right now, if any. */
export function activeEntry(entries: AskEntry[]): AskEntry | undefined {
  return entries.find((entry) => isActive(entry.status));
}

// Nothing of a session stays in memory after it ends.
onSessionEnd(() => {
  useAskStore.getState().reset();
});
