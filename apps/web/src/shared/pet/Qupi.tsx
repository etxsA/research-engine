import { cn } from "@/shared/lib/utils";
import type { PetState } from "./petState";
import { QUPI_LAYERS, QUPI_ORIGINS } from "./qupi/rig";
import "./qupi.css";

/** Edge of the square the layers are drawn on, in CSS pixels at the desktop size. */
export const QUPI_SIZE = 120;

// Every WebP of the rig, found at build time: `<layer>.webp` (1x) and `<layer>@2x.webp` (2x).
const FILES = import.meta.glob<string>("./qupi/*.webp", {
  eager: true,
  query: "?url",
  import: "default",
});

const origins: Record<string, string> = QUPI_ORIGINS;
/** Layers that turn and bob with the head: the ears, the face and everything drawn on it. */
const HEAD_GROUP = new Set([
  "ear-left",
  "ear-right",
  "head",
  "mark",
  "eyelids",
  "mouth-closed",
  "mouth-half",
]);

interface QupiProps {
  state: PetState;
  /** Stops every animation; the state is still shown by the pose, the eyes, the mouth and the filters. */
  still: boolean;
  /** Asks ahead of the question while it waits. */
  position?: number | null;
  className?: string;
}

function Layer({ name }: { name: string }) {
  const one = FILES[`./qupi/${name}.webp`];
  const two = FILES[`./qupi/${name}@2x.webp`];
  return (
    <img
      alt=""
      src={one}
      srcSet={one && two ? `${one} 1x, ${two} 2x` : undefined}
      width={QUPI_SIZE}
      height={QUPI_SIZE}
      decoding="async"
      draggable={false}
      data-layer={name}
      className={cn(
        "qupi-layer",
        `qupi-l-${name}`,
        (name === "ear-left" || name === "ear-right") && "qupi-ear",
      )}
      style={{ transformOrigin: origins[name] }}
    />
  );
}

/**
 * Qupi, a Pomeranian drawn as stacked WebP layers (`./qupi`, see its README) that CSS moves one by
 * one. The art is decorative. To change it, replace the files and `rig.ts` there: the states, the
 * layer names and the CSS variables it reads (`--qupi-level` for the microphone and `--qupi-mouth`
 * for the voice, both 0 to 1, and `data-mouth` for the voice's mouth, all set by QupiPet) are the
 * whole contract. The motion is in `qupi.css`.
 */
export function Qupi({ state, still, position = null, className }: QupiProps) {
  const motion = !still;
  const headLayers = QUPI_LAYERS.filter((name) => HEAD_GROUP.has(name));
  return (
    <div
      data-pet="qupi"
      data-state={state}
      data-motion={still ? "still" : "full"}
      className={cn("qupi relative size-20 shrink-0 sm:size-[120px]", className)}
    >
      <div className="qupi-rig">
        {QUPI_LAYERS.filter((name) => name === "tail" || name === "body").map((name) => (
          <Layer key={name} name={name} />
        ))}
        <div className="qupi-head-group" style={{ transformOrigin: origins.head }}>
          {headLayers.map((name) => (
            <Layer key={name} name={name} />
          ))}
        </div>
      </div>
      {state === "thinking" ? (
        <svg
          viewBox="0 0 120 120"
          aria-hidden="true"
          focusable="false"
          className="qupi-orbit pointer-events-none absolute inset-0 size-full"
        >
          <g className={cn("qupi-orbit-a", motion && "qupi-orbit-live")}>
            <circle cx="60" cy="6" r="4.5" fill="#2dd4bf" />
            <circle cx="60" cy="6" r="7" fill="#2dd4bf" opacity="0.25" />
          </g>
          <g className={cn("qupi-orbit-b", motion && "qupi-orbit-live")}>
            <circle cx="60" cy="14" r="3.5" fill="#a78bfa" />
            <circle cx="60" cy="14" r="6" fill="#a78bfa" opacity="0.25" />
          </g>
        </svg>
      ) : null}
      {state === "unavailable" ? (
        <span
          aria-hidden="true"
          className={cn(
            "qupi-z absolute top-1 right-1 text-sm font-semibold text-muted-foreground",
            motion && "qupi-z-live",
          )}
        >
          z
        </span>
      ) : null}
      {state === "transcribing" ? (
        <svg
          viewBox="0 0 24 6"
          aria-hidden="true"
          focusable="false"
          className="absolute top-2 right-1 h-1.5 w-6 fill-primary"
        >
          {[3, 12, 21].map((x, index) => (
            <circle
              key={x}
              cx={x}
              cy="3"
              r="2.4"
              className={cn(motion && "qupi-dot")}
              style={motion ? { animationDelay: `${index * 0.2}s` } : undefined}
            />
          ))}
        </svg>
      ) : null}
      {state === "waiting" && position !== null && position > 0 ? (
        <span
          aria-hidden="true"
          className="absolute top-3 right-0 grid min-w-5 place-items-center rounded-full border border-border bg-primary px-1 text-[11px] leading-5 font-semibold text-primary-foreground"
        >
          {position}
        </span>
      ) : null}
    </div>
  );
}
