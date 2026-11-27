/** Which mouth Qupi shows: the closed smile, the half-open one, or the open one drawn in the head. */
export type MouthShape = "closed" | "half" | "open";

/** Playback levels (0 to 1) where the mouth changes shape. */
export const MOUTH_HALF_FROM = 0.15;
export const MOUTH_OPEN_FROM = 0.4;

/** Maps the loudness of the voice to a mouth. A level that is not a number counts as silence. */
export function mouthFor(level: number): MouthShape {
  if (!(level >= MOUTH_HALF_FROM)) return "closed";
  return level < MOUTH_OPEN_FROM ? "half" : "open";
}
