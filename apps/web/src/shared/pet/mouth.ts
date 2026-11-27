/** Which mouth Qupi shows: the closed smile, the half-open one, or the open one drawn in the head. */
export type MouthShape = "closed" | "half" | "open";

/** Playback levels (0 to 1) where the mouth changes shape. */
export const MOUTH_HALF_FROM = 0.15;
export const MOUTH_OPEN_FROM = 0.4;

/** A shape that is showing holds until the level moves this far past its edge, so it does not flicker. */
export const MOUTH_HYSTERESIS = 0.03;

const RANK: Record<MouthShape, number> = { closed: 0, half: 1, open: 2 };

function plainShape(level: number): MouthShape {
  if (!(level >= MOUTH_HALF_FROM)) return "closed";
  return level < MOUTH_OPEN_FROM ? "half" : "open";
}

/**
 * Maps the loudness of the voice to a mouth. A level that is not a number counts as silence. With
 * the shape that is showing now as `current`, the mouth only changes once the level is clear of the
 * edge by `MOUTH_HYSTERESIS`.
 */
export function mouthFor(level: number, current?: MouthShape): MouthShape {
  const next = plainShape(level);
  if (!current || next === current) return next;
  // Judge the level against the edge between the two shapes that sit next to each other.
  const lower = RANK[next] < RANK[current];
  if (!lower) return plainShape(level - MOUTH_HYSTERESIS);
  return plainShape(level + MOUTH_HYSTERESIS);
}
