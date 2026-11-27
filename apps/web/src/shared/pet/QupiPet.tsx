import { useEffect, useRef, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { playbackLevel, usePlayback } from "@/shared/voice/playback";
import { usePetPrefs } from "./hidden";
import { mouthFor, type MouthShape } from "./mouth";
import { shake, squash, twitchEar } from "./moves";
import { petState, type PetInput, type PetState } from "./petState";
import { Qupi } from "./Qupi";

const BLINK_MS = 140;
const BLINK_SLOW_MS = 420;
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

interface QupiPetProps extends Omit<PetInput, "playback"> {
  /** Loudness of the microphone while recording, 0 to 1. */
  micLevel: number;
  /** Asks ahead of the newest question while it is queued. */
  position: number | null;
}

/**
 * Qupi with its "Hide Qupi" button, and a line for screen readers that says what it is doing.
 * The screen passes in what it knows; the reader's state comes from the shared playback store.
 */
export function QupiPet({ micLevel, position, ...signals }: QupiPetProps) {
  const { t } = useTranslation(undefined, { keyPrefix: "pet" });
  const hidden = usePetPrefs((prefs) => prefs.hidden);
  const setHidden = usePetPrefs((prefs) => prefs.setHidden);
  const playback = usePlayback((store) => store.state);
  const reduced = usePrefersReducedMotion();
  const stage = useRef<HTMLDivElement>(null);
  const state = petState({ ...signals, playback });
  const visible = !hidden;

  // The microphone level reaches the glow of the mark through a CSS variable; no frame is drawn by React.
  useEffect(() => {
    stage.current?.style.setProperty(
      "--qupi-level",
      state === "listening" && !reduced ? String(micLevel) : "0",
    );
  }, [micLevel, state, visible, reduced]);

  // While speaking, the mouth follows the loudness, one frame at a time, outside React. The loop
  // writes the level and, only when the shape changes, `data-mouth` (see mouth.ts).
  useEffect(() => {
    const node = stage.current;
    if (!node || !visible || state !== "speaking") return;
    const clear = () => {
      node.style.removeProperty("--qupi-mouth");
      delete node.dataset.mouth;
    };
    if (reduced) {
      node.style.setProperty("--qupi-mouth", "0.6");
      node.dataset.mouth = "half";
      return clear;
    }
    let frame = 0;
    const tick = () => {
      const level = playbackLevel.current;
      const value = level.toFixed(3);
      if (node.style.getPropertyValue("--qupi-mouth") !== value) {
        node.style.setProperty("--qupi-mouth", value);
      }
      const shape = mouthFor(level, node.dataset.mouth as MouthShape | undefined);
      if (node.dataset.mouth !== shape) node.dataset.mouth = shape;
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => {
      window.cancelAnimationFrame(frame);
      clear();
    };
  }, [state, reduced, visible]);

  // The eyelids close now and then in every state with open eyes: every 3 to 6 s, slower and rarer while waiting.
  useEffect(() => {
    const lids = stage.current?.querySelector<HTMLElement>(".qupi-l-eyelids");
    if (!lids || !visible || reduced || state === "unavailable") return;
    const slow = state === "waiting";
    let timer = 0;
    const schedule = () => {
      const wait = slow ? 5000 + Math.random() * 3000 : 3000 + Math.random() * 3000;
      timer = window.setTimeout(() => {
        lids.dataset.blink = "true";
        timer = window.setTimeout(
          () => {
            delete lids.dataset.blink;
            schedule();
          },
          slow ? BLINK_SLOW_MS : BLINK_MS,
        );
      }, wait);
    };
    schedule();
    return () => {
      window.clearTimeout(timer);
      delete lids.dataset.blink;
    };
  }, [state, reduced, visible]);

  // One-shot moves when the state changes: squash and stretch, the ear twitch, the shake.
  const before = useRef<PetState | null>(null);
  useEffect(() => {
    const node = stage.current;
    const previous = before.current;
    before.current = visible ? state : null;
    if (!node || !visible || reduced || previous === null || previous === state) return;
    squash(node.querySelector(".qupi-rig"), state);
    if (state === "listening") {
      twitchEar(node.querySelector(".qupi-l-ear-left"), -1);
      twitchEar(node.querySelector(".qupi-l-ear-right"), 1);
    }
    if (state === "error") shake(node.querySelector(".qupi-rig"));
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
    <div className="grid w-20 shrink-0 justify-items-center gap-1 sm:w-[120px]">
      <div ref={stage}>
        <Qupi state={state} still={reduced} position={position} />
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
