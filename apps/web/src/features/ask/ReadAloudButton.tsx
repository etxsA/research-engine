import { Square, Volume2 } from "lucide-react";
import { useEffect, useMemo, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { useReadingEngine } from "./api";
import {
  getReaderState,
  readerSupported,
  startReading,
  stopReading,
  subscribeReader,
} from "./reader";
import { readingPlan } from "./speakable";

interface ReadAloudButtonProps {
  /** Identity of the answer: only one answer reads at a time. */
  entryKey: string;
  /** The answer's text, as the model wrote it (markdown, citations and formulas included). */
  answer: string;
}

/** Press to hear the answer, press again to stop. The speech server reads it when there is one. */
export function ReadAloudButton({ entryKey, answer }: ReadAloudButtonProps) {
  const { t } = useTranslation(undefined, { keyPrefix: "voice.read" });
  const engine = useReadingEngine();
  const state = useSyncExternalStore(subscribeReader, getReaderState);
  const empty = useMemo(() => readingPlan(answer).text === "", [answer]);
  const reading = state.owner === entryKey;

  // Leaving the screen, or the answer going away, stops its voice.
  useEffect(
    () => () => {
      if (getReaderState().owner === entryKey) stopReading();
    },
    [entryKey],
  );

  if (empty || (!reading && !readerSupported(engine))) return null;

  const failed = state.failed?.owner === entryKey ? state.failed.problem : null;
  let message = "";
  if (reading) {
    message = state.loading
      ? t("loading")
      : state.fellBack
        ? t("fellBack")
        : state.engine === "browser"
          ? t("statusBrowser")
          : t("status");
  } else if (failed) {
    message = t(`problem.${failed}`);
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        variant={reading ? "default" : "outline"}
        size="sm"
        aria-pressed={reading}
        onClick={() => (reading ? stopReading() : startReading(entryKey, answer, engine))}
      >
        {reading ? <Square aria-hidden="true" /> : <Volume2 aria-hidden="true" />}
        {reading ? t("stop") : t("start")}
      </Button>
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
