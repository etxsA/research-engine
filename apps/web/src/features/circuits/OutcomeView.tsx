import { Ban, CircleCheck, CircleX, Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui/table";
import { Section } from "./Section";
import { ProbabilityBars, StateTable, UnitaryMatrix } from "./StateView";
import { useCircuitStore, type Outcome } from "./store";
import type { Comparison, SimulatorResult } from "./types";
import { addNamesToValues } from "./ValuesPanel";

/** Nothing was computed: the simulator turned the circuit down and gave its reasons, word for word. */
function Refusal({ reasons }: { reasons: string[] }) {
  const { t } = useTranslation();
  return (
    <Alert variant="destructive" data-testid="refusal">
      <Ban aria-hidden="true" />
      <AlertTitle>{t("circuits.result.refusedTitle")}</AlertTitle>
      <AlertDescription className="grid gap-1">
        <p>{t("circuits.result.refusedBody")}</p>
        <ul className="list-disc pl-5" aria-label={t("circuits.result.reasons")}>
          {reasons.map((reason, index) => (
            <li key={index}>{reason}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

function deviation(value: number, locale: string): string {
  const small = value !== 0 && Math.abs(value) < 1e-4;
  return new Intl.NumberFormat(locale, {
    ...(small ? { notation: "scientific" as const } : {}),
    maximumSignificantDigits: 6,
  }).format(value);
}

function Verdict({ equal, comparison }: { equal: boolean; comparison: Comparison | undefined }) {
  const { t, i18n } = useTranslation();
  const Icon = equal ? CircleCheck : CircleX;
  return (
    <div className="flex flex-wrap items-center gap-3" data-testid={equal ? "equal" : "not-equal"}>
      <Badge variant={equal ? "default" : "outline"} className="h-7 px-3 text-sm">
        <Icon aria-hidden="true" />
        {equal ? t("circuits.result.equal") : t("circuits.result.notEqual")}
      </Badge>
      {comparison ? (
        <span className="text-sm text-muted-foreground">
          {t("circuits.result.deviation", {
            value: deviation(comparison.max_deviation, i18n.language),
          })}
        </span>
      ) : null}
    </div>
  );
}

function ComparisonTable({ comparisons }: { comparisons: Comparison[] }) {
  const { t, i18n } = useTranslation();
  const plain = new Intl.NumberFormat(i18n.language, { maximumSignificantDigits: 6 });
  const tiny = new Intl.NumberFormat(i18n.language, {
    notation: "scientific",
    maximumSignificantDigits: 6,
  });
  // A deviation of rounding size (2.2e-16) reads better as a power of ten than as 18 zeros
  const number = {
    format: (value: number) => (value !== 0 && Math.abs(value) < 1e-4 ? tiny : plain).format(value),
  };
  return (
    <Table data-testid="comparisons" scrollLabel={t("circuits.result.comparisons")}>
      <TableCaption>{t("circuits.result.comparisons")}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t("circuits.result.between")}</TableHead>
          <TableHead scope="col">{t("circuits.result.method")}</TableHead>
          <TableHead scope="col">{t("circuits.result.equalColumn")}</TableHead>
          <TableHead scope="col">{t("circuits.result.exact")}</TableHead>
          <TableHead scope="col">{t("circuits.result.phase")}</TableHead>
          <TableHead scope="col">{t("circuits.result.maxDeviation")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {comparisons.map((comparison, index) => (
          <TableRow key={index}>
            <TableCell className="font-mono">{comparison.between.join(" / ")}</TableCell>
            <TableCell>{comparison.method}</TableCell>
            <TableCell>
              {comparison.equal ? t("circuits.result.yes") : t("circuits.result.no")}
            </TableCell>
            <TableCell>
              {comparison.exact ? t("circuits.result.yes") : t("circuits.result.no")}
            </TableCell>
            <TableCell className="tabular-nums">{number.format(comparison.phase)}</TableCell>
            <TableCell className="tabular-nums">
              {number.format(comparison.max_deviation)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Unbound({ names }: { names: string[] }) {
  const { t } = useTranslation();
  return (
    <Alert data-testid="unbound">
      <Info aria-hidden="true" />
      <AlertTitle>{t("circuits.result.symbolicTitle")}</AlertTitle>
      <AlertDescription className="grid gap-2">
        <p>{t("circuits.result.symbolicBody", { names: names.join(", ") })}</p>
        <div>
          <Button variant="outline" size="sm" onClick={() => addNamesToValues(names)}>
            {t("circuits.result.addNames")}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

function Facts({ result }: { result: SimulatorResult }) {
  const { t } = useTranslation();
  const values = Object.entries(result.values);
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
      {result.input !== null ? (
        <>
          <dt className="text-muted-foreground">{t("circuits.result.inputUsed")}</dt>
          <dd className="font-mono break-words">{result.input}</dd>
        </>
      ) : null}
      {values.length > 0 ? (
        <>
          <dt className="text-muted-foreground">{t("circuits.result.valuesUsed")}</dt>
          <dd className="font-mono break-words">
            {values.map(([name, value]) => `${name} = ${value}`).join(", ")}
          </dd>
        </>
      ) : null}
    </dl>
  );
}

function RunBody({ result }: { result: SimulatorResult }) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      {result.circuits.map((part) => (
        <div key={part.part} className="grid grid-cols-[minmax(0,1fr)] gap-4">
          {result.circuits.length > 1 ? (
            <h3 className="text-sm font-semibold">
              {t(part.sign ? "circuits.result.rightPart" : "circuits.result.leftPart", {
                sign: part.sign ?? "",
              })}
            </h3>
          ) : null}
          <StateTable part={part} />
          <ProbabilityBars part={part} level={result.circuits.length > 1 ? 4 : 3} />
          {part.measuredWires.length > 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("circuits.result.measured", { wires: part.measuredWires.join(", ") })}
            </p>
          ) : null}
          <UnitaryMatrix part={part} />
        </div>
      ))}
    </div>
  );
}

function CheckBody({ result }: { result: SimulatorResult }) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      {result.equalToTarget !== null ? (
        <Verdict equal={result.equalToTarget} comparison={result.comparisons[0]} />
      ) : null}
      {result.comparisons.length > 0 ? <ComparisonTable comparisons={result.comparisons} /> : null}
      <ul className="text-sm text-muted-foreground">
        {result.circuits.map((part) => (
          <li key={part.part}>
            {t("circuits.result.partSummary", {
              part: part.part,
              wires: part.wires,
              operations: part.operations ?? 0,
            })}
          </li>
        ))}
      </ul>
    </div>
  );
}

function OutcomeBody({ outcome }: { outcome: Outcome }) {
  const { t } = useTranslation();
  const { result } = outcome;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      {result.status === "refused" ? <Refusal reasons={result.refused} /> : null}
      {result.status === "symbolic" ? <Unbound names={result.unbound} /> : null}
      {result.status === "ok" ? (
        <>
          <Facts result={result} />
          {outcome.command === "run" ? <RunBody result={result} /> : <CheckBody result={result} />}
        </>
      ) : null}
      {result.notes.length > 0 ? (
        <div>
          <h3 className="mb-1 text-sm font-medium">{t("circuits.result.notes")}</h3>
          <ul className="list-disc pl-5 text-sm">
            {result.notes.map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <p className="text-sm">
        <a
          href="#circuit-conventions"
          className="underline underline-offset-3"
          onClick={(event) => {
            event.preventDefault();
            const card = document.getElementById("circuit-conventions");
            card?.querySelector("details")?.setAttribute("open", "");
            card?.scrollIntoView({ block: "start" });
            card?.querySelector<HTMLElement>("h2")?.focus();
          }}
        >
          {t("circuits.result.seeConventions")}
        </a>
      </p>
    </div>
  );
}

export function OutcomeView() {
  const { t } = useTranslation();
  const outcome = useCircuitStore((state) => state.outcome);
  if (!outcome) return null;
  const title =
    outcome.command === "run" ? t("circuits.result.runTitle") : t("circuits.result.checkTitle");
  return (
    <Section title={title}>
      <OutcomeBody key={outcome.serial} outcome={outcome} />
    </Section>
  );
}
