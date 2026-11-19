import { Loader2, Mic, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import { micSupport } from "@/shared/lib/recorder";
import { Button } from "@/shared/ui/button";
import { useVoiceInput } from "./useVoiceInput";

interface MicButtonProps {
  /** The speech server is not set up: the button is off and says so. */
  unavailable: boolean;
  /** Receives the transcript. */
  onText: (text: string) => void;
}

/** Push-to-talk: press to record, press again to stop; the words land in the question box. */
export function MicButton({ unavailable, onText }: MicButtonProps) {
  const { t } = useTranslation(undefined, { keyPrefix: "voice" });
  const { state, toggle } = useVoiceInput(onText);
  const support = micSupport();
  const recording = state.phase === "recording";
  const transcribing = state.phase === "transcribing";

  let message = "";
  if (unavailable) message = t("unavailable");
  else if (support !== "ok") message = t(`problem.${support}`);
  else if (recording) message = t("recording");
  else if (transcribing) message = t("transcribing");
  else if (state.phase === "done") message = t("done");
  else if (state.problem) message = t(`problem.${state.problem}`);

  const disabled = !recording && (unavailable || support !== "ok" || transcribing);
  const failed = state.phase === "error" || unavailable || support !== "ok";

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        variant={recording ? "default" : "outline"}
        disabled={disabled}
        aria-pressed={recording}
        onClick={() => void toggle()}
      >
        {transcribing ? (
          <Loader2 aria-hidden="true" className="animate-spin" />
        ) : recording ? (
          <Square aria-hidden="true" />
        ) : (
          <Mic aria-hidden="true" />
        )}
        {recording ? t("stop") : transcribing ? t("transcribingShort") : t("start")}
      </Button>
      {recording ? (
        <span aria-hidden="true" className="h-2 w-20 overflow-hidden rounded-full bg-muted">
          <span
            className="block h-full bg-primary transition-[width] duration-100"
            style={{ width: `${Math.round(state.level * 100)}%` }}
          />
        </span>
      ) : null}
      <p
        role="status"
        aria-live="polite"
        className={failed ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
      >
        {message}
      </p>
    </div>
  );
}
