import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedAnswer } from "@/test/circuits-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";
import { initialCircuitState, useCircuitStore } from "./store";

preloadScreens(() => import("./CircuitsPage"));

type User = ReturnType<typeof userEvent.setup>;

const TELEPORTATION = "c7f460355b1958c3e6cf45b6d3234766cd8c9d49a5a9265864030a0606907a44";
const TELEPORTATION_TITLE =
  "A compact teleportation circuit with mid-circuit measurement (synthetic)";
const TRANSMON_TITLE = "Coherence limits of fixed-frequency transmon qubits (synthetic)";
const BELL_PAIR_FILE = "c7f460355b1958c3-b02-c01-p01.qasm";

interface RecordedPaperExport {
  circuits: { programs: { filename: string; text: string }[] }[];
}

beforeEach(() => {
  useCircuitStore.setState(initialCircuitState);
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

async function openPage(path = "/circuits"): Promise<User> {
  const user = userEvent.setup();
  renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Circuits" });
  return user;
}

/** Open the Paper tab, search, and choose the paper titled `title`. */
async function choosePaper(user: User, title: string) {
  await user.click(screen.getByRole("tab", { name: "Paper" }));
  await user.type(screen.getByLabelText("Title or id"), title.split(" ")[2]!);
  await user.click(screen.getByRole("button", { name: "Find papers" }));
  const results = await screen.findByRole("list", { name: "Papers found" });
  await user.click(within(results).getByRole("button", { name: title }));
  await screen.findByTestId("paper-blocks");
}

const regions = () =>
  within(screen.getByTestId("live-status"))
    .getAllByRole("status")
    .map((region) => region.textContent);

/** The URLs of the requests the screen sends while `work` runs. */
async function requestsDuring(work: () => Promise<void>): Promise<string[]> {
  const sent: string[] = [];
  server.events.on("request:start", ({ request }) => void sent.push(request.url));
  try {
    await work();
  } finally {
    server.events.removeAllListeners();
  }
  return sent;
}

describe("the Paper tab", () => {
  it("finds a paper and lists its circuit blocks with their status", async () => {
    const user = await openPage();
    await choosePaper(user, TELEPORTATION_TITLE);
    const blocks = screen.getAllByTestId("paper-block");
    expect(blocks).toHaveLength(2);
    expect(
      within(blocks[0]!).getByRole("heading", { level: 4, name: "Block 1" }),
    ).toBeInTheDocument();
    expect(within(blocks[0]!).getByText("Read in part")).toBeInTheDocument();
    expect(within(blocks[1]!).getByText("Read in full")).toBeInTheDocument();
    expect(within(blocks[1]!).getByText("Circuits read: 1")).toBeInTheDocument();
    const paper = screen.getByTestId("paper-blocks");
    expect(
      within(paper).getByRole("heading", { name: "synthetic-teleportation-circuit.tex" }),
    ).toBeInTheDocument();
    expect(within(paper).getByText(`Id: ${TELEPORTATION}`)).toBeInTheDocument();
    expect(regions().filter(Boolean)).toEqual(["Circuit blocks of the paper: 2."]);
    // the cards of the circuit read stay where they are
    expect(screen.getByRole("button", { name: "Simulate" })).toBeDisabled();
  });

  it("says so when no stored paper matches", async () => {
    server.use(http.get("*/api/v1/documents", () => HttpResponse.json({ items: [], total: 0 })));
    const user = await openPage();
    await user.click(screen.getByRole("tab", { name: "Paper" }));
    await user.type(screen.getByLabelText("Title or id"), "nothing");
    await user.click(screen.getByRole("button", { name: "Find papers" }));
    expect(await screen.findByText("No stored paper matches.")).toBeInTheDocument();
  });

  it("sends the search to the API's own paper search", async () => {
    let asked: URLSearchParams | undefined;
    server.use(
      http.get("*/api/v1/documents", ({ request }) => {
        asked = new URL(request.url).searchParams;
        return HttpResponse.json({ items: [], total: 0 });
      }),
    );
    const user = await openPage();
    await user.click(screen.getByRole("tab", { name: "Paper" }));
    await user.type(screen.getByLabelText("Title or id"), "  c7f46035 ");
    await user.click(screen.getByRole("button", { name: "Find papers" }));
    await screen.findByText("No stored paper matches.");
    expect(asked?.get("query")).toBe("c7f46035");
    expect(asked?.get("limit")).toBe("20");
  });

  it("exports the teleportation paper: one program and one refusal, as the CLI names them", async () => {
    const user = await openPage();
    await choosePaper(user, TELEPORTATION_TITLE);
    await user.click(screen.getByRole("button", { name: "Export this paper" }));
    const result = await screen.findByTestId("paper-export");
    expect(
      within(result).getByRole("heading", {
        level: 3,
        name: "Programs of synthetic-teleportation-circuit.tex",
      }),
    ).toBeInTheDocument();
    expect(within(result).getByText("Programs: 1. Refused circuits: 1.")).toBeInTheDocument();
    const [first, second] = within(result).getAllByTestId("paper-circuit");
    expect(within(first!).getByRole("heading", { name: "Block 1, circuit 1" })).toBeInTheDocument();
    const refusal = within(first!).getByTestId("export-refusal");
    // one summary is announced for the whole paper, so a refusal in it is a note, not an alert
    expect(refusal).toHaveAttribute("role", "note");
    expect(
      within(refusal).getByText("status: the circuit was read as 'partial', not 'parsed'"),
    ).toBeInTheDocument();
    expect(within(first!).queryByTestId("qasm-program")).not.toBeInTheDocument();
    expect(
      within(second!).getByRole("heading", { name: "Block 2, circuit 1" }),
    ).toBeInTheDocument();
    const recorded = capturedAnswer("paper qasm teleportation")
      .response as unknown as RecordedPaperExport;
    const program = recorded.circuits[1]!.programs[0]!;
    expect(program.filename).toBe(BELL_PAIR_FILE);
    const box = within(second!).getByRole("region", {
      name: `OpenQASM 3 program ${BELL_PAIR_FILE}`,
    });
    expect(box.textContent).toBe(program.text);
    expect(box).toHaveAttribute("tabindex", "0");
    expect(
      within(second!).getByRole("heading", { level: 5, name: BELL_PAIR_FILE }),
    ).toBeInTheDocument();
    expect(
      within(second!).getByRole("button", { name: `Copy ${BELL_PAIR_FILE}` }),
    ).toBeInTheDocument();
    expect(regions().filter(Boolean)).toEqual([
      "Export of the paper finished. Programs: 1. Refused circuits: 1.",
    ]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("saves a paper's program under the file name the CLI writes", async () => {
    const saved: string[] = [];
    URL.createObjectURL = () => "blob:paper";
    URL.revokeObjectURL = () => undefined;
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push(this.download);
    });
    try {
      const user = await openPage();
      await choosePaper(user, TELEPORTATION_TITLE);
      await user.click(screen.getByRole("button", { name: "Export this paper" }));
      await user.click(await screen.findByRole("button", { name: `Download ${BELL_PAIR_FILE}` }));
      expect(saved).toEqual([BELL_PAIR_FILE]);
    } finally {
      click.mockRestore();
    }
  });

  it("says a paper has no circuit blocks, and shows the export's warning", async () => {
    const user = await openPage();
    await choosePaper(user, TRANSMON_TITLE);
    expect(
      screen.getByText("This paper has no circuit blocks: its text has no fenced latex block."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("paper-block")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Export this paper" }));
    const result = await screen.findByTestId("paper-export");
    expect(within(result).getByText("Programs: 0. Refused circuits: 0.")).toBeInTheDocument();
    const warnings = within(result).getByTestId("export-notes");
    expect(within(warnings).getByText("Warnings")).toBeInTheDocument();
    expect(
      within(warnings).getByText("synthetic-transmon-coherence.pdf: no circuits found"),
    ).toBeInTheDocument();
  });

  it("exports with the values and shows the warning for a value no circuit uses", async () => {
    const user = await openPage();
    await choosePaper(user, TELEPORTATION_TITLE);
    await user.type(screen.getByLabelText("Parameter values"), "theta = 1");
    await user.click(screen.getByRole("button", { name: "Export this paper" }));
    const result = await screen.findByTestId("paper-export");
    expect(
      within(result).getByText("binding 'theta' matches no symbol of any exported circuit"),
    ).toBeInTheDocument();
  });

  it("refuses a value whose name is not one symbol before sending the export", async () => {
    const user = await openPage();
    await choosePaper(user, TELEPORTATION_TITLE);
    await user.type(screen.getByLabelText("Parameter values"), "pi = 1");
    const sent = await requestsDuring(async () => {
      await user.click(screen.getByRole("button", { name: "Export this paper" }));
      await screen.findByTestId("problem");
    });
    expect(sent.filter((url) => url.includes("/qasm"))).toEqual([]);
    expect(screen.getByTestId("problem")).toHaveTextContent(
      'Line 1: "pi" is not a parameter name.',
    );
  });

  it("reads a block in the LaTeX tab, where every card works on it", async () => {
    const user = await openPage();
    await choosePaper(user, TELEPORTATION_TITLE);
    const block = (capturedAnswer("parse paper block 2").request as { latex: string }).latex;
    await user.click(screen.getByRole("button", { name: "Read this block: block 2" }));
    expect(screen.getByRole("tab", { name: "LaTeX", selected: true })).toBeInTheDocument();
    const box = screen.getByLabelText("LaTeX code of one circuit");
    expect(box).toHaveValue(block);
    expect(box).toHaveFocus();
    expect(await screen.findByText("Read in full")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Gates of circuit 1/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Export" }));
    const program = await screen.findByRole("region", {
      name: "OpenQASM 3 program circuit-p01.qasm",
    });
    expect(program.textContent).toBe(
      (capturedAnswer("qasm paper block 2").response as { programs: { text: string }[] })
        .programs[0]!.text,
    );
    // the paper stays chosen on its tab
    await user.click(screen.getByRole("tab", { name: "Paper" }));
    expect(screen.getAllByTestId("paper-block")).toHaveLength(2);
    // and going back keeps what was read
    await user.click(screen.getByRole("tab", { name: "LaTeX" }));
    expect(screen.getByText("Read in full")).toBeInTheDocument();
  });
});

describe("the ?doc= link", () => {
  it("is dropped from the address when a block is read in the LaTeX tab", async () => {
    const user = userEvent.setup();
    const app = renderApp(`/circuits?doc=${TELEPORTATION}`);
    await screen.findByTestId("paper-blocks");
    expect(app.router.state.location.search).toBe(`?doc=${TELEPORTATION}`);
    await user.click(screen.getByRole("button", { name: "Read this block: block 2" }));
    expect(screen.getByRole("tab", { name: "LaTeX", selected: true })).toBeInTheDocument();
    expect(app.router.state.location.search).toBe("");
    await screen.findByText("Read in full");
  });

  it("opens the Paper tab on that paper", async () => {
    const sent = await requestsDuring(async () => {
      await openPage(`/circuits?doc=${TELEPORTATION}`);
      await screen.findByTestId("paper-blocks");
    });
    expect(screen.getByRole("tab", { name: "Paper", selected: true })).toBeInTheDocument();
    expect(screen.getAllByTestId("paper-block")).toHaveLength(2);
    // read once: the blocks are asked for one time, and no search is made
    expect(sent.filter((url) => url.endsWith(`/documents/${TELEPORTATION}/circuits`))).toHaveLength(
      1,
    );
    expect(sent.filter((url) => url.includes("/documents?"))).toEqual([]);
  });

  it("says when the paper is not stored", async () => {
    await openPage("/circuits?doc=ffffffffffffffff");
    const problem = await screen.findByTestId("problem");
    expect(within(problem).getByText("Not found")).toBeInTheDocument();
    expect(within(problem).getByText("No such document.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Paper", selected: true })).toBeInTheDocument();
    expect(screen.queryByTestId("paper-blocks")).not.toBeInTheDocument();
  });

  it("is rewritten to the paper chosen, and dropped when the Paper tab is left", async () => {
    const user = userEvent.setup();
    const app = renderApp(`/circuits?doc=${TELEPORTATION}`);
    await screen.findByTestId("paper-blocks");
    await user.type(screen.getByLabelText("Title or id"), "transmon");
    await user.click(screen.getByRole("button", { name: "Find papers" }));
    const results = await screen.findByRole("list", { name: "Papers found" });
    await user.click(within(results).getByRole("button", { name: TRANSMON_TITLE }));
    await screen.findByRole("heading", { name: "synthetic-transmon-coherence.pdf" });
    const search = () => new URLSearchParams(app.router.state.location.search);
    expect(search().get("doc")).toMatch(/^[0-9a-f]{64}$/);
    expect(search().get("doc")).not.toBe(TELEPORTATION);
    await user.click(screen.getByRole("tab", { name: "LaTeX" }));
    expect(app.router.state.location.search).toBe("");
  });

  it("is not applied again when another paper is chosen", async () => {
    const user = await openPage(`/circuits?doc=${TELEPORTATION}`);
    await screen.findByTestId("paper-blocks");
    await user.type(screen.getByLabelText("Title or id"), "transmon");
    await user.click(screen.getByRole("button", { name: "Find papers" }));
    const results = await screen.findByRole("list", { name: "Papers found" });
    await user.click(within(results).getByRole("button", { name: TRANSMON_TITLE }));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "synthetic-transmon-coherence.pdf" }),
      ).toBeInTheDocument(),
    );
  });
});

it("is in Spanish when the language is", async () => {
  const user = await openPage();
  await user.click(screen.getByRole("button", { name: "Language: English" }));
  await user.click(screen.getByRole("menuitemradio", { name: "Español" }));
  await user.click(await screen.findByRole("tab", { name: "Artículo" }));
  await user.click(screen.getByRole("button", { name: "Buscar artículos" }));
  const results = await screen.findByRole("list", { name: "Artículos encontrados" });
  await user.click(within(results).getByRole("button", { name: TELEPORTATION_TITLE }));
  expect(await screen.findByRole("heading", { name: "Bloque 1" })).toBeInTheDocument();
  expect(screen.getByText("Leído en parte")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Exportar este artículo" }));
  const result = await screen.findByTestId("paper-export");
  expect(within(result).getByText("Programas: 1. Circuitos rechazados: 1.")).toBeInTheDocument();
  expect(
    within(result).getByRole("button", { name: `Descargar ${BELL_PAIR_FILE}` }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Leer este bloque: bloque 2" })).toBeInTheDocument();
});
