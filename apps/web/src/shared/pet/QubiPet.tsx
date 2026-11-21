import { useEffect, useRef, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { playbackLevel, usePlayback } from "@/shared/voice/playback";
import { usePetPrefs } from "./hidden";
import { petState, type PetInput } from "./petState";
import { Qubi } from "./Qubi";

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReduced(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReduced,
    () => window.matchMedia(REDUCED_QUERY).matches,
    () => false,
  );
}

interface QubiPetProps extends Omit<PetInput, "playback"> {
  /** Loudness of the microphone while recording, 0 to 1. */
  micLevel: number;
  /** Asks ahead of the newest question while it is queued. */
  position: number | null;
}

/**
 * Qubi with its "Hide Qubi" button, and a line for screen readers that says what it is doing.
 * The screen passes in what it knows; the reader's state comes from the shared playback store.
 */
export function QubiPet({ micLevel, position, ...signals }: QubiPetProps) {
  const { t } = useTranslation(undefined, { keyPrefix: "pet" });
  const hidden = usePetPrefs((prefs) => prefs.hidden);
  const setHidden = usePetPrefs((prefs) => prefs.setHidden);
  const playback = usePlayback((store) => store.state);
  const reduced = usePrefersReducedMotion();
  const stage = useRef<HTMLDivElement>(null);
  const state = petState({ ...signals, playback });
  const visible = !hidden;

  // The microphone level reaches the rings through a CSS variable; no frame is drawn by React.
  useEffect(() => {
    stage.current?.style.setProperty(
      "--qubi-level",
      state === "listening" ? String(micLevel) : "0",
    );
  }, [micLevel, state, visible]);

  // While speaking, the mouth follows the loudness, one frame at a time, outside React.
  useEffect(() => {
    const node = stage.current;
    if (!node || !visible || state !== "speaking") return;
    if (reduced) {
      node.style.setProperty("--qubi-mouth", "0.6");
      return;
    }
    let frame = 0;
    const tick = () => {
      node.style.setProperty("--qubi-mouth", playbackLevel.current.toFixed(3));
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => {
      window.cancelAnimationFrame(frame);
      node.style.removeProperty("--qubi-mouth");
    };
  }, [state, reduced, visible]);

  if (!visible) {
    return (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-auto justify-self-start px-2 py-1 text-xs"
        onClick={() => setHidden(false)}
      >
        {t("show")}
      </Button>
    );
  }
  return (
    <div className="grid w-20 shrink-0 justify-items-center gap-1">
      <div ref={stage}>
        <Qubi state={state} still={reduced} position={position} />
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {t(`state.${state}`)}
      </p>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-auto px-1 py-0.5 text-center text-xs leading-tight text-muted-foreground"
        onClick={() => setHidden(true)}
      >
        {t("hide")}
      </Button>
    </div>
  );
}
