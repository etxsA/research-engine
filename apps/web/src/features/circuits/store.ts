import { create } from "zustand";
import type { ApiError } from "@/shared/api/errors";
import type { CopyOutcome } from "@/shared/lib/clipboard";
import type { PaperCircuits, PaperExport, QasmExport, Reading, SimulatorResult } from "./types";

export type InputKind = "latex" | "json";
export type TargetKind = "state" | "unitary" | "circuit" | "drawn";
export type Busy = "read" | "run" | "check" | "qasm" | "paperCircuits" | "paperQasm" | null;

/** Why the screen did not send a request, or could not show an answer. Each has a translated message. */
export type LocalCode =
  | "emptyInput"
  | "invalidJson"
  | "duplicateKey"
  | "notACircuit"
  | "notRead"
  | "badValues"
  | "badName"
  | "badState"
  | "badUnitary"
  | "emptyTarget"
  | "secondUnread"
  | "secondMany"
  | "noPaper"
  | "unknownPaper"
  | "network";

export type Problem =
  | { kind: "api"; error: ApiError }
  | { kind: "malformed" }
  | { kind: "local"; code: LocalCode; params: Record<string, string | number> };

export interface Outcome {
  /** Different for every answer, so a view can start fresh when a new answer arrives. */
  serial: number;
  command: "run" | "check";
  result: SimulatorResult;
  /** The index of the circuit that was sent, in the reading. */
  circuit: number;
  target: TargetKind | null;
}

/** The OpenQASM export of the pasted circuit. */
export interface QasmOutcome {
  serial: number;
  export: QasmExport;
  /** The index of the circuit that was sent, in the reading. */
  circuit: number;
}

/** The OpenQASM export of a stored paper. */
export interface PaperExportOutcome {
  serial: number;
  export: PaperExport;
}

/** What a screen reader is told when a reading or an answer arrives. The text is made when it is shown. */
export type Announcement =
  | { kind: "reading"; status: Reading["status"]; found: number }
  | { kind: "run" | "check" | "equal" | "notEqual" }
  | { kind: "qasm"; programs: number }
  | { kind: "paperCircuits"; blocks: number }
  | { kind: "paperQasm"; programs: number; refused: number }
  | { kind: "copy"; outcome: CopyOutcome; file: string };

/** An announcement with a number that grows with every one, so the same words twice are two events. */
export type NumberedAnnouncement = Announcement & { serial: number };

let outcomeSerial = 0;
let announcementSerial = 0;

/** The announcement for a reading or an answer, numbered. */
export function announce(announcement: Announcement): NumberedAnnouncement {
  announcementSerial += 1;
  return { serial: announcementSerial, ...announcement };
}

/** What to say about an answer; a refusal is announced by its own alert, so it says nothing. */
export function announcementFor(outcome: Outcome): Announcement | null {
  const { result } = outcome;
  if (result.status !== "ok") return null;
  if (outcome.command === "run") return { kind: "run" };
  if (result.equalToTarget === null) return { kind: "check" };
  return { kind: result.equalToTarget ? "equal" : "notEqual" };
}

/** An outcome with its own serial number. */
export function makeOutcome(outcome: Omit<Outcome, "serial">): Outcome {
  outcomeSerial += 1;
  return { serial: outcomeSerial, ...outcome };
}

/** A pasted circuit's export with its own serial number. */
export function makeQasmOutcome(outcome: Omit<QasmOutcome, "serial">): QasmOutcome {
  outcomeSerial += 1;
  return { serial: outcomeSerial, ...outcome };
}

/** A paper's export with its own serial number. */
export function makePaperExport(exported: PaperExport): PaperExportOutcome {
  outcomeSerial += 1;
  return { serial: outcomeSerial, export: exported };
}

export interface CircuitState {
  inputKind: InputKind;
  /** The Paper tab is open. The circuit read stays the one of `inputKind`: Simulate, Check and Export use it. */
  showPaper: boolean;
  latexText: string;
  jsonText: string;
  reading: Reading | null;
  selected: number;
  inputState: string;
  valuesText: string;
  unitary: boolean;
  targetKind: TargetKind;
  targetText: string;
  outcome: Outcome | null;
  /** The export of the pasted circuit; it depends on the circuit, the one chosen and the values. */
  qasm: QasmOutcome | null;
  /** The stored paper chosen (its full id, or a prefix of 16 or more hex characters). */
  paperId: string;
  /** The circuit blocks of that paper, once listed. */
  paperCircuits: PaperCircuits | null;
  /** The export of that paper; it depends on the paper and the values. */
  paperExport: PaperExportOutcome | null;
  /**
   * The last thing announced. Editing the screen does not clear it: a new reading or answer
   * replaces it, and a refusal or a problem clears it, so the hidden text stays true.
   */
  announcement: NumberedAnnouncement | null;
  problem: Problem | null;
  busy: Busy;
  /** Bumped by every change of what a request depends on, so an answer to an older screen is dropped. */
  revision: number;
  /** Bumped only when the circuit text changes: a reading stays valid while the options are edited. */
  textRevision: number;
  /** Bumped when the values change: the exports depend on them, the paper's blocks do not. */
  valuesRevision: number;
  /** Bumped when another paper is chosen. */
  paperRevision: number;
}

interface CircuitActions {
  setInputKind: (kind: InputKind) => void;
  setShowPaper: (show: boolean) => void;
  /** Put LaTeX from elsewhere (a paper's block, a link) in the LaTeX tab and open that tab. */
  loadLatex: (text: string) => void;
  setText: (text: string) => void;
  loadExample: (kind: InputKind, text: string) => void;
  selectCircuit: (index: number) => void;
  setInputState: (text: string) => void;
  setValuesText: (text: string) => void;
  setUnitary: (value: boolean) => void;
  setTargetKind: (kind: TargetKind) => void;
  setTargetText: (text: string) => void;
  setPaper: (docId: string) => void;
  /** Say what a Copy button did: copied, or only selected the text when the browser refused. */
  announceCopy: (outcome: CopyOutcome, file: string) => void;
  clear: () => void;
}

export const initialCircuitState: CircuitState = {
  inputKind: "latex",
  showPaper: false,
  latexText: "",
  jsonText: "",
  reading: null,
  selected: 0,
  inputState: "",
  valuesText: "",
  unitary: false,
  targetKind: "state",
  targetText: "",
  outcome: null,
  qasm: null,
  paperId: "",
  paperCircuits: null,
  paperExport: null,
  announcement: null,
  problem: null,
  busy: null,
  revision: 0,
  textRevision: 0,
  valuesRevision: 0,
  paperRevision: 0,
};

/** What a request depends on changed: the old answer and problem no longer describe the screen. */
const stale = (state: CircuitState) => ({
  outcome: null,
  problem: null,
  revision: state.revision + 1,
});

/** The circuit itself changed: the reading goes too, and the export of the pasted circuit. */
const staleText = (state: CircuitState) => ({
  reading: null,
  selected: 0,
  qasm: null,
  ...stale(state),
  textRevision: state.textRevision + 1,
});

/** Client-only state of the circuit screen: what was typed, what was read, the last answer. Memory only. */
export const useCircuitStore = create<CircuitState & CircuitActions>()((set) => ({
  ...initialCircuitState,
  setInputKind: (inputKind) => set((s) => ({ inputKind, showPaper: false, ...staleText(s) })),
  setShowPaper: (showPaper) => set({ showPaper }),
  loadLatex: (text) =>
    set((s) => ({ inputKind: "latex", showPaper: false, latexText: text, ...staleText(s) })),
  setText: (text) =>
    set((s) => ({
      ...(s.inputKind === "latex" ? { latexText: text } : { jsonText: text }),
      ...staleText(s),
    })),
  loadExample: (kind, text) =>
    set((s) => ({
      inputKind: kind,
      ...(kind === "latex" ? { latexText: text } : { jsonText: text }),
      targetKind: "state",
      ...staleText(s),
    })),
  selectCircuit: (selected) => set((s) => ({ selected, qasm: null, ...stale(s) })),
  setInputState: (inputState) => set((s) => ({ inputState, ...stale(s) })),
  setValuesText: (valuesText) =>
    set((s) => ({
      valuesText,
      qasm: null,
      paperExport: null,
      ...stale(s),
      valuesRevision: s.valuesRevision + 1,
    })),
  setUnitary: (unitary) => set((s) => ({ unitary, ...stale(s) })),
  setTargetKind: (targetKind) => set((s) => ({ targetKind, ...stale(s) })),
  setTargetText: (targetText) => set((s) => ({ targetText, ...stale(s) })),
  setPaper: (paperId) =>
    set((s) => ({
      paperId,
      paperCircuits: null,
      paperExport: null,
      problem: null,
      paperRevision: s.paperRevision + 1,
    })),
  announceCopy: (outcome, file) => set({ announcement: announce({ kind: "copy", outcome, file }) }),
  clear: () =>
    set((s) => ({
      ...initialCircuitState,
      revision: s.revision + 1,
      textRevision: s.textRevision + 1,
      valuesRevision: s.valuesRevision + 1,
      paperRevision: s.paperRevision + 1,
    })),
}));

export function currentText(state: Pick<CircuitState, "inputKind" | "latexText" | "jsonText">) {
  return state.inputKind === "latex" ? state.latexText : state.jsonText;
}
