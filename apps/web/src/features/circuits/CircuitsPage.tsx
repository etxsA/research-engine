import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/shared/layout/PageHeader";
import { useCircuitCalls } from "./api";
import { CheckPanel } from "./CheckPanel";
import { ConventionsCard } from "./ConventionsCard";
import { createFlows } from "./flows";
import { InputPanel } from "./InputPanel";
import { LiveStatus } from "./LiveStatus";
import { OutcomeView } from "./OutcomeView";
import { CurrentProblem } from "./ProblemView";
import { ReadingView } from "./ReadingView";
import { RunPanel } from "./RunPanel";

export function CircuitsPage() {
  const { t } = useTranslation();
  const { parse, run, check, qasm, paperCircuits, paperQasm } = useCircuitCalls();
  const flows = useMemo(
    () => createFlows({ parse, run, check, qasm, paperCircuits, paperQasm }),
    [parse, run, check, qasm, paperCircuits, paperQasm],
  );
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
        <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          <RunPanel flows={flows} />
          <CheckPanel flows={flows} />
        </div>
        <OutcomeView />
        <ConventionsCard />
      </div>
    </>
  );
}

export { CircuitsPage as Component };
