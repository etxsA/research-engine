/** Parameter values typed as lines, `theta = \pi/2`, and the input state typed as a label or a list. */

export type ValueErrorCode = "format" | "noName" | "noValue" | "duplicate";

export interface ValueError {
  line: number;
  code: ValueErrorCode;
  name: string;
}

export interface ParsedValues {
  values: Record<string, string | number>;
  errors: ValueError[];
}

const NUMBER = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

/**
 * A loose form of the name quantum-sim compares, to catch a name given twice before the request:
 * `theta`, `\theta` and `{theta}` are one symbol. quantum-sim's `symbol_name` is the rule; it
 * also refuses a key that is not one symbol (`thetta`, `pi`).
 */
export function symbolKey(name: string): string {
  return name.replace(/[\\{}\s]/g, "");
}

/** Lines of `name = value`. A number stays a number; anything else is an angle written as text. */
export function parseValues(text: string): ParsedValues {
  const values: Record<string, string | number> = {};
  const errors: ValueError[] = [];
  const seen = new Set<string>();
  text.split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (line === "") return;
    const at = line.indexOf("=");
    if (at < 0) {
      errors.push({ line: index + 1, code: "format", name: line });
      return;
    }
    const name = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim();
    if (name === "") {
      errors.push({ line: index + 1, code: "noName", name });
      return;
    }
    if (value === "") {
      errors.push({ line: index + 1, code: "noValue", name });
      return;
    }
    const key = symbolKey(name);
    if (seen.has(key)) {
      errors.push({ line: index + 1, code: "duplicate", name });
      return;
    }
    seen.add(key);
    values[name] = NUMBER.test(value) ? Number(value) : value;
  });
  return { values, errors };
}

/** Appends an empty line for each name that has no line yet. */
export function addMissingNames(text: string, names: readonly string[]): string {
  const have = new Set(
    text
      .split(/\r?\n/)
      .map((line) => symbolKey(line.split("=")[0] ?? ""))
      .filter(Boolean),
  );
  const missing = names.filter((name) => !have.has(symbolKey(name)));
  if (missing.length === 0) return text;
  const base = text === "" || text.endsWith("\n") ? text : `${text}\n`;
  return base + missing.map((name) => `${name} = `).join("\n");
}

export type StateInput =
  { ok: true; value: string | unknown[] | undefined } | { ok: false; code: "format" };

/** A basis label (`010`, wire 1 first), or a JSON list of amplitudes. Empty means |0...0>. */
export function parseStateInput(text: string): StateInput {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: true, value: undefined };
  if (/^[01]+$/.test(trimmed)) return { ok: true, value: trimmed };
  if (trimmed.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return { ok: true, value: parsed as unknown[] };
    } catch {
      // falls through to the format error
    }
  }
  return { ok: false, code: "format" };
}

export type UnitaryInput = { ok: true; value: unknown[][] } | { ok: false; code: "format" };

/** A matrix as a JSON list of rows; each entry a number or an `[re, im]` pair. */
export function parseUnitaryInput(text: string): UnitaryInput {
  try {
    const parsed: unknown = JSON.parse(text);
    if (
      Array.isArray(parsed) &&
      parsed.length > 0 &&
      (parsed as unknown[]).every((row) => Array.isArray(row))
    ) {
      return { ok: true, value: parsed as unknown[][] };
    }
  } catch {
    // falls through
  }
  return { ok: false, code: "format" };
}
