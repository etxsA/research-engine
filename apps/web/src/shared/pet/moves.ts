import type { PetState } from "./petState";

/**
 * One-shot moves, played with the Web Animations API on a single element so that no class has to
 * be added and removed. They do nothing where the API is missing (old test browsers).
 */
function play(
  node: Element | null | undefined,
  keyframes: Keyframe[],
  options: KeyframeAnimationOptions,
) {
  if (!node || typeof node.animate !== "function") return;
  node.animate(keyframes, { fill: "none", ...options });
}

/** Squash and stretch on a change of state: crouch (anticipation), spring up, settle (overshoot). */
export function squash(node: Element | null | undefined, state: PetState) {
  const lively = state === "speaking" || state === "listening" || state === "thinking";
  const crouch = lively ? "scale(1.04, 0.93)" : "scale(1.02, 0.97)";
  const spring = lively ? "scale(0.97, 1.05)" : "scale(0.99, 1.02)";
  play(
    node,
    [
      { transform: "scale(1, 1)", offset: 0 },
      { transform: crouch, offset: 0.25, easing: "ease-out" },
      { transform: spring, offset: 0.6, easing: "ease-in-out" },
      { transform: "scale(1, 1)", offset: 1 },
    ],
    { duration: 420 },
  );
}

/**
 * The ears flick once when Qupi starts to listen. `side` is -1 for the left ear, 1 for the right.
 * The peak adds to the 4 degree listening pose, so it stays at 4 to keep the total within the art's 8.
 */
export function twitchEar(node: Element | null | undefined, side: -1 | 1) {
  play(
    node,
    [
      { transform: "rotate(0deg)" },
      { transform: `rotate(${side * 4}deg)`, offset: 0.3 },
      { transform: `rotate(${side * -2}deg)`, offset: 0.65 },
      { transform: "rotate(0deg)" },
    ],
    { duration: 380 },
  );
}

/** One shake, side to side, for an error. It adds to the squash that plays at the same time. */
export function shake(node: Element | null | undefined) {
  play(
    node,
    [
      { transform: "translateX(0)" },
      { transform: "translateX(-4px) rotate(-2deg)", offset: 0.2 },
      { transform: "translateX(4px) rotate(2deg)", offset: 0.4 },
      { transform: "translateX(-3px) rotate(-1deg)", offset: 0.6 },
      { transform: "translateX(2px)", offset: 0.8 },
      { transform: "translateX(0)" },
    ],
    { duration: 480, composite: "add" },
  );
}
