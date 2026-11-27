import { useId, useRef } from "react";
import { flushSync } from "react-dom";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import { Textarea } from "@/shared/ui/textarea";
import { EXAMPLES, exampleById } from "./examples";
import type { CircuitFlows } from "./flows";
import { PaperPanel } from "./PaperPanel";
import { Section } from "./Section";
import { currentText, useCircuitStore, type InputKind } from "./store";

const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

export function InputPanel({ flows }: { flows: CircuitFlows }) {
  const { t } = useTranslation();
  const kind = useCircuitStore((state) => state.inputKind);
  const showPaper = useCircuitStore((state) => state.showPaper);
  const text = useCircuitStore(currentText);
  const busy = useCircuitStore((state) => state.busy);
  const setKind = useCircuitStore((state) => state.setInputKind);
  const setShowPaper = useCircuitStore((state) => state.setShowPaper);
  const loadLatex = useCircuitStore((state) => state.loadLatex);
  const setText = useCircuitStore((state) => state.setText);
  const loadExample = useCircuitStore((state) => state.loadExample);
  const clear = useCircuitStore((state) => state.clear);
  const exampleId = useId();
  const hintId = useId();
  const latexBox = useRef<HTMLTextAreaElement>(null);

  /** A block of a paper goes to the LaTeX tab and is read there, so every card works on it. */
  const readBlock = (latex: string) => {
    // Draw the LaTeX tab now, so focus can move to its box: the button pressed is gone with the tab.
    flushSync(() => loadLatex(latex));
    latexBox.current?.focus();
    void flows.read();
  };

  const area = (which: InputKind) => (
    <Textarea
      ref={which === "latex" ? latexBox : undefined}
      aria-label={t(`circuits.input.${which}Label`)}
      aria-describedby={hintId}
      className="min-h-44 font-mono text-sm"
      spellCheck={false}
      autoCapitalize="none"
      autoCorrect="off"
      value={text}
      placeholder={t(`circuits.input.${which}Placeholder`)}
      onChange={(event) => setText(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          void flows.read();
        }
      }}
    />
  );

  return (
    <Section title={t("circuits.input.title")} description={t("circuits.input.description")}>
      <Tabs
        value={showPaper ? "paper" : kind}
        onValueChange={(value) => {
          if (value === "paper") return setShowPaper(true);
          const next = value === "json" ? "json" : "latex";
          // Back from the Paper tab to the kind already in use keeps what was read
          if (next === kind) setShowPaper(false);
          else setKind(next);
        }}
      >
        <TabsList aria-label={t("circuits.input.kind")}>
          <TabsTrigger value="latex">{t("circuits.input.latex")}</TabsTrigger>
          <TabsTrigger value="json">{t("circuits.input.json")}</TabsTrigger>
          <TabsTrigger value="paper">{t("circuits.input.paper")}</TabsTrigger>
        </TabsList>
        <TabsContent value="latex">{area("latex")}</TabsContent>
        <TabsContent value="json">{area("json")}</TabsContent>
        <TabsContent value="paper">
          <PaperPanel flows={flows} onReadBlock={readBlock} />
        </TabsContent>
      </Tabs>
      {showPaper ? null : (
        <>
          <p id={hintId} className="text-sm text-muted-foreground">
            {t(`circuits.input.${kind}Hint`)}
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <Button onClick={() => void flows.read()} disabled={busy !== null}>
              {busy === "read" ? t("circuits.input.reading") : t("circuits.input.read")}
            </Button>
            <Button variant="outline" onClick={clear} disabled={busy !== null}>
              {t("circuits.input.clear")}
            </Button>
            <div className="ml-auto grid gap-1.5">
              <Label htmlFor={exampleId}>{t("circuits.input.example")}</Label>
              <select
                id={exampleId}
                className={SELECT_CLASS}
                value=""
                disabled={busy !== null}
                onChange={(event) => {
                  const example = exampleById(event.target.value);
                  if (!example) return;
                  loadExample(example.kind, example.text);
                  void flows.read();
                }}
              >
                <option value="">{t("circuits.input.examplePick")}</option>
                {EXAMPLES.map((example) => (
                  <option key={example.id} value={example.id}>
                    {t(`circuits.examples.${example.id}`)}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </>
      )}
    </Section>
  );
}
