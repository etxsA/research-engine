import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { downloadText } from "@/shared/lib/download";
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
import {
  basisLabel,
  bitsFor,
  formatComplex,
  formatProbability,
  NEGLIGIBLE,
  squaredMagnitude,
} from "./format";
import type { SimulatedPart } from "./types";

const FIRST_ROWS = 64;
const MORE_ROWS = 256;
/** A unitary of 8 wires has 65,536 entries: the page shows a corner and offers the whole matrix. */
export const MATRIX_PREVIEW = 16;

function useRowLimit(total: number) {
  const [limit, setLimit] = useState(FIRST_ROWS);
  return {
    limit,
    shown: Math.min(limit, total),
    more: () => setLimit((value) => value + MORE_ROWS),
  };
}

function ShowMore({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  const { t } = useTranslation();
  if (shown >= total) return null;
  return (
    <div className="mt-2 flex items-center gap-3 text-sm text-muted-foreground">
      <span>{t("circuits.result.showing", { shown, total })}</span>
      <Button variant="outline" size="sm" onClick={onMore}>
        {t("circuits.result.more")}
      </Button>
    </div>
  );
}

/** The amplitudes of the final state. Zero amplitudes are hidden unless asked for. */
export function StateTable({ part }: { part: SimulatedPart }) {
  const { t, i18n } = useTranslation();
  const [withZeros, setWithZeros] = useState(false);
  const state = part.state;
  const rows = useMemo(() => {
    const all = (state ?? []).map((amplitude, index) => ({ amplitude, index }));
    return withZeros ? all : all.filter((row) => squaredMagnitude(row.amplitude) > NEGLIGIBLE);
  }, [state, withZeros]);
  const limit = useRowLimit(rows.length);
  if (!state) {
    return <p className="text-sm text-muted-foreground">{t("circuits.result.noState")}</p>;
  }
  const hidden = state.length - rows.length;
  return (
    <div>
      <Table
        data-testid="state-table"
        scrollLabel={t("circuits.result.stateCaption", { wires: part.wires })}
      >
        <TableCaption>{t("circuits.result.stateCaption", { wires: part.wires })}</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t("circuits.result.basis")}</TableHead>
            <TableHead scope="col">{t("circuits.result.amplitude")}</TableHead>
            <TableHead scope="col">{t("circuits.result.probability")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.slice(0, limit.shown).map(({ amplitude, index }) => (
            <TableRow key={index}>
              <TableCell className="font-mono">{basisLabel(index, part.wires)}</TableCell>
              <TableCell className="font-mono">{formatComplex(amplitude, i18n.language)}</TableCell>
              <TableCell>{formatProbability(squaredMagnitude(amplitude), i18n.language)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <ShowMore shown={limit.shown} total={rows.length} onMore={limit.more} />
      {hidden > 0 || withZeros ? (
        <Button variant="ghost" size="sm" className="mt-1" onClick={() => setWithZeros(!withZeros)}>
          {withZeros
            ? t("circuits.result.hideZeros")
            : t("circuits.result.showZeros", { count: hidden })}
        </Button>
      ) : null}
    </div>
  );
}

/** Outcome probabilities as a bar list. The first listed wire is the most significant bit. */
export function ProbabilityBars({ part, level = 3 }: { part: SimulatedPart; level?: 3 | 4 }) {
  const Heading = level === 3 ? "h3" : "h4";
  const { t, i18n } = useTranslation();
  const [withZeros, setWithZeros] = useState(false);
  const probabilities = part.probabilities;
  const rows = useMemo(() => {
    const all = (probabilities?.values ?? []).map((value, index) => ({ value, index }));
    return withZeros ? all : all.filter((row) => row.value > NEGLIGIBLE);
  }, [probabilities, withZeros]);
  const limit = useRowLimit(rows.length);
  if (!probabilities) return null;
  const bits = probabilities.wires.length || bitsFor(probabilities.values.length);
  const hidden = probabilities.values.length - rows.length;
  return (
    <div>
      <Heading className="mb-2 text-sm font-medium">
        {t("circuits.result.probabilitiesTitle", { wires: probabilities.wires.join(", ") })}
      </Heading>
      <ul className="grid gap-1" data-testid="probabilities">
        {rows.slice(0, limit.shown).map(({ value, index }) => (
          <li
            key={index}
            className="grid grid-cols-[max-content_minmax(0,1fr)_5.5rem] items-center gap-2 text-sm"
          >
            <span className="font-mono whitespace-nowrap">{basisLabel(index, bits)}</span>
            <span aria-hidden="true" className="h-3 overflow-hidden rounded-sm bg-muted">
              <span
                className="block h-full bg-primary"
                style={{ width: `${Math.max(value * 100, value > 0 ? 0.5 : 0)}%` }}
              />
            </span>
            <span className="text-right whitespace-nowrap tabular-nums">
              {formatProbability(value, i18n.language)}
            </span>
          </li>
        ))}
      </ul>
      <ShowMore shown={limit.shown} total={rows.length} onMore={limit.more} />
      {hidden > 0 || withZeros ? (
        <Button variant="ghost" size="sm" className="mt-1" onClick={() => setWithZeros(!withZeros)}>
          {withZeros
            ? t("circuits.result.hideZeros")
            : t("circuits.result.showZeros", { count: hidden })}
        </Button>
      ) : null}
    </div>
  );
}

/** The unitary matrix; a corner of it when it is large, with the whole matrix as a download. */
export function UnitaryMatrix({ part }: { part: SimulatedPart }) {
  const { t, i18n } = useTranslation();
  const unitary = part.unitary;
  if (!unitary) return null;
  const size = unitary.length;
  const shown = Math.min(size, MATRIX_PREVIEW);
  const rows = unitary.slice(0, shown);
  return (
    <div>
      <Table
        data-testid="unitary-table"
        scrollLabel={t("circuits.result.unitaryCaption", { size })}
      >
        <TableCaption>{t("circuits.result.unitaryCaption", { size })}</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">
              <span className="sr-only">{t("circuits.result.row")}</span>
            </TableHead>
            {Array.from({ length: shown }, (_, column) => (
              <TableHead key={column} scope="col" className="font-mono text-xs">
                {basisLabel(column, part.wires)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={index}>
              <TableHead scope="row" className="font-mono text-xs">
                {basisLabel(index, part.wires)}
              </TableHead>
              {row.slice(0, shown).map((entry, column) => (
                <TableCell key={column} className="font-mono text-xs whitespace-nowrap">
                  {formatComplex(entry, i18n.language)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        {shown < size ? <span>{t("circuits.result.corner", { shown, size })}</span> : null}
        <Button
          variant="outline"
          size="sm"
          onClick={() => downloadText("unitary.json", JSON.stringify(unitary), "application/json")}
        >
          {t("circuits.result.downloadUnitary")}
        </Button>
      </div>
    </div>
  );
}
