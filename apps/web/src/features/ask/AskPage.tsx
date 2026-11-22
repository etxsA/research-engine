import { useTranslation } from "react-i18next";
import { PageHeader } from "@/shared/layout/PageHeader";
import { Button } from "@/shared/ui/button";
import { useAskFlow, useEngineStatus } from "./api";
import { AnswerView } from "./AnswerView";
import { blocksAsking, EngineNotice, engineView, isStarting } from "./EngineNotice";
import { FailureView } from "./FailureView";
import { HistoryList } from "./HistoryList";
import { ProgressView, useProgressText } from "./ProgressView";
import { QuestionForm } from "./QuestionForm";
import { stopReading } from "./reader";
import { SourcePanel } from "./SourcePanel";
import { activeEntry, isActive, useAskStore, type AskEntry } from "./store";

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
  // A new question stops the voice of the answer being read.
  const submit = (question: string) => {
    stopReading();
    void flow.submit(question, useAskStore.getState().settings);
  };

  return (
    <>
      <PageHeader title={root("pages.ask.title")}>
        <p className="max-w-prose text-muted-foreground">{t("intro")}</p>
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,1fr)_16rem]">
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <EngineNotice view={view} />
          <QuestionForm
            blocked={blocked}
            busy={active !== undefined}
            askAnyway={view.kind === "unavailable"}
            speechUnavailable={status.data?.speech?.stt === "unavailable"}
            onSubmit={submit}
          />
          <Announcer entry={entries[0]} />
          {selected ? (
            <EntryView
              entry={selected}
              retryDisabled={retryBlocked}
              onCancel={() => void flow.cancel()}
              onRetry={(entry) => {
                stopReading();
                void flow.submit(entry.question, entry.settings);
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
  const setQuestion = useAskStore((state) => state.setQuestion);
  const setSettings = useAskStore((state) => state.setSettings);
  // Puts the question and its options back in the box, to change them before asking again.
  const edit = () => {
    setQuestion(entry.question);
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
