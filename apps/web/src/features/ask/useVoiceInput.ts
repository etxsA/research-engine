import { useCallback, useEffect, useRef, useState } from "react";
import { isApiError } from "@/shared/api/errors";
import {
  micProblem,
  MIN_SECONDS,
  startRecording,
  type MicProblem,
  type Recording,
} from "@/shared/lib/recorder";
import { useUiStore } from "@/shared/stores/ui-store";
import { useTranscribe } from "./api";

export type VoiceProblem =
  MicProblem | "tooShort" | "empty" | "rejected" | "overloaded" | "serverDown" | "failed";

export type VoicePhase = "idle" | "recording" | "transcribing" | "done" | "error";

export interface VoiceState {
  phase: VoicePhase;
  /** Loudness of the last moment, 0 to 1, while recording. */
  level: number;
  problem: VoiceProblem | null;
}

const IDLE: VoiceState = { phase: "idle", level: 0, problem: null };

function transcribeProblem(error: unknown): VoiceProblem {
  if (isApiError(error, 429)) return "overloaded";
  if (isApiError(error, 503)) return "serverDown";
  if (isApiError(error, 413) || isApiError(error, 422)) return "rejected";
  return "failed";
}

/**
 * Push-to-talk: the first call starts the recording, the second (or the 60 s limit) ends it and
 * sends the clip. `onText` receives the transcript; nothing is kept here.
 */
export function useVoiceInput(onText: (text: string) => void) {
  const [state, setState] = useState<VoiceState>(IDLE);
  const recording = useRef<Recording | null>(null);
  const starting = useRef(false);
  const alive = useRef(true);
  const { mutateAsync: transcribe } = useTranscribe();
  const language = useUiStore((s) => s.language);
  const onTextRef = useRef(onText);
  useEffect(() => {
    onTextRef.current = onText;
  });

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      recording.current?.cancel();
      recording.current = null;
    };
  }, []);

  const finish = useCallback(async () => {
    const active = recording.current;
    if (!active) return;
    recording.current = null;
    setState({ phase: "transcribing", level: 0, problem: null });
    try {
      const clip = await active.stop();
      if (clip.seconds < MIN_SECONDS) {
        if (alive.current) setState({ phase: "error", level: 0, problem: "tooShort" });
        return;
      }
      const text = (await transcribe({ wav: clip.wav, lang: language })).trim();
      if (!alive.current) return;
      if (text === "") {
        setState({ phase: "error", level: 0, problem: "empty" });
        return;
      }
      onTextRef.current(text);
      setState({ phase: "done", level: 0, problem: null });
    } catch (error) {
      if (alive.current) setState({ phase: "error", level: 0, problem: transcribeProblem(error) });
    }
  }, [transcribe, language]);

  const toggle = useCallback(async () => {
    if (recording.current) {
      await finish();
      return;
    }
    if (starting.current || state.phase === "transcribing") return;
    starting.current = true;
    try {
      const started = await startRecording({
        onLevel: (level) => {
          if (alive.current) setState({ phase: "recording", level, problem: null });
        },
        onLimit: () => void finish(),
      });
      if (!alive.current) {
        started.cancel();
        return;
      }
      recording.current = started;
      setState({ phase: "recording", level: 0, problem: null });
    } catch (error) {
      if (alive.current) setState({ phase: "error", level: 0, problem: micProblem(error) });
    } finally {
      starting.current = false;
    }
  }, [finish, state.phase]);

  return { state, toggle };
}
