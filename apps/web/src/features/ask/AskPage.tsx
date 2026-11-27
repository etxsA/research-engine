import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { QupiPet } from "@/shared/pet/QupiPet";
import { PageHeader } from "@/shared/layout/PageHeader";
import { Button } from "@/shared/ui/button";
import { useAskFlow, useEngineStatus, useReadingEngine, useReadSessionGuard } from "./api";
import { getAutoRead } from "./autoRead";
import { AnswerView } from "./AnswerView";
import { blocksAsking, EngineNotice, engineView, isStarting } from "./EngineNotice";
import { FailureView } from "./FailureView";
import { HistoryList } from "./HistoryList";
import { ProgressView, useProgressText } from "./ProgressView";
import { QuestionForm } from "./QuestionForm";
import { startReading, type Engine, stopReading } from "./reader";
import { SourcePanel } from "./SourcePanel";
import { activeEntry, isActive, useAskStore, type AskEntry } from "./store";
import type { VoiceState } from "./useVoiceInput";

export function AskPage() {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const { t: root } = useTranslation();
  const flow = useAskFlow();
  const status = useEngineStatus();
  const entries = useAskStore((state) => state.entries);
  const selectedKey = useAskStore((state) => state.selectedKey);
  const view = engineView(status.data, status.isError);
  const active = activeEntry(entries);
  const selected = entries.find((entry) => entry.key === selectedKey);
  const blocked = blocksAsking(view) || active !== undefined;
  // A retry is an explicit second try, so only a starting engine (or a question in flight) stops it.
  const retryBlocked = isStarting(view) || active !== undefined;
  const [voice, setVoice] = useState<VoiceState>({ phase: "idle", level: 0, problem: null });
  const newest = entries[0];
  // A new question stops the voice of the answer being read.
  const submit = (question: string) => {
    stopReading();
    // A microphone problem is old news once the person asks: Qupi follows the question now.
    setVoice((now) => (now.phase === "error" ? { phase: "idle", level: 0, problem: null } : now));
    const { settings, spoken } = useAskStore.getState();
    void flow.submit(question, settings, spoken);
  };
  useReadSessionGuard();
  useReadWhenAnswered(selected, useReadingEngine());
  // Leaving the screen stops whatever is being read, whichever answer it is.
  useEffect(() => stopReading, []);

  return (
    <>
      <PageHeader title={root("pages.ask.title")}>
        <p className="max-w-prose text-muted-foreground">{t("intro")}</p>
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,1fr)_16rem]">
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <EngineNotice view={view} />
          <div className="flex items-start gap-3">
            <QupiPet
              mic={voice.phase}
              micLevel={voice.level}
              ask={newest?.status ?? null}
              position={newest?.status === "queued" ? newest.position : null}
              unavailable={view.kind === "unavailable"}
            />
            <div className="min-w-0 flex-1">
              <QuestionForm
                blocked={blocked}
                busy={active !== undefined}
                askAnyway={view.kind === "unavailable"}
                speechUnavailable={status.data?.speech?.stt === "unavailable"}
                onVoice={setVoice}
                onSubmit={submit}
              />
            </div>
          </div>
          <Announcer entry={entries[0]} />
          {selected ? (
            <EntryView
              entry={selected}
              retryDisabled={retryBlocked}
              onCancel={() => void flow.cancel()}
              onRetry={(entry) => {
                stopReading();
                void flow.submit(entry.question, entry.settings, entry.spoken);
              }}
            />
          ) : null}
        </div>
        <HistoryList />
      </div>
      <SourcePanel />
    </>
  );
}

/**
 * Reads the answer by itself when the question on screen was spoken and has just been answered,
 * if the person left that on. Only a change seen here counts: an answer that was already on the
 * screen when the page opened is not read, nor is one that finishes while another is shown (it
 * would play with no Stop button next to it).
 */
function useReadWhenAnswered(entry: AskEntry | undefined, engine: Engine) {
  const seen = useRef<{ key: string; status: AskEntry["status"] } | null>(null);
  useEffect(() => {
    const before = seen.current;
    seen.current = entry ? { key: entry.key, status: entry.status } : null;
    if (entry?.status !== "done" || !entry.response || !entry.spoken) return;
    if (before?.key !== entry.key || !isActive(before.status)) return;
    if (getAutoRead()) startReading(entry.key, entry.response.answer, engine);
  }, [entry, engine]);
}

/** What a screen reader hears as the newest question moves along. It exists before the first change, so each change is read. */
function Announcer({ entry }: { entry: AskEntry | undefined }) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const progressText = useProgressText();
  let text = "";
  if (entry) {
    if (isActive(entry.status)) text = progressText(entry.status, entry.position);
    else text = t(`progress.${entry.status}`);
  }
  return (
    <div role="status" aria-live="polite" className="sr-only">
      {text}
    </div>
  );
}

function EntryView({
  entry,
  retryDisabled,
  onCancel,
  onRetry,
}: {
  entry: AskEntry;
  retryDisabled: boolean;
  onCancel: () => void;
  onRetry: (entry: AskEntry) => void;
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const restore = useAskStore((state) => state.restore);
  const setSettings = useAskStore((state) => state.setSettings);
  // Puts the question and its options back in the box, to change them before asking again.
  const edit = () => {
    restore(entry.question, entry.spoken);
    setSettings(entry.settings);
    document.getElementById("ask-question")?.focus();
  };
  if (isActive(entry.status)) return <ProgressView entry={entry} onCancel={onCancel} />;
  if (entry.status === "done" && entry.response) {
    return <AnswerView entry={entry} response={entry.response} />;
  }
  if (entry.status === "failed" && entry.failure) {
    return (
      <FailureView
        failure={entry.failure}
        question={entry.question}
        disabled={retryDisabled}
        onRetry={() => onRetry(entry)}
        onEdit={edit}
      />
    );
  }
  return (
    <div className="grid justify-items-start gap-2 rounded-lg border border-border p-4">
      <p className="text-sm">{t("cancelled")}</p>
      <p className="text-sm text-muted-foreground">
        <span className="font-medium">{t("answer.asked")}:</span> {entry.question}
      </p>
      <Button type="button" variant="outline" size="sm" onClick={edit}>
        {t("answer.again")}
      </Button>
    </div>
  );
}

export { AskPage as Component };
