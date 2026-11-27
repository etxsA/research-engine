import { useMutation, useQuery } from "@tanstack/react-query";
import { fetchClient } from "@/shared/api/client";
import {
  readConventions,
  readPaperCircuits,
  readPaperExport,
  readQasmExport,
  readReading,
  readResult,
  type Conventions,
  type PaperCircuits,
  type PaperExport,
  type QasmExport,
  type Reading,
  type SimulatorResult,
} from "./types";

const PARSE = "/api/v1/circuits/parse";
const RUN = "/api/v1/circuits/run";
const CHECK = "/api/v1/circuits/check";
const QASM = "/api/v1/circuits/qasm";
const CONVENTIONS = "/api/v1/circuits/conventions";
const PAPER_CIRCUITS = "/api/v1/documents/{doc_id}/circuits";
const PAPER_QASM = "/api/v1/documents/{doc_id}/qasm";

type Values = Record<string, string | number> | undefined;

export interface RunInput {
  circuitJson: string;
  input: string | unknown[] | undefined;
  values: Values;
  unitary: boolean;
}

export type CheckTarget =
  { state: string | unknown[] } | { unitary: unknown[][] } | { circuit_json: string } | undefined;

export interface CheckInput {
  circuitJson: string;
  target: CheckTarget;
  input: string | unknown[] | undefined;
  values: Values;
}

export interface QasmInput {
  circuitJson: string;
  values: Values;
}

export interface PaperQasmInput {
  docId: string;
  values: Values;
}

export interface CircuitCalls {
  parse: (latex: string) => Promise<Reading>;
  run: (request: RunInput) => Promise<SimulatorResult>;
  check: (request: CheckInput) => Promise<SimulatorResult>;
  qasm: (request: QasmInput) => Promise<QasmExport>;
  paperCircuits: (docId: string) => Promise<PaperCircuits>;
  paperQasm: (request: PaperQasmInput) => Promise<PaperExport>;
}

export async function parseCircuit(latex: string): Promise<Reading> {
  const { data } = await fetchClient.POST(PARSE, { body: { latex } });
  return readReading(data);
}

export async function runCircuit(request: RunInput): Promise<SimulatorResult> {
  const { data } = await fetchClient.POST(RUN, {
    body: {
      circuit_json: request.circuitJson,
      ...(request.input === undefined ? {} : { input: request.input }),
      ...(request.values === undefined ? {} : { values: request.values }),
      unitary: request.unitary,
    },
  });
  return readResult(data);
}

export async function checkCircuit(request: CheckInput): Promise<SimulatorResult> {
  const { data } = await fetchClient.POST(CHECK, {
    body: {
      circuit_json: request.circuitJson,
      // A target left out means the two sides of a drawn equality; null would be refused.
      ...(request.target === undefined ? {} : { target: request.target }),
      ...(request.input === undefined ? {} : { input: request.input }),
      ...(request.values === undefined ? {} : { values: request.values }),
    },
  });
  return readResult(data);
}

/** The OpenQASM 3 programs of a pasted circuit; a circuit the export cannot express is refused. */
export async function exportQasm(request: QasmInput): Promise<QasmExport> {
  const { data } = await fetchClient.POST(QASM, {
    body: {
      circuit_json: request.circuitJson,
      ...(request.values === undefined ? {} : { values: request.values }),
    },
  });
  return readQasmExport(data);
}

/** The fenced `latex` blocks of a stored paper, numbered as `docingest circuits export` numbers them. */
export async function listPaperCircuits(docId: string): Promise<PaperCircuits> {
  const { data } = await fetchClient.GET(PAPER_CIRCUITS, { params: { path: { doc_id: docId } } });
  return readPaperCircuits(data);
}

/** The files `docingest circuits export --doc` would write for a paper; nothing is written. */
export async function exportPaperQasm(request: PaperQasmInput): Promise<PaperExport> {
  const { data } = await fetchClient.POST(PAPER_QASM, {
    params: { path: { doc_id: request.docId } },
    body: request.values === undefined ? {} : { values: request.values },
  });
  return readPaperExport(data);
}

export function useCircuitCalls(): CircuitCalls {
  const parse = useMutation({ mutationFn: parseCircuit });
  const run = useMutation({ mutationFn: runCircuit });
  const check = useMutation({ mutationFn: checkCircuit });
  const qasm = useMutation({ mutationFn: exportQasm });
  // A paper's blocks are read when the person asks, like the other calls, and never cached: a
  // mutation keeps the flows in charge of which answer is still current.
  const paperCircuits = useMutation({ mutationFn: listPaperCircuits });
  const paperQasm = useMutation({ mutationFn: exportPaperQasm });
  // mutateAsync is stable; the mutation cache sees every failure (a 401 sends the user to sign in)
  return {
    parse: parse.mutateAsync,
    run: run.mutateAsync,
    check: check.mutateAsync,
    qasm: qasm.mutateAsync,
    paperCircuits: paperCircuits.mutateAsync,
    paperQasm: paperQasm.mutateAsync,
  };
}

/** The conventions, the gate table and the limits of the simulator. They do not change while the app is open. */
export function useConventions() {
  return useQuery<Conventions>({
    queryKey: ["circuits", "conventions"],
    queryFn: async () => {
      const { data } = await fetchClient.GET(CONVENTIONS);
      return readConventions(data);
    },
    staleTime: Infinity,
    retry: false,
  });
}
