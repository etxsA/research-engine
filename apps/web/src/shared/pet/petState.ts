import type { PlaybackState } from "@/shared/voice/playback";

export type PetState =
  | "idle"
  | "listening"
  | "transcribing"
  | "waiting"
  | "thinking"
  | "speaking"
  | "error"
  | "unavailable";

export const PET_STATES: readonly PetState[] = [
  "idle",
  "listening",
  "transcribing",
  "waiting",
  "thinking",
  "speaking",
  "error",
  "unavailable",
];

export type PetMicPhase = "idle" | "recording" | "transcribing" | "done" | "error";
export type PetAskStatus =
  "sending" | "queued" | "retrieving" | "answering" | "done" | "failed" | "cancelled";

/** The signals the screen hands to the pet. Each one is a plain value, so the mapping stays testable. */
export interface PetInput {
  mic: PetMicPhase;
  /** Status of the newest question, or null when there is none. */
  ask: PetAskStatus | null;
  playback: PlaybackState;
  /** The engine reports it cannot answer. */
  unavailable: boolean;
}

/**
 * One state out of everything that may be going on. When several apply the order is
 * error, speaking, listening, transcribing, thinking, waiting, then unavailable, then idle.
 */
export function petState(input: PetInput): PetState {
  if (input.mic === "error" || input.ask === "failed") return "error";
  if (input.playback === "speaking") return "speaking";
  if (input.mic === "recording") return "listening";
  if (input.mic === "transcribing") return "transcribing";
  if (input.ask === "retrieving" || input.ask === "answering") return "thinking";
  if (input.ask === "queued") return "waiting";
  if (input.unavailable) return "unavailable";
  return "idle";
}
