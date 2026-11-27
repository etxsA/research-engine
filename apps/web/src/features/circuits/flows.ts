import { isApiError } from "@/shared/api/errors";
import { MalformedAnswerError, readCircuitDocument, type Reading } from "./types";
import type { CheckInput, CheckTarget, CircuitCalls } from "./api";
import { parseStateInput, parseUnitaryInput, parseValues } from "./values";
import { readStrictJson } from "./strictJson";
import {
  announce,
  announcementFor,
  currentText,
  makeOutcome,
  useCircuitStore,
  type Busy,
  type LocalCode,
  type Outcome,
  type Problem,
  type TargetKind,
} from "./store";

const local = (code: LocalCode, params: Record<string, string | number> = {}): Problem => ({
  kind: "local",
  code,
  params,
});

interface Fail {
  problem: Problem;
}
type Picked<T> = { value: T } | Fail;

const failed = <T>(picked: Picked<T>): picked is Fail => "problem" in picked;

/** A circuit/1 text typed by a person: it must be JSON without a repeated key. */
function checkJsonText(text: string): Fail | null {
  const read = readStrictJson(text);
  if (read.ok) return null;
  if (read.reason === "empty") return { problem: local("emptyInput") };
  if (read.reason === "duplicate") return { problem: local("duplicateKey", { key: read.key }) };
  return { problem: local("invalidJson") };
}

function circuitJson(): Picked<string> {
  const state = useCircuitStore.getState();
  if (state.inputKind === "json") {
    const text = state.jsonText;
    return checkJsonText(text) ?? { value: text };
  }
  const raw = state.reading?.raw[state.selected];
  return raw ? { value: JSON.stringify(raw) } : { problem: local("notRead") };
}

function requestOptions() {
  const state = useCircuitStore.getState();
  const values = parseValues(state.valuesText);
  const firstError = values.errors[0];
  if (firstError?.code === "notName") {
    // the API refuses it too (a 422 at /values/<key>), in the same words
    return { problem: local("badName", { line: firstError.line, name: firstError.name }) };
  }
  if (firstError) {
    return {
      problem: local("badValues", {
        line: firstError.line,
        name: firstError.name,
        code: firstError.code,
      }),
    };
  }
  const input = parseStateInput(state.inputState);
  if (!input.ok) return { problem: local("badState", { where: "input" }) };
  return {
    value: {
      input: input.value,
      values: Object.keys(values.values).length > 0 ? values.values : undefined,
    },
  };
}

/** Show a reading and announce it. */
function showReading(reading: Reading) {
  useCircuitStore.setState({
    reading,
    selected: 0,
    outcome: null,
    problem: null,
    announcement: announce({
      kind: "reading",
      status: reading.status,
      found: reading.circuits.length,
    }),
  });
}

/**
 * Show an answer and announce it. A refusal is announced by its own alert and clears the last
 * announcement, so the hidden status never keeps a sentence the screen no longer shows.
 */
function showOutcome(outcome: Outcome) {
  const said = announcementFor(outcome);
  useCircuitStore.setState({ outcome, announcement: said ? announce(said) : null });
}

/** Show a problem. Its own panel speaks, and the last announcement is cleared with it. */
function showProblem(problem: Problem, extra: { reading?: null; outcome?: null } = {}) {
  useCircuitStore.setState({ ...extra, problem, announcement: null });
}

function toProblem(error: unknown): Problem {
  if (isApiError(error)) return { kind: "api", error };
  if (error instanceof MalformedAnswerError) return { kind: "malformed" };
  return local("network");
}

/**
 * The three actions of the screen. Each reads the store, validates what a person typed, sends one
 * request and writes the answer or the problem back. An answer to an input that has changed since
 * is dropped, so the screen never shows a result for text that is no longer there.
 */
export function createFlows(calls: CircuitCalls) {
  async function guarded<T>(
    busy: Exclude<Busy, null>,
    work: () => Promise<T>,
    apply: (value: T) => void,
    watch: "revision" | "textRevision" = "revision",
  ) {
    const store = useCircuitStore;
    if (store.getState().busy !== null) return;
    const revision = store.getState()[watch];
    store.setState({ busy, problem: null });
    try {
      const value = await work();
      if (store.getState()[watch] === revision) apply(value);
    } catch (error) {
      if (store.getState()[watch] === revision) showProblem(toProblem(error));
    } finally {
      // Nothing else starts while the flag is set (see the first lines), so the flag is always ours.
      store.setState({ busy: null });
    }
  }

  /** Read the text: LaTeX goes to the API, a circuit/1 document is read here. */
  async function read(): Promise<void> {
    const state = useCircuitStore.getState();
    const text = currentText(state);
    if (state.inputKind === "json") {
      const bad = checkJsonText(text);
      if (bad) {
        showProblem(bad.problem, { reading: null, outcome: null });
        return;
      }
      const document = readCircuitDocument(JSON.parse(text));
      if (!document) {
        showProblem(local("notACircuit"), { reading: null, outcome: null });
        return;
      }
      const status =
        document.status === "partial" || document.status === "unparsed"
          ? document.status
          : "parsed";
      const reading: Reading = { status, text: "", circuits: [document], raw: [] };
      showReading(reading);
      return;
    }
    if (text.trim() === "") {
      showProblem(local("emptyInput"));
      return;
    }
    await guarded(
      "read",
      () => calls.parse(text),
      showReading,
      // A reading answers the text only: editing the options meanwhile does not make it stale.
      "textRevision",
    );
  }

  async function run(): Promise<void> {
    const circuit = circuitJson();
    if (failed(circuit)) return showProblem(circuit.problem);
    const options = requestOptions();
    if (failed(options)) return showProblem(options.problem);
    const { selected, unitary } = useCircuitStore.getState();
    await guarded(
      "run",
      () => calls.run({ circuitJson: circuit.value, unitary, ...options.value }),
      (result) =>
        showOutcome(makeOutcome({ command: "run", result, circuit: selected, target: null })),
    );
  }

  /** The target of a check, or the problem with what was typed. A second circuit in LaTeX is read first. */
  async function target(kind: TargetKind, text: string): Promise<Picked<CheckTarget>> {
    if (kind === "drawn") return { value: undefined };
    if (text.trim() === "") return { problem: local("emptyTarget") };
    if (kind === "state") {
      const state = parseStateInput(text);
      if (!state.ok || state.value === undefined)
        return { problem: local("badState", { where: "target" }) };
      return { value: { state: state.value } };
    }
    if (kind === "unitary") {
      const matrix = parseUnitaryInput(text);
      return matrix.ok ? { value: { unitary: matrix.value } } : { problem: local("badUnitary") };
    }
    if (text.trim().startsWith("{")) {
      return checkJsonText(text) ?? { value: { circuit_json: text } };
    }
    const reading = await calls.parse(text);
    if (reading.circuits.length === 0) return { problem: local("secondUnread") };
    if (reading.circuits.length > 1)
      return { problem: local("secondMany", { count: reading.circuits.length }) };
    return { value: { circuit_json: JSON.stringify(reading.raw[0]) } };
  }

  async function check(): Promise<void> {
    const circuit = circuitJson();
    if (failed(circuit)) return showProblem(circuit.problem);
    const options = requestOptions();
    if (failed(options)) return showProblem(options.problem);
    const { selected, targetKind, targetText } = useCircuitStore.getState();
    await guarded(
      "check",
      async () => {
        const picked = await target(targetKind, targetText);
        if (failed(picked)) return picked;
        const request: CheckInput = {
          circuitJson: circuit.value,
          target: picked.value,
          ...options.value,
        };
        return { result: await calls.check(request) };
      },
      (outcome) => {
        if ("problem" in outcome) showProblem(outcome.problem);
        else
          showOutcome(
            makeOutcome({
              command: "check",
              result: outcome.result,
              circuit: selected,
              target: targetKind,
            }),
          );
      },
    );
  }

  return { read, run, check };
}

export type CircuitFlows = ReturnType<typeof createFlows>;
