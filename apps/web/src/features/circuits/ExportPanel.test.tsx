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

interface RecordedProgram {
  filename: string;
  text: string;
}

/** The programs of a recorded export, as the API wrote them. */
function programs(name: string): RecordedProgram[] {
  return (capturedAnswer(name).response as { programs: RecordedProgram[] }).programs;
}

beforeEach(() => {
  useCircuitStore.setState(initialCircuitState);
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

async function openPage(): Promise<User> {
  const user = userEvent.setup();
  renderApp("/circuits");
  await screen.findByRole("heading", { level: 1, name: "Circuits" });
  return user;
}

async function readExample(user: User, id: string, status = "Read in full") {
  await user.selectOptions(screen.getByLabelText("Try an example"), id);
  await screen.findByText(status);
}

const exportButton = () => screen.getByRole("button", { name: "Export" });
const programBox = (file: string) =>
  screen.getByRole("region", { name: `OpenQASM 3 program ${file}` });
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

/** The JSON bodies of the export requests the screen sends while `work` runs. */
async function bodiesDuring(work: () => Promise<void>): Promise<{ values?: unknown }[]> {
  const sent: Promise<{ values?: unknown }>[] = [];
  server.events.on("request:start", ({ request }) => {
    if (request.url.includes("/circuits/qasm")) {
      sent.push(request.clone().json() as Promise<{ values?: unknown }>);
    }
  });
  try {
    await work();
  } finally {
    server.events.removeAllListeners();
  }
  return Promise.all(sent);
}

describe("the values card", () => {
  it("sits above Simulate, Check and Export, which all use it", async () => {
    await openPage();
    const values = screen.getByRole("region", { name: "Values" });
    expect(within(values).getByLabelText("Parameter values")).toBeInTheDocument();
    for (const name of ["Simulate", "Check", "Export"]) {
      const card = screen.getByRole("region", { name });
      expect(
        values.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING,
        name,
      ).toBeTruthy();
      expect(within(card).queryByLabelText("Parameter values")).not.toBeInTheDocument();
    }
  });
});

describe("the export card", () => {
  it("is off until a circuit is read", async () => {
    const user = await openPage();
    expect(exportButton()).toBeDisabled();
    await readExample(user, "bell");
    expect(exportButton()).toBeEnabled();
  });

  it("shows the program of a Bell pair as the API wrote it, in a box a keyboard can reach", async () => {
    const user = await openPage();
    await readExample(user, "bell");
    await user.click(exportButton());
    const [bell] = programs("qasm bell");
    const box = await screen.findByRole("region", { name: "OpenQASM 3 program circuit-p01.qasm" });
    expect(box.textContent).toBe(bell!.text);
    expect(box).toHaveAttribute("tabindex", "0");
    const result = screen.getByRole("region", { name: "OpenQASM 3 programs" });
    expect(
      within(result).getByRole("heading", { level: 3, name: "circuit-p01.qasm" }),
    ).toBeInTheDocument();
    expect(
      within(result).getByText(/Part 1 · Qubits: 2\. Classical bits: 0\./),
    ).toBeInTheDocument();
    expect(within(result).getByRole("button", { name: "Copy circuit-p01.qasm" })).toBeEnabled();
    expect(within(result).getByRole("button", { name: "Download circuit-p01.qasm" })).toBeEnabled();
    expect(within(result).queryByRole("alert")).not.toBeInTheDocument();
    expect(regions().filter(Boolean)).toEqual(["Export finished. Programs: 1."]);
  });

  it("exports the circuit/1 text as it was typed", async () => {
    const user = await openPage();
    await readExample(user, "bellJson");
    await user.click(exportButton());
    expect((await screen.findByTestId("qasm-program")).querySelector("pre")?.textContent).toBe(
      programs("qasm bell json")[0]!.text,
    );
  });

  it("gives a drawn equality one program per side, with the sign drawn", async () => {
    const user = await openPage();
    await readExample(user, "hzhEqualsX");
    await user.click(exportButton());
    const [left, right] = programs("qasm hzh equals x");
    expect((await screen.findAllByTestId("qasm-program")).length).toBe(2);
    expect(programBox("circuit-p01.qasm").textContent).toBe(left!.text);
    expect(programBox("circuit-p02.qasm").textContent).toBe(right!.text);
    expect(screen.getByText(/Part 2, after "="/)).toBeInTheDocument();
    expect(regions().filter(Boolean)).toEqual(["Export finished. Programs: 2."]);
  });

  it("says how many classical bits a measured circuit has", async () => {
    const user = await openPage();
    await readExample(user, "measured");
    await user.click(exportButton());
    expect(await screen.findByText(/Qubits: 2\. Classical bits: 2\./)).toBeInTheDocument();
    expect(programBox("circuit-p01.qasm").textContent).toContain("measure q[1] -> c[1];");
  });

  it("asks for the missing values, adds their names, then exports with them", async () => {
    const user = await openPage();
    await readExample(user, "rotation");
    await user.click(exportButton());
    const refusal = await screen.findByTestId("export-refusal");
    expect(refusal).toHaveAttribute("role", "alert");
    expect(
      within(refusal).getByText("part 1: unbound parameter(s): theta; bind them with a value"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("qasm-program")).not.toBeInTheDocument();
    // the refusal speaks for itself; the hidden sentence says nothing old
    expect(regions()).toEqual(["", ""]);
    const unbound = screen.getByTestId("export-unbound");
    expect(within(unbound).getByText(/symbols with no value: theta\./)).toBeInTheDocument();
    await user.click(within(unbound).getByRole("button", { name: "Add the names to the values" }));
    const values = screen.getByLabelText("Parameter values");
    expect(values).toHaveValue("theta = ");
    // the note with the button is gone; focus is in the box, after the text
    expect(values).toHaveFocus();
    expect((values as HTMLTextAreaElement).selectionStart).toBe("theta = ".length);
    // editing the values clears the refusal they answered
    expect(screen.queryByTestId("export-refusal")).not.toBeInTheDocument();
    await user.type(values, "pi/4");
    await user.click(exportButton());
    const box = await screen.findByRole("region", { name: "OpenQASM 3 program circuit-p01.qasm" });
    expect(box.textContent).toBe(programs("qasm symbolic bound")[0]!.text);
    expect(box.textContent).toContain("ry(pi/4.0) q[0];");
  });

  it("shows a refusal with every reason word for word, and no program", async () => {
    const user = await openPage();
    await readExample(user, "partial", "Read in part");
    await user.click(exportButton());
    const refusal = await screen.findByTestId("export-refusal");
    expect(within(refusal).getByText("Refused")).toBeInTheDocument();
    expect(
      within(refusal).getByText("status: the circuit was read as 'partial', not 'parsed'"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("export-unbound")).not.toBeInTheDocument();
    expect(screen.queryByTestId("qasm-program")).not.toBeInTheDocument();
  });

  it("does not call the values unused when the circuit is refused", async () => {
    const user = await openPage();
    await readExample(user, "partial", "Read in part");
    await user.type(screen.getByLabelText("Parameter values"), "theta = 1");
    await user.click(exportButton());
    const refusal = await screen.findByTestId("export-refusal");
    // the API lists every key of a refused circuit as unmatched
    expect(capturedAnswer("qasm partial refused with a value").response.unmatched).toEqual([
      "theta",
    ]);
    expect(
      within(refusal).getByText("status: the circuit was read as 'partial', not 'parsed'"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("export-unmatched")).not.toBeInTheDocument();
    expect(screen.queryByText("Values not used")).not.toBeInTheDocument();
  });

  it("refuses a key that is not one symbol before sending anything", async () => {
    const user = await openPage();
    await readExample(user, "rotation");
    await user.type(screen.getByLabelText("Parameter values"), "thetta = 1");
    const sent = await requestsDuring(async () => {
      await user.click(exportButton());
      await screen.findByTestId("problem");
    });
    expect(sent.filter((url) => url.includes("/circuits/qasm"))).toEqual([]);
    expect(
      within(screen.getByTestId("problem")).getByText(
        'Line 1: "thetta" is not a parameter name. Write one symbol, such as theta, \\theta or \\theta_{1}.',
      ),
    ).toBeInTheDocument();
  });

  it("shows the server's 422 for a key in its own words, with the field it points at", async () => {
    const recorded = capturedAnswer("qasm bad key");
    // The screen catches this key itself; here the server is the one that refuses it.
    server.use(
      http.post("*/api/v1/circuits/qasm", () =>
        HttpResponse.json(recorded.response, {
          status: recorded.status,
          headers: { "content-type": "application/problem+json" },
        }),
      ),
    );
    const user = await openPage();
    await readExample(user, "rotation");
    await user.click(exportButton());
    const problem = await screen.findByTestId("problem");
    expect(within(problem).getByText("The circuit could not be processed")).toBeInTheDocument();
    expect(
      within(problem).getByText(
        "'thetta' is not a parameter name: write one symbol, such as theta, \\theta or \\theta_{1}",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("qasm-program")).not.toBeInTheDocument();
  });

  it("writes the program and says which values named no symbol", async () => {
    const user = await openPage();
    await readExample(user, "bell");
    await user.type(screen.getByLabelText("Parameter values"), "theta = 1");
    await user.click(exportButton());
    const note = await screen.findByTestId("export-unmatched");
    expect(note).toHaveAttribute("role", "note");
    expect(within(note).getByText("Values not used")).toBeInTheDocument();
    expect(within(note).getByText(/their values were not used: theta\./)).toBeInTheDocument();
    expect(programBox("circuit-p01.qasm").textContent).toBe(programs("qasm stray key")[0]!.text);
  });

  it("drops the programs when the circuit changes", async () => {
    const user = await openPage();
    await readExample(user, "bell");
    await user.click(exportButton());
    await screen.findByTestId("qasm-program");
    await user.type(screen.getByLabelText("LaTeX code of one circuit"), " ");
    expect(screen.queryByTestId("qasm-program")).not.toBeInTheDocument();
  });

  it("sends a value as typed, so 0.50 stays 0.50 in the bound line, as with --set", async () => {
    const user = await openPage();
    await readExample(user, "rotation");
    await user.type(screen.getByLabelText("Parameter values"), "theta = 0.50 ");
    const sent = await bodiesDuring(async () => {
      await user.click(exportButton());
      await screen.findByTestId("qasm-program");
    });
    expect(sent.map((body) => body.values)).toEqual([{ theta: "0.50" }]);
    const text = programBox("circuit-p01.qasm").textContent;
    expect(text).toBe(programs("qasm bound as typed")[0]!.text);
    expect(text).toContain("// bound: theta = 0.50\n");
  });

  it("sends exponent notation as text and shows the server's refusal of it", async () => {
    const user = await openPage();
    await readExample(user, "rotation");
    await user.type(screen.getByLabelText("Parameter values"), "theta = 1e3");
    const sent = await bodiesDuring(async () => {
      await user.click(exportButton());
      await screen.findByTestId("problem");
    });
    expect(sent.map((body) => body.values)).toEqual([{ theta: "1e3" }]);
    const recorded = capturedAnswer("qasm exponent").response as { detail: string };
    expect(within(screen.getByTestId("problem")).getByText(recorded.detail)).toBeInTheDocument();
    expect(screen.queryByTestId("qasm-program")).not.toBeInTheDocument();
  });
});

describe("copying a program", () => {
  function stubClipboard(writeText: (text: string) => Promise<void>) {
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  }

  async function exportBell(user: User) {
    await readExample(user, "bell");
    await user.click(exportButton());
    await screen.findByTestId("qasm-program");
  }

  it("copies the text and says so, on the screen and to a screen reader", async () => {
    const user = await openPage();
    await exportBell(user);
    const writeText = vi.fn(() => Promise.resolve());
    stubClipboard(writeText);
    await user.click(screen.getByRole("button", { name: "Copy circuit-p01.qasm" }));
    expect(writeText).toHaveBeenCalledWith(programs("qasm bell")[0]!.text);
    expect(
      await within(screen.getByTestId("qasm-program")).findByText("circuit-p01.qasm copied."),
    ).toBeInTheDocument();
    expect(regions().filter(Boolean)).toEqual(["circuit-p01.qasm copied."]);
    expect(document.querySelectorAll("[aria-live]")).toHaveLength(0);
  });

  it("selects the text in its box when the browser refuses", async () => {
    const user = await openPage();
    await exportBell(user);
    stubClipboard(() => Promise.reject(new DOMException("denied", "NotAllowedError")));
    await user.click(screen.getByRole("button", { name: "Copy circuit-p01.qasm" }));
    const box = programBox("circuit-p01.qasm");
    await waitFor(() => expect(box).toHaveFocus());
    expect(window.getSelection()?.toString()).toBe(programs("qasm bell")[0]!.text);
    expect(
      within(screen.getByTestId("qasm-program")).getByText(
        /circuit-p01\.qasm is selected: the browser did not allow copying/,
      ),
    ).toBeInTheDocument();
    expect(regions().filter(Boolean)).toEqual([
      "circuit-p01.qasm is selected: the browser did not allow copying. Press Ctrl+C (Cmd+C on a Mac) to copy it.",
    ]);
  });
});

describe("downloading a program", () => {
  let saved: { name: string; blob: Blob }[];

  beforeEach(() => {
    saved = [];
    let blob: Blob | undefined;
    URL.createObjectURL = (made: Blob | MediaSource) => {
      blob = made as Blob;
      return "blob:qasm";
    };
    URL.revokeObjectURL = () => undefined;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push({ name: this.download, blob: blob! });
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("saves each part under its file name, as plain text, byte for byte", async () => {
    const user = await openPage();
    await readExample(user, "hzhEqualsX");
    await user.click(exportButton());
    await screen.findAllByTestId("qasm-program");
    await user.click(screen.getByRole("button", { name: "Download circuit-p02.qasm" }));
    expect(saved.map((file) => file.name)).toEqual(["circuit-p02.qasm"]);
    expect(saved[0]!.blob.type).toBe("text/plain;charset=utf-8");
    expect(await saved[0]!.blob.text()).toBe(programs("qasm hzh equals x")[1]!.text);
  });
});

it("is in Spanish when the language is", async () => {
  const user = await openPage();
  await user.click(screen.getByRole("button", { name: "Language: English" }));
  await user.click(screen.getByRole("menuitemradio", { name: "Español" }));
  expect(await screen.findByRole("region", { name: "Valores" })).toBeInTheDocument();
  expect(screen.getByLabelText("Valores de los parámetros")).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Probar un ejemplo"), "rotation");
  await screen.findByText("Leído completo");
  await user.click(screen.getByRole("button", { name: "Exportar" }));
  const refusal = await screen.findByTestId("export-refusal");
  expect(within(refusal).getByText("Rechazado")).toBeInTheDocument();
  // the server's reason stays as the server wrote it
  expect(
    within(refusal).getByText("part 1: unbound parameter(s): theta; bind them with a value"),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Agregar los nombres a los valores" }));
  await user.type(screen.getByLabelText("Valores de los parámetros"), "pi/4");
  await user.click(screen.getByRole("button", { name: "Exportar" }));
  expect(
    await screen.findByRole("region", { name: "Programa OpenQASM 3 circuit-p01.qasm" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Programas OpenQASM 3" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Copiar circuit-p01.qasm" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Descargar circuit-p01.qasm" })).toBeInTheDocument();
  expect(screen.getByTestId("live-status")).toHaveTextContent(
    "Exportación terminada. Programas: 1.",
  );
});
