import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useSearchParams } from "react-router";
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

/** The LaTeX another screen sent in the router state, or null. The state is not trusted to be ours. */
function latexOf(state: unknown): string | null {
  if (typeof state !== "object" || state === null || !("latex" in state)) return null;
  const { latex } = state;
  return typeof latex === "string" && latex.trim() !== "" ? latex : null;
}

/** What another screen asked this one to open: a LaTeX to read, or a paper to list. */
type Incoming = { latex: string } | { docId: string };

export function CircuitsPage() {
  const { t } = useTranslation();
  const { parse, run, check, qasm, paperCircuits, paperQasm } = useCircuitCalls();
  const flows = useMemo(
    () => createFlows({ parse, run, check, qasm, paperCircuits, paperQasm }),
    [parse, run, check, qasm, paperCircuits, paperQasm],
  );
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const busy = useCircuitStore((state) => state.busy);
  // undefined until the screen has looked at its entry; then what is still to open, or null
  const incoming = useRef<Incoming | null | undefined>(undefined);
  // What another screen sends here is read once, when the screen opens:
  // - router state `{ latex }` ("Open in Circuits" under a latex block of an answer or a paper)
  //   goes to the LaTeX tab and is read. The LaTeX travels in the state, not in the address: it
  //   can be long and it is paper text. The state is then removed from the history entry
  //   (navigate with replace), so a reload, or coming back to this entry, does not read it again.
  //   It does not survive a reload either way: the screen's store is in memory only.
  // - `?doc=<id>` ("Circuits of this paper") opens the Paper tab on that paper. It stays in the
  //   address, so a reload opens the same paper. Choosing another paper writes its id there, and
  //   leaving the Paper tab removes it.
  // One request runs at a time, so when both come, the LaTeX wins. A request of an earlier visit
  // may still be running (the store outlives the screen): then what was sent waits until the
  // screen is idle, instead of being dropped by the one-request rule.
  useEffect(() => {
    if (incoming.current === undefined) {
      const latex = latexOf(location.state);
      const docId = params.get("doc")?.trim().toLowerCase();
      incoming.current = latex !== null ? { latex } : docId ? { docId } : null;
      if (latex !== null) {
        void navigate(`${location.pathname}${location.search}${location.hash}`, {
          replace: true,
          state: null,
        });
      }
    }
    const next = incoming.current;
    if (next === null || busy !== null) return;
    incoming.current = null;
    const store = useCircuitStore.getState();
    if ("latex" in next) {
      store.loadLatex(next.latex);
      void flows.read();
      return;
    }
    store.setShowPaper(true);
    store.setPaper(next.docId);
    void flows.listPaperCircuits();
  }, [busy, params, location, navigate, flows]);
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
