import { http, HttpResponse } from "msw";
import { isApiError } from "@/shared/api/errors";
import { capturedAnswer } from "@/test/circuits-api";
import { server } from "@/test/server";
import {
  checkCircuit,
  exportPaperQasm,
  exportQasm,
  listPaperCircuits,
  parseCircuit,
  runCircuit,
} from "./api";
import { exampleById } from "./examples";
import { createFlows } from "./flows";
import { initialCircuitState, useCircuitStore } from "./store";
import { readReading } from "./types";

// The flows of the OpenQASM export, over the recorded answers of the API. The other flows are
// tested through the screen (CircuitsPage.test.tsx).

const TELEPORTATION = "c7f460355b1958c3e6cf45b6d3234766cd8c9d49a5a9265864030a0606907a44";
const TRANSMON = "93a7c5f67472745e2a8f2a0f153e44cd66c90936f1b74c81eec6b587c46130ce";

const flows = createFlows({
  parse: parseCircuit,
  run: runCircuit,
  check: checkCircuit,
  qasm: exportQasm,
  paperCircuits: listPaperCircuits,
  paperQasm: exportPaperQasm,
});

const store = useCircuitStore;

/** The screen after the LaTeX of an example was read. */
function readExample(id: string, recording: string) {
  store.setState({
    ...initialCircuitState,
    latexText: exampleById(id)!.text,
    reading: readReading(capturedAnswer(recording).response),
  });
}

/** Every request the flows send, as `METHOD /path`. */
function watchRequests(): string[] {
  const sent: string[] = [];
  server.events.on("request:start", ({ request }) => {
    sent.push(`${request.method} ${new URL(request.url).pathname}`);
  });
  return sent;
}

/** Holds the answer of one endpoint until `release` is called; then gives the recorded one. */
function hold(method: "get" | "post", path: string, recording: string) {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const entry = capturedAnswer(recording);
  server.use(
    http[method](`*/api/v1${path}`, async () => {
      await gate;
      return HttpResponse.json(entry.response, { status: entry.status });
    }),
  );
  return () => release();
}

/** Waits until the request in flight has started. */
async function started(busy: string) {
  await vi.waitFor(() => expect(store.getState().busy).toBe(busy));
}

beforeEach(() => {
  store.setState(initialCircuitState);
});

afterEach(() => {
  server.events.removeAllListeners();
});

describe("exportQasm", () => {
  it("exports the circuit that was read and announces the programs", async () => {
    readExample("bell", "parse bell");
    await flows.exportQasm();
    const state = store.getState();
    expect(state.busy).toBeNull();
    expect(state.problem).toBeNull();
    expect(state.qasm?.circuit).toBe(0);
    expect(state.qasm?.export.programs.map((p) => p.filename)).toEqual(["circuit-p01.qasm"]);
    expect(state.announcement).toMatchObject({ kind: "qasm", programs: 1 });
  });

  it("sends the values typed, pi standing for \\pi", async () => {
    readExample("rotation", "parse rotation");
    store.getState().setValuesText("theta = pi/4");
    await flows.exportQasm();
    expect(store.getState().qasm?.export.status).toBe("ok");
    expect(store.getState().qasm?.export.programs[0]?.text).toContain("// bound: theta = pi/4");
  });

  it("keeps a refusal as the answer, with the names that need a value, and says nothing", async () => {
    readExample("rotation", "parse rotation");
    store.setState({ announcement: { kind: "run", serial: 1 } });
    await flows.exportQasm();
    expect(store.getState().qasm?.export).toMatchObject({ status: "refused", unbound: ["theta"] });
    expect(store.getState().announcement).toBeNull();
  });

  it("sends a circuit/1 text as it was typed", async () => {
    store.setState({ inputKind: "json", jsonText: exampleById("bellJson")!.text });
    await flows.exportQasm();
    expect(store.getState().qasm?.export.status).toBe("ok");
  });

  it("does not need a valid input state, which only a simulation uses", async () => {
    readExample("bell", "parse bell");
    store.getState().setInputState("2");
    await flows.exportQasm();
    expect(store.getState().problem).toBeNull();
    expect(store.getState().qasm?.export.status).toBe("ok");
  });

  it("refuses before any request when nothing was read or a key is not one symbol", async () => {
    const sent = watchRequests();
    await flows.exportQasm();
    expect(store.getState().problem).toMatchObject({ kind: "local", code: "notRead" });
    readExample("rotation", "parse rotation");
    store.getState().setValuesText("thetta = 1");
    await flows.exportQasm();
    expect(store.getState().problem).toMatchObject({
      kind: "local",
      code: "badName",
      params: { line: 1, name: "thetta" },
    });
    store.getState().setValuesText("theta");
    await flows.exportQasm();
    expect(store.getState().problem).toMatchObject({ kind: "local", code: "badValues" });
    expect(sent).toEqual([]);
    expect(store.getState().qasm).toBeNull();
  });

  it("shows the API's problem, and an answer of another shape as one", async () => {
    readExample("bell", "parse bell");
    server.use(
      http.post("*/api/v1/circuits/qasm", () =>
        HttpResponse.json(
          { type: "about:blank", title: "Busy", status: 503, detail: "All circuit jobs are busy." },
          {
            status: 503,
            headers: { "content-type": "application/problem+json", "retry-after": "5" },
          },
        ),
      ),
    );
    await flows.exportQasm();
    const problem = store.getState().problem;
    expect(problem?.kind === "api" && isApiError(problem.error) && problem.error.status).toBe(503);
    server.use(http.post("*/api/v1/circuits/qasm", () => HttpResponse.json({ status: "ok" })));
    await flows.exportQasm();
    expect(store.getState().problem).toEqual({ kind: "malformed" });
    expect(store.getState().qasm).toBeNull();
  });

  it("drops an answer for a circuit edited, values changed or another circuit chosen meanwhile", async () => {
    for (const change of [
      () => store.getState().setText(`${store.getState().latexText} `),
      () => store.getState().setValuesText("theta = 1"),
      () => store.getState().selectCircuit(1),
    ]) {
      readExample("bell", "parse two circuits");
      const release = hold("post", "/circuits/qasm", "qasm bell");
      const exporting = flows.exportQasm();
      await started("qasm");
      change();
      release();
      await exporting;
      expect(store.getState().qasm).toBeNull();
      expect(store.getState().busy).toBeNull();
    }
  });

  it("keeps an answer when only the options of a simulation or a check change meanwhile", async () => {
    readExample("bell", "parse bell");
    const release = hold("post", "/circuits/qasm", "qasm bell");
    const exporting = flows.exportQasm();
    await started("qasm");
    store.getState().setUnitary(true);
    store.getState().setInputState("10");
    store.getState().setTargetKind("unitary");
    release();
    await exporting;
    expect(store.getState().qasm?.export.status).toBe("ok");
  });

  it("sends nothing while another request is on its way", async () => {
    readExample("bell", "parse bell");
    const release = hold("post", "/circuits/qasm", "qasm bell");
    const sent = watchRequests();
    const first = flows.exportQasm();
    await started("qasm");
    await flows.exportQasm();
    await flows.run();
    await flows.exportPaper();
    release();
    await first;
    expect(sent).toEqual(["POST /api/v1/circuits/qasm"]);
  });

  it("is cleared by what it depends on, and only by that", async () => {
    readExample("bell", "parse bell");
    await flows.exportQasm();
    store.getState().setUnitary(true);
    expect(store.getState().qasm).not.toBeNull();
    store.getState().setValuesText("x = 1");
    expect(store.getState().qasm).toBeNull();
    store.getState().setValuesText("");
    await flows.exportQasm();
    store.getState().selectCircuit(0);
    expect(store.getState().qasm).toBeNull();
  });
});

describe("listPaperCircuits", () => {
  it("lists the blocks of the paper chosen and announces how many", async () => {
    store.getState().setPaper(TELEPORTATION);
    await flows.listPaperCircuits();
    const paper = store.getState().paperCircuits;
    expect(paper?.sourceName).toBe("synthetic-teleportation-circuit.tex");
    expect(paper?.blocks.map((block) => block.status)).toEqual(["partial", "parsed"]);
    expect(store.getState().announcement).toMatchObject({ kind: "paperCircuits", blocks: 2 });
  });

  it("lists no block for a paper with no circuits", async () => {
    store.getState().setPaper(TRANSMON);
    await flows.listPaperCircuits();
    expect(store.getState().paperCircuits?.blocks).toEqual([]);
    expect(store.getState().announcement).toMatchObject({ kind: "paperCircuits", blocks: 0 });
  });

  it("asks for a paper before any request, and shows the API's 404", async () => {
    const sent = watchRequests();
    store.getState().setPaper("  ");
    await flows.listPaperCircuits();
    expect(store.getState().problem).toMatchObject({ kind: "local", code: "noPaper" });
    expect(sent).toEqual([]);
    store.getState().setPaper("ffffffffffffffff");
    await flows.listPaperCircuits();
    const problem = store.getState().problem;
    expect(problem?.kind === "api" && problem.error.status).toBe(404);
    expect(problem?.kind === "api" && problem.error.detail).toBe("No such document.");
    expect(store.getState().paperCircuits).toBeNull();
  });

  it("drops the blocks of a paper that is no longer the one chosen", async () => {
    store.getState().setPaper(TELEPORTATION);
    const release = hold(
      "get",
      `/documents/${TELEPORTATION}/circuits`,
      "paper circuits teleportation",
    );
    const listing = flows.listPaperCircuits();
    await started("paperCircuits");
    store.getState().setPaper(TRANSMON);
    release();
    await listing;
    expect(store.getState().paperCircuits).toBeNull();
    expect(store.getState().paperId).toBe(TRANSMON);
  });

  it("keeps the blocks when the values or the pasted circuit change meanwhile", async () => {
    store.getState().setPaper(TELEPORTATION);
    const release = hold(
      "get",
      `/documents/${TELEPORTATION}/circuits`,
      "paper circuits teleportation",
    );
    const listing = flows.listPaperCircuits();
    await started("paperCircuits");
    store.getState().setValuesText("theta = 1");
    store.getState().setText("\\begin{quantikz} & \\gate{H} & \\qw \\end{quantikz}");
    release();
    await listing;
    expect(store.getState().paperCircuits?.blocks).toHaveLength(2);
  });
});

describe("exportPaper", () => {
  it("exports every circuit of the paper, refused ones too, and announces the counts", async () => {
    store.getState().setPaper(TELEPORTATION);
    await flows.exportPaper();
    const exported = store.getState().paperExport?.export;
    expect(exported?.circuits.map((c) => c.status)).toEqual(["refused", "ok"]);
    expect(exported?.circuits[1]?.programs[0]?.filename).toBe("c7f460355b1958c3-b02-c01-p01.qasm");
    expect(store.getState().announcement).toMatchObject({
      kind: "paperQasm",
      programs: 1,
      refused: 1,
    });
  });

  it("sends the values and keeps the warnings", async () => {
    store.getState().setPaper(TELEPORTATION);
    store.getState().setValuesText("theta = 1");
    await flows.exportPaper();
    expect(store.getState().paperExport?.export.warnings).toEqual([
      "binding 'theta' matches no symbol of any exported circuit",
    ]);
    store.getState().setPaper(TRANSMON);
    store.getState().setValuesText("");
    await flows.exportPaper();
    expect(store.getState().paperExport?.export.warnings).toEqual([
      "synthetic-transmon-coherence.pdf: no circuits found",
    ]);
  });

  it("refuses before any request with no paper or a key that is not one symbol", async () => {
    const sent = watchRequests();
    await flows.exportPaper();
    expect(store.getState().problem).toMatchObject({ code: "noPaper" });
    store.getState().setPaper(TELEPORTATION);
    store.getState().setValuesText("pi = 1");
    await flows.exportPaper();
    expect(store.getState().problem).toMatchObject({ code: "badName", params: { name: "pi" } });
    expect(sent).toEqual([]);
  });

  it("shows the API's 404 for an unknown paper", async () => {
    store.getState().setPaper("ffffffffffffffff");
    await flows.exportPaper();
    const problem = store.getState().problem;
    expect(problem?.kind === "api" && problem.error.status).toBe(404);
    expect(store.getState().paperExport).toBeNull();
  });

  it("drops an export when the paper or the values change meanwhile", async () => {
    for (const change of [
      () => store.getState().setPaper(TRANSMON),
      () => store.getState().setValuesText("theta = 1"),
    ]) {
      store.setState(initialCircuitState);
      store.getState().setPaper(TELEPORTATION);
      const release = hold("post", `/documents/${TELEPORTATION}/qasm`, "paper qasm teleportation");
      const exporting = flows.exportPaper();
      await started("paperQasm");
      change();
      release();
      await exporting;
      expect(store.getState().paperExport).toBeNull();
      expect(store.getState().busy).toBeNull();
    }
  });

  it("keeps an export when only the pasted circuit changes meanwhile", async () => {
    store.getState().setPaper(TELEPORTATION);
    const release = hold("post", `/documents/${TELEPORTATION}/qasm`, "paper qasm teleportation");
    const exporting = flows.exportPaper();
    await started("paperQasm");
    store.getState().setText("\\begin{quantikz} & \\gate{H} & \\qw \\end{quantikz}");
    store.getState().setUnitary(true);
    release();
    await exporting;
    expect(store.getState().paperExport?.export.circuits).toHaveLength(2);
  });

  it("is cleared with the paper's blocks when another paper is chosen, and by the values", async () => {
    store.getState().setPaper(TELEPORTATION);
    await flows.listPaperCircuits();
    await flows.exportPaper();
    store.getState().setValuesText("theta = 1");
    expect(store.getState().paperExport).toBeNull();
    expect(store.getState().paperCircuits).not.toBeNull();
    await flows.exportPaper();
    store.getState().setPaper(TRANSMON);
    expect(store.getState().paperExport).toBeNull();
    expect(store.getState().paperCircuits).toBeNull();
    store.getState().clear();
    expect(store.getState().paperId).toBe("");
  });
});
