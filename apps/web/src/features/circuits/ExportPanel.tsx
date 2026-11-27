import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import type { CircuitFlows } from "./flows";
import {
  ExportNotes,
  ExportRefusal,
  ExportUnbound,
  ProgramView,
  UnmatchedValues,
} from "./ProgramView";
import { useCanSend } from "./RunPanel";
import { Section } from "./Section";
import { useCircuitStore, type QasmOutcome } from "./store";

/** The Export card: the circuit read (or the circuit/1 text) as OpenQASM 3, with the values. */
export function ExportPanel({ flows }: { flows: CircuitFlows }) {
  const { t } = useTranslation();
  const busy = useCircuitStore((state) => state.busy);
  const canSend = useCanSend();
  return (
    <Section title={t("circuits.export.title")} description={t("circuits.export.description")}>
      <div>
        <Button onClick={() => void flows.exportQasm()} disabled={!canSend}>
          {busy === "qasm" ? t("circuits.export.exporting") : t("circuits.export.submit")}
        </Button>
      </div>
    </Section>
  );
}

function QasmBody({ outcome }: { outcome: QasmOutcome }) {
  const { t } = useTranslation();
  const circuits = useCircuitStore((state) => state.reading?.circuits.length ?? 0);
  const exported = outcome.export;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      {circuits > 1 ? (
        <p className="text-sm text-muted-foreground">
          {t("circuits.export.ofCircuit", { n: outcome.circuit + 1 })}
        </p>
      ) : null}
      {exported.status === "refused" ? <ExportRefusal reasons={exported.reasons} /> : null}
      {exported.status === "refused" && exported.unbound.length > 0 ? (
        <ExportUnbound names={exported.unbound} />
      ) : null}
      <UnmatchedValues keys={exported.unmatched} />
      {exported.programs.map((program) => (
        <ProgramView key={program.filename} program={program} />
      ))}
      <ExportNotes title={t("circuits.export.notes")} items={exported.notes} />
    </div>
  );
}

/** The programs of the last export of the pasted circuit, or why there are none. */
export function QasmView() {
  const { t } = useTranslation();
  const outcome = useCircuitStore((state) => state.qasm);
  if (!outcome) return null;
  return (
    <Section
      title={t("circuits.export.resultTitle")}
      description={
        outcome.export.programs.length > 0 ? t("circuits.export.resultDescription") : undefined
      }
    >
      <QasmBody key={outcome.serial} outcome={outcome} />
    </Section>
  );
}
