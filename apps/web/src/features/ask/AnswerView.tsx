import { TriangleAlert } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { MarkdownMath, SafeLink } from "@/shared/markdown/MarkdownMath";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Card, CardContent } from "@/shared/ui/card";
import { citationIndex, linkCitations } from "./citations";
import { CitationChip, CitationTitle, InlineCitation, openerName } from "./CitationButtons";
import { ContextsPanel } from "./ContextsPanel";
import { formatSeconds } from "./format";
import { ReadAloudButton } from "./ReadAloudButton";
import { useAskStore, type AskEntry } from "./store";
import type { AskResponse } from "./types";

/** The answer to one question: its text with clickable citations, the sources, what the model read. */
export function AnswerView({ entry, response }: { entry: AskEntry; response: AskResponse }) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "ask" });
  const restore = useAskStore((state) => state.restore);
  const setSettings = useAskStore((state) => state.setSettings);
  const text = useMemo(
    () => linkCitations(response.answer, response.citations),
    [response.answer, response.citations],
  );
  const components = useMemo(
    () => ({
      a: ({ href, children }: { href?: string | undefined; children?: React.ReactNode }) => {
        const index = citationIndex(href);
        const citation = index === null ? undefined : response.citations[index];
        if (index === null || citation === undefined) {
          return <SafeLink href={href}>{children}</SafeLink>;
        }
        return (
          <InlineCitation citation={citation} opener={openerName(entry.key, "inline", index)}>
            {children}
          </InlineCitation>
        );
      },
    }),
    [response.citations, entry.key],
  );

  const { timings } = response;
  const language = i18n.language;
  return (
    <Card>
      <CardContent className="grid gap-5">
        <div className="grid gap-1">
          <h2 className="text-lg font-semibold">{t("answer.title")}</h2>
          <p className="text-sm text-muted-foreground">
            <span className="font-medium">{t("answer.asked")}:</span> {entry.question}
          </p>
        </div>

        {response.warnings.length > 0 ? (
          <Alert role="note">
            <TriangleAlert aria-hidden="true" />
            <AlertTitle>{t("answer.warnings")}</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-4">
                {response.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        <MarkdownMath components={components}>{text}</MarkdownMath>

        <section aria-labelledby={`sources-${entry.key}`} className="grid gap-2">
          <h3 id={`sources-${entry.key}`} className="text-sm font-medium">
            {t("answer.sources")}
          </h3>
          {response.citations.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("answer.noSources")}</p>
          ) : (
            <ul className="grid gap-2">
              {response.citations.map((citation, index) => (
                <li key={`${citation.key}:${index}`} className="flex flex-wrap items-center gap-2">
                  <CitationChip citation={citation} opener={openerName(entry.key, "chip", index)} />
                  <CitationTitle docId={citation.doc_id} />
                </li>
              ))}
            </ul>
          )}
        </section>

        <ContextsPanel entryKey={entry.key} contexts={response.contexts} />

        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            {entry.settings.useIndex
              ? t("answer.timings", {
                  retrieve: formatSeconds(timings.retrieve_s, language),
                  answer: formatSeconds(timings.answer_s, language),
                  wall: formatSeconds(timings.wall_s, language),
                })
              : t("answer.timingsNoSearch", { answer: formatSeconds(timings.answer_s, language) })}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <ReadAloudButton entryKey={entry.key} answer={response.answer} />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                restore(entry.question, entry.spoken);
                setSettings(entry.settings);
                document.getElementById("ask-question")?.focus();
              }}
            >
              {t("answer.again")}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
