import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { useCircuitCalls } from "./api";
import { CheckPanel } from "./CheckPanel";
import { ConventionsCard } from "./ConventionsCard";
import { ExportPanel, QasmView } from "./ExportPanel";
import { createFlows } from "./flows";
import { InputPanel } from "./InputPanel";
import { LiveStatus } from "./LiveStatus";
import { OutcomeView } from "./OutcomeView";
import { CurrentProblem } from "./ProblemView";
import { ReadingView } from "./ReadingView";
import { RunPanel } from "./RunPanel";
import { useCircuitStore } from "./store";
import { ValuesPanel } from "./ValuesPanel";

export function CircuitsPage() {
  const { t } = useTranslation();
  const { parse, run, check, qasm, paperCircuits, paperQasm } = useCircuitCalls();
  const flows = useMemo(
    () => createFlows({ parse, run, check, qasm, paperCircuits, paperQasm }),
    [parse, run, check, qasm, paperCircuits, paperQasm],
  );
  const [params] = useSearchParams();
  const opened = useRef(false);
  // `?doc=<id>` (the "Circuits of this paper" link of a paper) opens the Paper tab on that paper.
  // It is read once, when the screen opens; choosing another paper afterwards is not undone.
  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    const docId = params.get("doc")?.trim();
    if (!docId) return;
    const store = useCircuitStore.getState();
    store.setShowPaper(true);
    store.setPaper(docId);
    void flows.listPaperCircuits();
  }, [params, flows]);
  return (
    <>
      <PageHeader title={t("pages.circuits.title")}>
        <p className="max-w-prose text-muted-foreground">{t("circuits.intro")}</p>
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        <LiveStatus />
        <InputPanel flows={flows} />
        <CurrentProblem />
        <ReadingView />
        <ValuesPanel />
        <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          <RunPanel flows={flows} />
          <CheckPanel flows={flows} />
          <ExportPanel flows={flows} />
        </div>
        <OutcomeView />
        <QasmView />
        <ConventionsCard />
      </div>
    </>
  );
}

export { CircuitsPage as Component };
