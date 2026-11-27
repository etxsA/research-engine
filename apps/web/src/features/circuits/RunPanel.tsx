import { useId } from "react";
import { useTranslation } from "react-i18next";
import { TextField } from "@/shared/forms/TextField";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";
import { Switch } from "@/shared/ui/switch";
import type { CircuitFlows } from "./flows";
import { Section } from "./Section";
import { useCircuitStore } from "./store";

/** True when there is a circuit to send: a read one, or any text on the JSON tab. */
export function useCanSend(): boolean {
  const inputKind = useCircuitStore((state) => state.inputKind);
  const hasReading = useCircuitStore((state) => (state.reading?.circuits.length ?? 0) > 0);
  const busy = useCircuitStore((state) => state.busy);
  const jsonText = useCircuitStore((state) => state.jsonText);
  if (busy !== null) return false;
  return inputKind === "json" ? jsonText.trim() !== "" : hasReading;
}

export function RunPanel({ flows }: { flows: CircuitFlows }) {
  const { t } = useTranslation();
  const inputState = useCircuitStore((state) => state.inputState);
  const unitary = useCircuitStore((state) => state.unitary);
  const busy = useCircuitStore((state) => state.busy);
  const setInputState = useCircuitStore((state) => state.setInputState);
  const setUnitary = useCircuitStore((state) => state.setUnitary);
  const canSend = useCanSend();
  const unitaryId = useId();
  return (
    <Section title={t("circuits.run.title")} description={t("circuits.run.description")}>
      <TextField
        label={t("circuits.run.input")}
        hint={t("circuits.run.inputHint")}
        value={inputState}
        onChange={(event) => setInputState(event.target.value)}
        placeholder="000"
        spellCheck={false}
        autoComplete="off"
        className="font-mono"
      />
      <div className="flex items-center gap-2">
        <Switch id={unitaryId} checked={unitary} onCheckedChange={setUnitary} />
        <Label htmlFor={unitaryId}>{t("circuits.run.unitary")}</Label>
      </div>
      <div>
        <Button onClick={() => void flows.run()} disabled={!canSend}>
          {busy === "run" ? t("circuits.run.running") : t("circuits.run.simulate")}
        </Button>
      </div>
    </Section>
  );
}
