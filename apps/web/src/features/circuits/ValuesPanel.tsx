import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Label } from "@/shared/ui/label";
import { Textarea } from "@/shared/ui/textarea";
import { Section } from "./Section";
import { useCircuitStore } from "./store";

/** The values of the circuit's symbols, one `name = value` per line. Simulate, Check and Export use them. */
export function ValuesPanel() {
  const { t } = useTranslation();
  const valuesText = useCircuitStore((state) => state.valuesText);
  const setValuesText = useCircuitStore((state) => state.setValuesText);
  const valuesId = useId();
  const hintId = useId();
  return (
    <Section title={t("circuits.values.title")} description={t("circuits.values.description")}>
      <div className="grid gap-1.5">
        <Label htmlFor={valuesId}>{t("circuits.values.label")}</Label>
        <Textarea
          id={valuesId}
          aria-describedby={hintId}
          className="min-h-16 font-mono text-sm"
          spellCheck={false}
          autoCapitalize="none"
          autoCorrect="off"
          value={valuesText}
          placeholder={"theta = \\pi/2"}
          onChange={(event) => setValuesText(event.target.value)}
        />
        <p id={hintId} className="text-sm text-muted-foreground">
          {t("circuits.values.hint")}
        </p>
      </div>
    </Section>
  );
}
