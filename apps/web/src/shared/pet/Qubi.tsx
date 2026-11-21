import { cn } from "@/shared/lib/utils";
import type { PetState } from "./petState";

interface QubiProps {
  state: PetState;
  /** Stops every animation; the state is still shown by pose and colour. */
  still: boolean;
  /** Asks ahead of the question while it waits. */
  position?: number | null;
  className?: string;
}

/**
 * The placeholder art of Qubi: a Bloch sphere with a face, and its state vector as the antenna.
 * To replace the art, replace this file; the states, the sizes and the CSS variables it reads
 * (`--qubi-level`, `--qubi-mouth`, both 0 to 1, set by QubiPet) are the whole contract.
 * Colours come from the theme tokens, so it works in the light and the dark theme.
 */
export function Qubi({ state, still, position = null, className }: QubiProps) {
  const motion = !still;
  return (
    <div
      data-pet="qubi"
      data-state={state}
      data-motion={still ? "still" : "full"}
      className={cn(
        "relative size-[72px] shrink-0 text-primary",
        state === "unavailable" && "opacity-50 grayscale",
        className,
      )}
    >
      <svg viewBox="0 0 72 72" aria-hidden="true" focusable="false" className="size-full">
        <g className={cn(motion && state === "idle" && "qubi-bob")}>
          {state === "listening" ? (
            <g
              className={cn("qubi-rings fill-none stroke-primary", motion && "qubi-rings-live")}
              strokeWidth="1.5"
            >
              <circle cx="36" cy="42" r="27" opacity="0.55" />
              <circle cx="36" cy="42" r="31" opacity="0.3" />
            </g>
          ) : null}
          <g
            className={cn(
              "qubi-antenna",
              state === "error" && "qubi-antenna-droop",
              motion && state === "thinking" && "qubi-antenna-precess",
            )}
          >
            <line
              x1="36"
              y1="18"
              x2="36"
              y2="6"
              strokeWidth="2"
              strokeLinecap="round"
              className={state === "error" ? "stroke-destructive" : "stroke-primary"}
            />
            <path
              d="M36 2 L32 8.5 L40 8.5 Z"
              className={state === "error" ? "fill-destructive" : "fill-primary"}
            />
          </g>
          <circle cx="36" cy="42" r="24" className="fill-card stroke-primary" strokeWidth="2" />
          <ellipse
            cx="36"
            cy="42"
            rx="24"
            ry="7"
            className="fill-none stroke-primary"
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity="0.4"
          />
          <ellipse
            cx="36"
            cy="42"
            rx="7"
            ry="24"
            className="fill-none stroke-primary"
            strokeWidth="1"
            opacity="0.25"
          />
          <circle cx="28" cy="38" r="2.6" className="fill-foreground" />
          <circle cx="44" cy="38" r="2.6" className="fill-foreground" />
          <Mouth state={state} />
          {state === "transcribing" ? (
            <g className="fill-primary">
              {[26, 36, 46].map((x, index) => (
                <circle
                  key={x}
                  cx={x}
                  cy="64"
                  r="2.2"
                  className={cn(motion && "qubi-dot")}
                  style={motion ? { animationDelay: `${index * 0.2}s` } : undefined}
                />
              ))}
            </g>
          ) : null}
        </g>
      </svg>
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

function Mouth({ state }: { state: PetState }) {
  if (state === "speaking") {
    return <ellipse cx="36" cy="51" rx="5.5" ry="5" className="qubi-mouth fill-foreground" />;
  }
  const path =
    state === "error"
      ? "M30 53 Q36 48 42 53"
      : state === "listening"
        ? "M31 50 Q36 55 41 50"
        : "M30 50 Q36 56 42 50";
  return (
    <path d={path} className="fill-none stroke-foreground" strokeWidth="2" strokeLinecap="round" />
  );
}
