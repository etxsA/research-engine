/** Parameter values typed as lines, `theta = \pi/2`, and the input state typed as a label or a list. */

export type ValueErrorCode = "format" | "noName" | "notName" | "noValue" | "duplicate";

export interface ValueError {
  line: number;
  code: ValueErrorCode;
  name: string;
}

export interface ParsedValues {
  /** Each value as typed (trimmed): the API reads it with quantum-sim's one value rule. */
  values: Record<string, string>;
  errors: ValueError[];
}

// A copy of quantum-sim's binding-name rule (`quantum_sim/angles.py`: `symbol_name`, `GREEK`,
// `MAX_CHARS`, `_BINDING_KEY`); `values.test.ts` holds the cases of its tests, `test_angles.py`.
const MAX_CHARS = 500;
const GREEK = new Set(
  (
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu " +
    "xi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega " +
    "Gamma Delta Theta Lambda Xi Sigma Upsilon Phi Psi Omega"
  ).split(" "),
);
const CONSTANT_LETTERS = new Set(["e", "E", "i", "j"]);
const BINDING_KEY = /^(\\?)([A-Za-z]+)(?:_(?:([A-Za-z0-9])|\{([A-Za-z0-9]{1,8})\}))?$/;
// What Python's `str.strip()` strips (`str.isspace`), which is not quite what `trim()` strips.
const SPACE =
  "\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const OUTER_SPACE = new RegExp(`^[${SPACE}]+|[${SPACE}]+$`, "g");

/** More than `max` characters, counted as Python counts them (code points). */
function longerThan(text: string, max: number): boolean {
  if (text.length <= max) return false;
  let count = 0;
  for (let i = 0; i < text.length; i += text.codePointAt(i)! > 0xffff ? 2 : 1) {
    if (++count > max) return true;
  }
  return false;
}

/**
 * The name of the free parameter that a key of the values spells, or null when the key is not
 * one symbol: quantum-sim's `symbol_name`, the rule the API applies to every key. `theta`,
 * `\theta`, `{\theta}` and ` theta ` are `theta`, `\theta_{1}` is `theta_1`; `thetta`, `pi`,
 * `e`, `the{ta}` and `theta_12` (a digit after the subscript) are not names. Two keys with the
 * same name bind one symbol twice.
 */
export function symbolKey(key: string): string | null {
  if (longerThan(key, MAX_CHARS)) return null; // no symbol is this long
  let text = key.replace(OUTER_SPACE, "");
  while (text.length >= 2 && text.startsWith("{") && text.endsWith("}")) {
    text = text.slice(1, -1).replace(OUTER_SPACE, "");
  }
  const match = BINDING_KEY.exec(text);
  if (!match) return null;
  const [, backslash, word = "", letter, braced] = match;
  if (!GREEK.has(word) && (backslash || word.length > 1 || CONSTANT_LETTERS.has(word))) {
    return null;
  }
  const subscript = letter ?? braced;
  return subscript ? `${word}_${subscript}` : word;
}

/**
 * The key to write for a symbol: a key as it is (the export's `unbound` lists keys, such as
 * `theta_{12}`), or a reader's name made a key (the simulator's `unbound` lists names, and
 * `theta_12` is written `theta_{12}`, as the binding rule reads `theta_12` as no name).
 */
export function bindingKey(name: string): string {
  if (symbolKey(name) !== null) return name.replace(OUTER_SPACE, "");
  const at = name.indexOf("_");
  if (at > 0) {
    const key = `${name.slice(0, at)}_{${name.slice(at + 1)}}`;
    if (symbolKey(key) === name) return key;
  }
  return name;
}

/**
 * Lines of `name = value`. Each value is sent as the text typed, trimmed, numbers too: the API
 * reads every value with quantum-sim's `parse_value`, as `--set` does, so `0.50` stays `0.50` in
 * an export's `// bound:` line and `1e3` is refused there (no exponent notation), not turned
 * into 1000 here.
 */
export function parseValues(text: string): ParsedValues {
  const values: Record<string, string> = {};
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
    const key = symbolKey(name);
    if (key === null) {
      errors.push({ line: index + 1, code: "notName", name });
      return;
    }
    if (value === "") {
      errors.push({ line: index + 1, code: "noValue", name });
      return;
    }
    if (seen.has(key)) {
      errors.push({ line: index + 1, code: "duplicate", name });
      return;
    }
    seen.add(key);
    values[name] = value;
  });
  return { values, errors };
}

/**
 * Appends an empty line for each symbol that has no line yet. `names` may be a reader's names or
 * keys (see `bindingKey`); each line is written as a key the binding rule takes.
 */
export function addMissingNames(text: string, names: readonly string[]): string {
  const have = new Set(
    text
      .split(/\r?\n/)
      .map((line) => symbolKey(line.split("=")[0] ?? ""))
      .filter((key) => key !== null),
  );
  const missing: string[] = [];
  for (const name of names) {
    const key = bindingKey(name);
    const symbol = symbolKey(key) ?? key;
    if (have.has(symbol)) continue;
    have.add(symbol);
    missing.push(key);
  }
  if (missing.length === 0) return text;
  const base = text === "" || text.endsWith("\n") ? text : `${text}\n`;
  return base + missing.map((key) => `${key} = `).join("\n");
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
