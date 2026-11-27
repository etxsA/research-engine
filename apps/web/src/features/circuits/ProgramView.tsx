import { Ban, Copy, Download, Info, TriangleAlert } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { copyText, type CopyOutcome } from "@/shared/lib/clipboard";
import { downloadText } from "@/shared/lib/download";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { useCircuitStore } from "./store";
import type { QasmProgram } from "./types";
import { addNamesToValues } from "./ValuesPanel";

/**
 * The pieces of an OpenQASM 3 export, for a pasted circuit and for a paper: each program with
 * Copy and Download, and the refusal, the missing values, the values not used and the notes.
 *
 * A program is saved as `text/plain;charset=utf-8` under the file name the API gave it: OpenQASM
 * has no registered media type, and a text type keeps the browser from guessing another name.
 * The program's text is shown and saved byte for byte, as `docingest circuits export` writes it.
 *
 * Only the refusal is an alert. The other notices are notes, so a result is announced once: by the
 * hidden status sentence when it has programs, by the refusal when it has none.
 */

export const QASM_TYPE = "text/plain;charset=utf-8";

/** One program: its file name, the part of the drawing it is, its text, Copy and Download. */
export function ProgramView({ program, level = 3 }: { program: QasmProgram; level?: 3 | 4 | 5 }) {
  const { t } = useTranslation();
  const Heading = `h${level}` as const;
  const code = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState<CopyOutcome | null>(null);
  const announceCopy = useCircuitStore((state) => state.announceCopy);
  const file = program.filename;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-2" data-testid="qasm-program">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Heading className="font-mono text-sm font-semibold break-all">{file}</Heading>
        <span className="text-sm text-muted-foreground">
          {program.sign
            ? t("circuits.export.partAfter", { part: program.part, sign: program.sign })
            : t("circuits.export.part", { part: program.part })}
          {" \u00b7 "}
          {t("circuits.export.size", { qubits: program.nQubits, clbits: program.nClbits })}
        </span>
      </div>
      <pre
        ref={code}
        role="region"
        aria-label={t("circuits.export.program", { file })}
        // The box scrolls when the program is long or wide, so a keyboard must be able to reach it.
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        className="max-h-80 overflow-auto rounded-lg bg-muted p-3 font-mono text-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {program.text}
      </pre>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          aria-label={t("circuits.export.copyFile", { file })}
          onClick={async () => {
            const outcome = await copyText(program.text, code.current);
            setCopied(outcome);
            announceCopy(outcome, file);
          }}
        >
          <Copy aria-hidden="true" />
          {t("circuits.export.copy")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          aria-label={t("circuits.export.downloadFile", { file })}
          onClick={() => downloadText(file, program.text, QASM_TYPE)}
        >
          <Download aria-hidden="true" />
          {t("circuits.export.download")}
        </Button>
        {copied ? (
          <span className="text-sm text-muted-foreground">
            {t(`circuits.export.copied.${copied}`, { file })}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The export turned the circuit down: its reasons, word for word, and no program. A `quiet` one is
 * a note: a paper's export can refuse several circuits, and its summary is announced once.
 */
export function ExportRefusal({ reasons, quiet = false }: { reasons: string[]; quiet?: boolean }) {
  const { t } = useTranslation();
  return (
    <Alert variant="destructive" data-testid="export-refusal" {...(quiet ? { role: "note" } : {})}>
      <Ban aria-hidden="true" />
      <AlertTitle>{t("circuits.export.refusedTitle")}</AlertTitle>
      <AlertDescription className="grid gap-1">
        <p>{t("circuits.export.refusedBody")}</p>
        <ul className="list-disc pl-5" aria-label={t("circuits.export.reasons")}>
          {reasons.map((reason, index) => (
            <li key={index}>{reason}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

/** The symbols that had no value, with a button that adds their lines to the values. */
export function ExportUnbound({ names }: { names: string[] }) {
  const { t } = useTranslation();
  return (
    <Alert role="note" data-testid="export-unbound">
      <Info aria-hidden="true" />
      <AlertTitle>{t("circuits.export.unboundTitle")}</AlertTitle>
      <AlertDescription className="grid gap-2">
        <p>{t("circuits.export.unboundBody", { names: names.join(", ") })}</p>
        <div>
          <Button variant="outline" size="sm" onClick={() => addNamesToValues(names)}>
            {t("circuits.result.addNames")}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

/** Lines the API wrote about the export (notes, warnings), word for word. */
export function ExportNotes({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <Alert role="note" data-testid="export-notes">
      <TriangleAlert aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <ul className="list-disc pl-5">
          {items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

/** Values whose names match no symbol of the circuit: sent, and not used. */
export function UnmatchedValues({ keys }: { keys: string[] }) {
  const { t } = useTranslation();
  if (keys.length === 0) return null;
  return (
    <Alert role="note" data-testid="export-unmatched">
      <TriangleAlert aria-hidden="true" />
      <AlertTitle>{t("circuits.export.unmatchedTitle")}</AlertTitle>
      <AlertDescription>
        <p>{t("circuits.export.unmatchedBody", { names: keys.join(", ") })}</p>
      </AlertDescription>
    </Alert>
  );
}
