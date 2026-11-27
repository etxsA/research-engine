import { useId } from "react";
import { flushSync } from "react-dom";
import { useTranslation } from "react-i18next";
import { Label } from "@/shared/ui/label";
import { Textarea } from "@/shared/ui/textarea";
import { Section } from "./Section";
import { useCircuitStore } from "./store";
import { addMissingNames } from "./values";

/** The id of the values box, so a button elsewhere can send focus to it. */
export const VALUES_ID = "circuit-values";

/**
 * Add a `name = ` line for each symbol with no value, and put the cursor at the end of the box.
 * The note with the button goes away with the old outcome, so focus must not fall to the page.
 */
export function addNamesToValues(names: string[]): void {
  const { valuesText, setValuesText } = useCircuitStore.getState();
  flushSync(() => setValuesText(addMissingNames(valuesText, names)));
  const box = document.getElementById(VALUES_ID);
  if (box instanceof HTMLTextAreaElement) {
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }
}

/** The values of the circuit's symbols, one `name = value` per line. Simulate, Check and Export use them. */
export function ValuesPanel() {
  const { t } = useTranslation();
  const valuesText = useCircuitStore((state) => state.valuesText);
  const setValuesText = useCircuitStore((state) => state.setValuesText);
  const hintId = useId();
  return (
    <Section title={t("circuits.values.title")} description={t("circuits.values.description")}>
      <div className="grid gap-1.5">
        <Label htmlFor={VALUES_ID}>{t("circuits.values.label")}</Label>
        <Textarea
          id={VALUES_ID}
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
