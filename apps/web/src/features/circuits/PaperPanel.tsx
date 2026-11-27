import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { isApiError } from "@/shared/api/errors";
import { TextField } from "@/shared/forms/TextField";
import { Button } from "@/shared/ui/button";
import { usePaperSearch } from "./api";
import type { CircuitFlows } from "./flows";
import { ProblemView } from "./ProblemView";
import { ExportNotes, ExportRefusal, ExportUnbound, ProgramView } from "./ProgramView";
import { StatusBadge } from "./ReadingView";
import { useCircuitStore, type PaperExportOutcome } from "./store";
import type { PaperBlock } from "./types";

/**
 * The Paper tab: find a stored paper, list its circuit blocks as `docingest circuits export`
 * numbers them, read one block in the LaTeX tab, or export every circuit of the paper. The export
 * is read only: the programs come back to the browser and nothing is written on the server.
 */
export function PaperPanel({
  flows,
  onReadBlock,
}: {
  flows: CircuitFlows;
  onReadBlock: (latex: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 pt-2">
      <p className="max-w-prose text-sm text-muted-foreground">{t("circuits.paper.description")}</p>
      <PaperSearch flows={flows} />
      <PaperBlocks flows={flows} onReadBlock={onReadBlock} />
      <PaperExportView />
    </div>
  );
}

function PaperSearch({ flows }: { flows: CircuitFlows }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState<string | null>(null);
  const search = usePaperSearch(query);
  const busy = useCircuitStore((state) => state.busy);
  const paperId = useCircuitStore((state) => state.paperId);
  const setPaper = useCircuitStore((state) => state.setPaper);
  const [, setParams] = useSearchParams();
  const items = search.data?.items ?? [];
  return (
    <>
      <form
        role="search"
        aria-label={t("circuits.paper.searchLabel")}
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <div className="min-w-0 flex-1">
          <TextField
            label={t("circuits.paper.query")}
            hint={t("circuits.paper.queryHint")}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck={false}
            autoComplete="off"
            maxLength={200}
          />
        </div>
        <Button type="submit" disabled={search.isFetching}>
          {search.isFetching ? t("circuits.paper.searching") : t("circuits.paper.search")}
        </Button>
      </form>
      {search.isError ? (
        <ProblemView
          problem={
            isApiError(search.error)
              ? { kind: "api", error: search.error }
              : { kind: "local", code: "network", params: {} }
          }
        />
      ) : null}
      {search.data ? (
        items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("circuits.paper.none")}</p>
        ) : (
          <div className="grid gap-2">
            {search.data.total > items.length ? (
              <p className="text-sm text-muted-foreground">
                {t("circuits.paper.showing", { shown: items.length, total: search.data.total })}
              </p>
            ) : null}
            <ul
              className="grid gap-2"
              aria-label={t("circuits.paper.found")}
              data-testid="paper-results"
            >
              {items.map((item) => (
                <li key={item.doc_id} className="grid gap-0.5">
                  <div>
                    <Button
                      variant={item.doc_id === paperId ? "default" : "outline"}
                      size="sm"
                      className="h-auto max-w-full py-1 text-left whitespace-normal"
                      aria-current={item.doc_id === paperId ? "true" : undefined}
                      disabled={busy !== null}
                      onClick={() => {
                        setPaper(item.doc_id);
                        // the address names the paper shown, so a reload or a shared link opens it
                        setParams(
                          (previous) => {
                            const next = new URLSearchParams(previous);
                            next.set("doc", item.doc_id);
                            return next;
                          },
                          { replace: true },
                        );
                        void flows.listPaperCircuits();
                      }}
                    >
                      {item.title}
                    </Button>
                  </div>
                  <span className="font-mono text-xs break-all text-muted-foreground">
                    {item.short_id} {"\u00b7"} {item.source_name}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )
      ) : null}
    </>
  );
}

function Block({
  block,
  disabled,
  onRead,
}: {
  block: PaperBlock;
  disabled: boolean;
  onRead: () => void;
}) {
  const { t } = useTranslation();
  return (
    <li className="grid gap-2 rounded-lg border p-3" data-testid="paper-block">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">{t("circuits.paper.block", { block: block.block })}</h4>
        <StatusBadge status={block.status} />
        <span className="text-sm text-muted-foreground">
          {t("circuits.paper.circuitsRead", { count: block.circuits.length })}
        </span>
      </div>
      <details>
        <summary className="cursor-pointer text-sm">
          {t("circuits.paper.latex", { block: block.block })}
        </summary>
        <pre className="mt-2 rounded-lg bg-muted p-3 font-mono text-xs break-all whitespace-pre-wrap">
          {block.latex}
        </pre>
      </details>
      <div>
        <Button
          variant="outline"
          size="sm"
          aria-label={t("circuits.paper.readBlock", { block: block.block })}
          disabled={disabled}
          onClick={onRead}
        >
          {t("circuits.paper.read")}
        </Button>
      </div>
    </li>
  );
}

function PaperBlocks({
  flows,
  onReadBlock,
}: {
  flows: CircuitFlows;
  onReadBlock: (latex: string) => void;
}) {
  const { t } = useTranslation();
  const paper = useCircuitStore((state) => state.paperCircuits);
  const busy = useCircuitStore((state) => state.busy);
  if (!paper) {
    return busy === "paperCircuits" ? (
      <p className="text-sm text-muted-foreground">{t("circuits.paper.loading")}</p>
    ) : null;
  }
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3" data-testid="paper-blocks">
      <div>
        <h3 className="text-sm font-semibold break-all">{paper.sourceName}</h3>
        <p className="font-mono text-xs break-all text-muted-foreground">
          {t("circuits.paper.id", { id: paper.docId })}
        </p>
      </div>
      {paper.blocks.length === 0 ? (
        <p className="text-sm">{t("circuits.paper.noBlocks")}</p>
      ) : (
        <ol
          className="grid gap-3"
          aria-label={t("circuits.paper.blocks", { count: paper.blocks.length })}
        >
          {paper.blocks.map((block) => (
            <Block
              key={block.block}
              block={block}
              disabled={busy !== null}
              onRead={() => onReadBlock(block.latex)}
            />
          ))}
        </ol>
      )}
      <div className="grid gap-1.5">
        <p className="max-w-prose text-sm text-muted-foreground">
          {t("circuits.paper.exportHint")}
        </p>
        <div>
          <Button onClick={() => void flows.exportPaper()} disabled={busy !== null}>
            {busy === "paperQasm" ? t("circuits.paper.exporting") : t("circuits.paper.export")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function PaperExportBody({ outcome }: { outcome: PaperExportOutcome }) {
  const { t } = useTranslation();
  const exported = outcome.export;
  const programs = exported.circuits.reduce((sum, item) => sum + item.programs.length, 0);
  const refused = exported.circuits.filter((item) => item.status === "refused").length;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3" data-testid="paper-export">
      <div>
        <h3 className="text-sm font-semibold break-all">
          {t("circuits.paper.exportTitle", { source: exported.sourceName })}
        </h3>
        <p className="text-sm text-muted-foreground">
          {t("circuits.paper.exportSummary", { programs, refused })}
        </p>
      </div>
      <ExportNotes title={t("circuits.paper.warnings")} items={exported.warnings} />
      {exported.circuits.length > 0 ? (
        <ol className="grid grid-cols-[minmax(0,1fr)] gap-4">
          {exported.circuits.map((item) => (
            <li
              key={`${item.block}-${item.circuit}`}
              className="grid grid-cols-[minmax(0,1fr)] gap-3 rounded-lg border p-3"
              data-testid="paper-circuit"
            >
              <h4 className="text-sm font-medium">
                {t("circuits.paper.circuit", { block: item.block, circuit: item.circuit })}
              </h4>
              {item.status === "refused" ? <ExportRefusal reasons={item.reasons} quiet /> : null}
              {item.status === "refused" && item.unbound.length > 0 ? (
                <ExportUnbound names={item.unbound} />
              ) : null}
              {item.programs.map((program) => (
                <ProgramView key={program.filename} program={program} level={5} />
              ))}
              <ExportNotes title={t("circuits.export.notes")} items={item.notes} />
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

/** The programs, refusals and warnings of the last export of the paper. */
function PaperExportView() {
  const outcome = useCircuitStore((state) => state.paperExport);
  return outcome ? <PaperExportBody key={outcome.serial} outcome={outcome} /> : null;
}
