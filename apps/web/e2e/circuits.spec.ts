import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { DANI, expect, expectNoAxeViolations, signIn, test } from "./fixtures";
import type { APIRequestContext, Download, Page } from "@playwright/test";
import captured from "../src/features/circuits/fixtures/captured.json" with { type: "json" };

// Acceptance checks C1 to C7 of the circuit screen, and C8 to C10 of its OpenQASM 3 export,
// against the real API in fake mode: the parser, the simulator, the emitter and the sign-in are
// the real ones. Every test signs in as `dani`, a seeded member that no other file uses, so these
// tests never end another file's session.
//
// The tests of this file run one after the other in one worker. The API runs at most two circuit
// jobs at once and answers 503 to a third (by design), so the file's parallel tests would turn
// into a lottery of busy answers.
test.describe.configure({ mode: "default" });

const BRIDGE = fileURLToPath(
  new URL("../../../packages/quantum-sim/tests/fixtures/bridge/", import.meta.url),
);

interface Recorded {
  name: string;
  path: string;
  request: Record<string, unknown> | null;
  status: number;
  response: unknown;
}
const recorded = captured as Recorded[];
const request = (name: string) => {
  const entry = recorded.find((item) => item.name === name);
  if (!entry?.request) throw new Error(`no recorded request ${name}`);
  return entry.request;
};
const response = <T>(name: string) => {
  const entry = recorded.find((item) => item.name === name);
  if (!entry) throw new Error(`no recorded answer ${name}`);
  return entry.response as T;
};

interface QasmProgramAnswer {
  filename: string;
  text: string;
}
/** The text of program `index` of a recorded export of a pasted circuit. */
const programText = (name: string, index = 0) => {
  const program = response<{ programs: QasmProgramAnswer[] }>(name).programs[index];
  if (!program) throw new Error(`no program ${index} in ${name}`);
  return program.text;
};

// The fake corpus's teleportation paper: block 1 is read in part (so its export is refused) and
// block 2, a Bell pair, exports one program, named as `docingest circuits export --doc` names it.
const TELEPORT_TITLE = "A compact teleportation circuit with mid-circuit measurement (synthetic)";
const TELEPORT_SOURCE = "synthetic-teleportation-circuit.tex";
const TELEPORT_FILE = "c7f460355b1958c3-b02-c01-p01.qasm";
const teleportBlocks = response<{ doc_id: string; blocks: { latex: string }[] }>(
  "paper circuits teleportation",
);
const TELEPORT_ID = teleportBlocks.doc_id;
const bellBlock = teleportBlocks.blocks[1]!.latex;
const teleportProgram = (() => {
  const answer = response<{ circuits: { programs: QasmProgramAnswer[] }[] }>(
    "paper qasm teleportation",
  );
  const program = answer.circuits.flatMap((circuit) => circuit.programs)[0];
  if (program?.filename !== TELEPORT_FILE) throw new Error("the recorded paper export changed");
  return program.text;
})();
const TRANSMON_ID = response<{ doc_id: string }>("paper circuits none").doc_id;

interface ParseAnswer {
  status: "parsed" | "partial" | "unparsed";
  circuits: { status: string; wires: number; parts: unknown[] }[];
}
interface SimulatorAnswer {
  status: "ok" | "symbolic" | "refused";
  refused: string[];
  circuits: unknown[];
  equal_to_target: boolean | null;
}

/** One call to a circuit endpoint, typed. */
async function ask<T>(api: APIRequestContext, path: string, data: unknown): Promise<T> {
  const answer = await api.post(`/api/v1/circuits/${path}`, { data });
  return (await answer.json()) as T;
}

async function openCircuits(page: Page) {
  await page.goto("/login");
  await signIn(page, DANI);
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Circuits" })
    .click();
  await expect(page.getByRole("heading", { level: 1, name: "Circuits" })).toBeVisible();
}

const latexBox = (page: Page) => page.getByLabel("LaTeX code of one circuit");
const jsonBox = (page: Page) => page.getByLabel("circuit/1 document as JSON");
const read = (page: Page) => page.getByRole("button", { name: "Read circuit" }).click();

async function readExample(page: Page, id: string) {
  await page.getByLabel("Try an example").selectOption(id);
  // Wait for the reading, so that what comes next acts on a circuit that has been read
  await expect(page.getByTestId("gate-table").first()).toBeVisible();
}

// "Export" alone: the Paper tab also has "Export this paper"
const exportButton = (page: Page) => page.getByRole("button", { name: "Export", exact: true });
const program = (page: Page, file: string) =>
  page.getByRole("region", { name: `OpenQASM 3 program ${file}` });

/** The text of a downloaded file, and the name the browser was asked to save it under. */
async function saved(download: Promise<Download>) {
  const file = await download;
  return { name: file.suggestedFilename(), text: readFileSync(await file.path(), "utf8") };
}

/** On the Paper tab, finds the teleportation paper by a word of its title and lists its blocks. */
async function pickTeleportPaper(page: Page) {
  await page.getByRole("tab", { name: "Paper" }).click();
  await page.getByLabel("Title or id").fill("teleportation");
  await page.getByRole("button", { name: "Find papers" }).click();
  await page
    .getByRole("list", { name: "Papers found" })
    .getByRole("button", { name: TELEPORT_TITLE })
    .click();
  await expect(page.getByTestId("paper-block")).toHaveCount(2);
}

async function expectNoSidewaysScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

const BELL = String.raw`\begin{quantikz}
\lstick{\ket{0}} & \gate{H} & \ctrl{1} & \qw \\
\lstick{\ket{0}} & \qw & \targ{} & \qw
\end{quantikz}`;

test.describe("C1: LaTeX and the gate list", () => {
  test("reads a quantikz circuit and lists each gate", async ({ page }) => {
    await openCircuits(page);
    await latexBox(page).fill(BELL);
    await read(page);
    await expect(page.getByText("Read in full")).toBeVisible();
    const table = page.getByRole("table", { name: /Gates of circuit 1/ });
    await expect(table.getByRole("columnheader")).toHaveText([
      "Step",
      "Gate",
      "Targets",
      "Controls",
      "Open controls",
    ]);
    await expect(table.getByRole("row")).toHaveCount(3);
    await expect(table.getByRole("row").nth(2)).toContainText("X(CNOT)");
    await page.getByText("Gate list in words").click();
    await expect(page.getByText("Step 2: CNOT: control wire 1, target wire 2")).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("reads Qcircuit and lists a circuit with a drawn equality as two sides", async ({
    page,
  }) => {
    await openCircuits(page);
    await readExample(page, "ghz");
    await expect(page.getByRole("heading", { name: "Wires (3)" })).toBeVisible();
    await expect(
      page.getByRole("table", { name: /Gates of circuit 1/ }).getByRole("row"),
    ).toHaveCount(4);
    await readExample(page, "hzhEqualsX");
    await expect(page.getByRole("table", { name: /left side of the equality/ })).toBeVisible();
    await expect(page.getByRole("table", { name: /right side, after "="/ })).toBeVisible();
  });

  test("lists what was not read, and says so for text with no circuit", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "partial");
    await expect(page.getByText("Read in part")).toBeVisible();
    const unread = page.getByTestId("unread");
    await expect(unread).toContainText("Step 2, wire 2");
    await expect(unread).toContainText("iddots");
    await expectNoAxeViolations(page);
    await latexBox(page).fill("nothing to see");
    await read(page);
    await expect(page.getByText("No circuit was found in this text.")).toBeVisible();
  });
});

test.describe("C2: circuit/1 JSON", () => {
  test("is read as it is", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bellJson");
    await expect(page.getByRole("tab", { name: "circuit/1 JSON", selected: true })).toBeVisible();
    await expect(page.getByText("Read in full")).toBeVisible();
    await expect(
      page.getByRole("table", { name: /Gates of circuit 1/ }).getByRole("row"),
    ).toHaveCount(3);
  });

  test("refuses a key written twice and names it", async ({ page }) => {
    await openCircuits(page);
    await page.getByRole("tab", { name: "circuit/1 JSON" }).click();
    await jsonBox(page).fill('{"format": "circuit/1", "wires": 1, "wires": 2, "parts": []}');
    await read(page);
    await expect(page.getByTestId("problem")).toContainText('The key "wires" appears twice');
    await expect(page.getByTestId("problem")).toContainText("A key is written twice");
    await expectNoAxeViolations(page);
  });

  test("the server refuses a repeated key too, with the key's name", async ({ api }) => {
    const answer = await api.post("/api/v1/circuits/run", {
      data: JSON.stringify({
        circuit_json: '{"format":"circuit/1","wires":1,"wires":2,"parts":[]}',
      }),
      headers: { "content-type": "application/json" },
    });
    expect(answer.status()).toBe(400);
    expect(((await answer.json()) as { detail: string }).detail).toContain("'wires' twice");
  });
});

test.describe("C3: refusals", () => {
  test("shows every reason as the simulator gives it, and never a result", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bundle");
    await page.getByRole("button", { name: "Simulate" }).click();
    const refusal = page.getByTestId("refusal");
    await expect(refusal).toContainText("Refused");
    await expect(refusal).toContainText(
      "wire 2: the legend '2 = a, bundle m' shows a bundle, a register, a row of dots or a classical wire",
    );
    await expect(page.getByTestId("state-table")).toHaveCount(0);
    await expectNoAxeViolations(page);
  });

  test("a refusal looks different from circuits that are not equal", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bell");
    await page.getByLabel("Target state").fill("11");
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("not-equal")).toContainText("Not equal");
    await expect(page.getByTestId("refusal")).toHaveCount(0);
    await page.getByRole("radio", { name: "A unitary" }).click();
    await page.getByLabel("Target unitary").fill("[[1, 0], [0, 1]]");
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("refusal")).toContainText("a 4 x 4 matrix");
    await expect(page.getByTestId("not-equal")).toHaveCount(0);
    await expect(page.getByTestId("equal")).toHaveCount(0);
  });
});

test.describe("C4: simulate", () => {
  test("shows the state and the probabilities, first wire most significant", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bell");
    await page.getByLabel("Input state").fill("10");
    await page.getByRole("button", { name: "Simulate" }).click();
    const state = page.getByTestId("state-table");
    await expect(state.getByRole("row")).toHaveCount(3);
    await expect(state.getByRole("row").nth(2)).toContainText("|11\u27E9");
    await expect(state.getByRole("row").nth(2)).toContainText("\u22120.7071");
    await expect(page.getByTestId("probabilities").getByRole("listitem")).toHaveText([
      /\|00\u27E9.*50%/,
      /\|11\u27E9.*50%/,
    ]);
    await expectNoAxeViolations(page);
  });

  test("shows the unitary only when asked", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bell");
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByTestId("state-table")).toBeVisible();
    await expect(page.getByTestId("unitary-table")).toHaveCount(0);
    await page.getByLabel("Also show the unitary (up to 8 wires)").click();
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByTestId("unitary-table").getByRole("row")).toHaveCount(5);
    await expectNoAxeViolations(page);
  });

  test("takes a symbol's value and an angle", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "rotation");
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByTestId("unbound")).toContainText("theta");
    await page.getByRole("button", { name: "Add the names to the values" }).click();
    await expect(page.getByLabel("Parameter values")).toHaveValue("theta = ");
    await page.getByLabel("Parameter values").fill("theta = \\pi/2");
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByTestId("state-table").getByRole("row")).toHaveCount(3);
    await expect(page.getByTestId("state-table")).toContainText("0.7071");
  });

  test("shows a state of 12 wires, and says that 13 are too many for this screen", async ({
    page,
  }) => {
    await openCircuits(page);
    await page.getByRole("tab", { name: "circuit/1 JSON" }).click();
    const wide = (wires: number) =>
      JSON.stringify({
        format: "circuit/1",
        wires,
        parts: [[[{ kind: "gate", label: "H", targets: [1] }]]],
      });
    await jsonBox(page).fill(wide(12));
    await page.getByRole("button", { name: "Simulate" }).click();
    const state = page.getByTestId("state-table");
    await expect(state.getByRole("row")).toHaveCount(3);
    await expect(state.getByRole("row").nth(1)).toContainText("|000000000000\u27E9");
    await expect(
      page.getByRole("button", { name: "Show the 4094 zero rows" }).first(),
    ).toBeVisible();
    await jsonBox(page).fill(wide(13));
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByTestId("problem")).toContainText("this check takes at most 12");
    await expect(page.getByTestId("problem")).toContainText("quantum-sim");
    await expect(page.getByTestId("state-table")).toHaveCount(0);
  });

  /**
   * Every probability row shows its whole basis label, and the bar starts after it. A label that a
   * bar covers makes two outcomes that differ in their last wires look the same.
   */
  for (const [wires, width] of [
    [8, 375],
    [12, 375],
    [12, 1280],
  ] as const) {
    test(`keeps the ${wires}-wire outcome labels whole at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openCircuits(page);
      await page.getByRole("tab", { name: "circuit/1 JSON" }).click();
      // H on the last wire: the two outcomes differ only in their last bit
      await jsonBox(page).fill(
        JSON.stringify({
          format: "circuit/1",
          wires,
          parts: [[[{ kind: "gate", label: "H", targets: [wires] }]]],
        }),
      );
      await page.getByRole("button", { name: "Simulate" }).click();
      const rows = page.getByTestId("probabilities").getByRole("listitem");
      await expect(rows).toHaveText([
        new RegExp(`\\|0{${wires}}\u27E9.*50%`),
        new RegExp(`\\|0{${wires - 1}}1\u27E9.*50%`),
      ]);
      const layout = await page.getByTestId("probabilities").evaluate((list) =>
        [...list.children].map((row) => {
          const [label, bar, value] = [...row.children].map((cell) => cell.getBoundingClientRect());
          const text = row.children[0] as HTMLElement;
          return {
            text: text.textContent,
            labelRight: label!.right,
            barLeft: bar!.left,
            barWidth: bar!.width,
            valueLeft: value!.left,
            clipped: text.scrollWidth > text.clientWidth,
            rowRight: row.getBoundingClientRect().right,
            valueRight: value!.right,
          };
        }),
      );
      expect(layout).toHaveLength(2);
      for (const row of layout) {
        expect(row.clipped, `${row.text} is not cut off`).toBe(false);
        expect(row.labelRight, `${row.text} ends before its bar`).toBeLessThanOrEqual(row.barLeft);
        expect(row.barWidth, "the bar keeps some width").toBeGreaterThan(20);
        expect(row.valueLeft).toBeGreaterThanOrEqual(row.barLeft + row.barWidth);
        expect(row.valueRight).toBeLessThanOrEqual(row.rowRight + 1);
      }
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
      await expectNoAxeViolations(page);
    });
  }

  test("shows a unitary of 8 wires as a corner with the whole matrix to download", async ({
    page,
  }) => {
    await openCircuits(page);
    await page.getByRole("tab", { name: "circuit/1 JSON" }).click();
    await jsonBox(page).fill(
      JSON.stringify({
        format: "circuit/1",
        wires: 8,
        parts: [[[{ kind: "gate", label: "H", targets: [1] }]]],
      }),
    );
    await page.getByLabel("Also show the unitary (up to 8 wires)").click();
    await page.getByRole("button", { name: "Simulate" }).click();
    const unitary = page.getByTestId("unitary-table");
    await expect(unitary.getByRole("row")).toHaveCount(17);
    await expect(page.getByText("Showing the top left 16 by 16 of 256 by 256.")).toBeVisible();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download the unitary (JSON)" }).click();
    expect((await download).suggestedFilename()).toBe("unitary.json");
  });
});

test.describe("C5: check against a target", () => {
  test("a state: equal up to global phase, with the method", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bell");
    await page
      .getByLabel("Target state")
      .fill(JSON.stringify((request("check bell state equal").target as { state: unknown }).state));
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("equal")).toContainText("Equal up to global phase");
    await expect(page.getByTestId("comparisons")).toContainText("arrays");
    await expectNoAxeViolations(page);
  });

  test("a state that differs: not equal, with the largest deviation", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bell");
    await page.getByLabel("Target state").fill("11");
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("not-equal")).toContainText("Not equal");
    await expect(page.getByTestId("not-equal")).toContainText("Largest deviation: 0.707107");
    await expectNoAxeViolations(page);
  });

  test("a unitary", async ({ page }) => {
    await openCircuits(page);
    await page.getByRole("tab", { name: "circuit/1 JSON" }).click();
    await jsonBox(page).fill(String(request("check hzh unitary x").circuit_json));
    await read(page);
    await page.getByRole("radio", { name: "A unitary" }).click();
    await page.getByLabel("Target unitary").fill("[[0, 1], [1, 0]]");
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("equal")).toBeVisible();
    await expect(page.getByTestId("comparisons")).toContainText("unitary");
    await page.getByLabel("Target unitary").fill("[[1, 0], [0, 1]]");
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("not-equal")).toBeVisible();
  });

  test("a second circuit, written in LaTeX or as circuit/1", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bellJson");
    await page.getByRole("radio", { name: "Another circuit" }).click();
    await page.getByLabel("Second circuit").fill(BELL);
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("equal")).toBeVisible();
    await page.getByLabel("Second circuit").fill(String(request("run bad input").circuit_json));
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("equal")).toBeVisible();
    await page
      .getByLabel("Second circuit")
      .fill(String(request("check hzh unitary x").circuit_json));
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("refusal")).toBeVisible();
  });

  test("a drawn equality checks the left side against the right", async ({ page }) => {
    await openCircuits(page);
    for (const id of ["hzhEqualsX", "cnotFromCz"]) {
      await readExample(page, id);
      await page.getByRole("radio", { name: "The drawn equality" }).click();
      await page.getByRole("button", { name: "Check" }).click();
      await expect(page.getByTestId("equal")).toBeVisible();
      await expect(page.getByTestId("comparisons")).toContainText("part 1 / part 2");
    }
  });
});

test.describe("C6: conventions", () => {
  test("a card states the conventions and links to the gate table", async ({ page }) => {
    await openCircuits(page);
    const card = page.getByRole("region", { name: "Conventions" });
    await expect(card).toContainText(
      "wire 1 is the top wire of the drawing and the most significant bit",
    );
    await expect(card).toContainText("R_a(theta) = exp(-i theta sigma_a / 2)");
    await expect(card).toContainText("equal up to global phase: max |a - e^{i phi} b| < 1e-9");
    await readExample(page, "bell");
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(card).toContainText("As stated in the last result.");
    await page.getByRole("link", { name: "See the conventions and the gate table" }).click();
    await expect(card.locator("details")).toHaveAttribute("open", "");
    await expect(card.getByRole("table", { name: /Gates the simulator knows/ })).toBeVisible();
    await expect(card.getByRole("cell", { name: "rzz", exact: true })).toBeVisible();
    await expectNoAxeViolations(page);
  });
});

test.describe("C7: the bridge sources", () => {
  const sources = readdirSync(BRIDGE)
    .filter((file) => file.endsWith(".tex"))
    .sort();

  test("the folder has the circuits the API tests use", () => {
    expect(sources.length).toBeGreaterThanOrEqual(30);
  });

  for (const file of sources) {
    test(`${file} shows what the API says`, async ({ page, api }) => {
      const latex = readFileSync(new URL(file, `file://${BRIDGE}`), "utf8");
      const saved = JSON.parse(
        readFileSync(new URL(file.replace(".tex", ".json"), `file://${BRIDGE}`), "utf8"),
      ) as {
        status: string;
        wires: number;
      };
      const parse = await ask<ParseAnswer>(api, "parse", { latex });
      expect(parse.circuits[0]).toMatchObject({ status: saved.status, wires: saved.wires });
      const circuitJson = JSON.stringify(parse.circuits[0]);

      await openCircuits(page);
      await latexBox(page).fill(latex);
      await read(page);
      const label = {
        parsed: "Read in full",
        partial: "Read in part",
        unparsed: "Not read",
      } as const;
      await expect(page.getByText(label[parse.status], { exact: true }).first()).toBeVisible();
      await expect(page.getByTestId("gate-table").first()).toBeVisible();

      const run = await ask<SimulatorAnswer>(api, "run", { circuit_json: circuitJson });
      await page.getByRole("button", { name: "Simulate" }).click();
      if (run.status === "refused") {
        const refusal = page.getByTestId("refusal");
        await expect(refusal).toBeVisible();
        for (const reason of run.refused) await expect(refusal).toContainText(reason);
        await expect(page.getByTestId("state-table")).toHaveCount(0);
      } else if (run.status === "symbolic") {
        await expect(page.getByTestId("unbound")).toBeVisible();
      } else {
        await expect(page.getByTestId("state-table")).toHaveCount(run.circuits.length);
        await expect(page.getByTestId("refusal")).toHaveCount(0);
      }

      const drawn = (parse.circuits[0]?.parts.length ?? 0) > 1;
      if (drawn && run.status !== "refused") {
        const check = await ask<SimulatorAnswer>(api, "check", { circuit_json: circuitJson });
        await page.getByRole("radio", { name: "The drawn equality" }).click();
        await page.getByRole("button", { name: "Check" }).click();
        await expect(page.getByTestId(check.equal_to_target ? "equal" : "not-equal")).toBeVisible();
      }
    });
  }
});

test.describe("C8: export a pasted circuit as OpenQASM 3", () => {
  test("writes one program per part of the drawing, word for word", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "hzhEqualsX");
    await exportButton(page).click();
    const result = page.getByRole("region", { name: "OpenQASM 3 programs" });
    await expect(result.getByTestId("qasm-program")).toHaveCount(2);
    await expect(result.getByRole("heading", { level: 3 })).toHaveText([
      "circuit-p01.qasm",
      "circuit-p02.qasm",
    ]);
    await expect(result).toContainText('Part 2, after "=" \u00b7 Qubits: 1. Classical bits: 0.');
    expect(await program(page, "circuit-p01.qasm").textContent()).toBe(
      programText("qasm hzh equals x", 0),
    );
    expect(await program(page, "circuit-p02.qasm").textContent()).toBe(
      programText("qasm hzh equals x", 1),
    );
    await expect(page.getByTestId("live-status")).toContainText("Export finished. Programs: 2.");
    await expect(page.getByTestId("export-refusal")).toHaveCount(0);
    await expectNoAxeViolations(page);
  });

  test("copies a program to the clipboard", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await openCircuits(page);
    await readExample(page, "bell");
    await exportButton(page).click();
    await page.getByRole("button", { name: "Copy circuit-p01.qasm" }).click();
    await expect(
      page.getByTestId("qasm-program").getByText("circuit-p01.qasm copied.", { exact: true }),
    ).toBeVisible();
    await expect(page.getByTestId("live-status")).toContainText("circuit-p01.qasm copied.");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
      programText("qasm bell"),
    );
  });

  test("selects the program when the browser refuses to copy it", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: () => Promise.reject(new DOMException("Write denied.", "NotAllowedError")),
        },
      });
    });
    await openCircuits(page);
    await readExample(page, "bell");
    await exportButton(page).click();
    await page.getByRole("button", { name: "Copy circuit-p01.qasm" }).click();
    const selected =
      "circuit-p01.qasm is selected: the browser did not allow copying. Press Ctrl+C (Cmd+C on a Mac) to copy it.";
    await expect(page.getByTestId("qasm-program").getByText(selected)).toBeVisible();
    await expect(page.getByTestId("live-status")).toContainText(selected);
    await expect(program(page, "circuit-p01.qasm")).toBeFocused();
    // The range covers the whole program; Selection.toString() is the rendered text, which drops
    // the program's final line break
    expect(await page.evaluate(() => window.getSelection()?.getRangeAt(0).toString())).toBe(
      programText("qasm bell"),
    );
  });

  test("downloads a program under its file name, byte for byte", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "bell");
    await exportButton(page).click();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download circuit-p01.qasm" }).click();
    expect(await saved(download)).toEqual({
      name: "circuit-p01.qasm",
      text: programText("qasm bell"),
    });
  });

  test("refuses a circuit with a symbol and no value, then exports it with pi/4", async ({
    page,
  }) => {
    await openCircuits(page);
    await readExample(page, "rotation");
    await exportButton(page).click();
    const refusal = page.getByTestId("export-refusal");
    await expect(refusal).toHaveRole("alert");
    await expect(refusal.getByRole("list", { name: "Reasons" })).toHaveText(
      "part 1: unbound parameter(s): theta; bind them with a value",
    );
    await expect(page.getByTestId("qasm-program")).toHaveCount(0);
    const unbound = page.getByTestId("export-unbound");
    await expect(unbound).toContainText("theta");
    await unbound.getByRole("button", { name: "Add the names to the values" }).click();
    await expect(page.getByLabel("Parameter values")).toHaveValue("theta = ");
    await expect(page.getByLabel("Parameter values")).toBeFocused();
    await page.getByLabel("Parameter values").fill("theta = pi/4");
    await exportButton(page).click();
    await expect(page.getByTestId("export-refusal")).toHaveCount(0);
    expect(await program(page, "circuit-p01.qasm").textContent()).toBe(
      programText("qasm symbolic bound"),
    );
  });

  test("refuses a circuit read in part, with the reason and nothing to save", async ({ page }) => {
    await openCircuits(page);
    await readExample(page, "partial");
    await exportButton(page).click();
    await expect(page.getByTestId("export-refusal")).toContainText(
      "status: the circuit was read as 'partial', not 'parsed'",
    );
    await expect(page.getByTestId("qasm-program")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Download / })).toHaveCount(0);
  });

  test("catches a name that is not one symbol before asking, and notes a value no symbol uses", async ({
    page,
  }) => {
    await openCircuits(page);
    await readExample(page, "bell");
    const asked: string[] = [];
    page.on("request", (sent) => {
      if (sent.url().endsWith("/api/v1/circuits/qasm")) asked.push(sent.url());
    });
    await page.getByLabel("Parameter values").fill("thetta = 1");
    await exportButton(page).click();
    const problem = page.getByTestId("problem");
    await expect(problem).toContainText("A parameter name is not valid");
    await expect(problem).toContainText('Line 1: "thetta" is not a parameter name.');
    expect(asked).toEqual([]);
    await page.getByLabel("Parameter values").fill("theta = 1");
    await exportButton(page).click();
    await expect(page.getByTestId("export-unmatched")).toContainText(
      "so their values were not used: theta.",
    );
    expect(await program(page, "circuit-p01.qasm").textContent()).toBe(
      programText("qasm stray key"),
    );
    expect(asked).toHaveLength(1);
  });
});

test.describe("C9: export a stored paper", () => {
  test("finds a paper, lists its circuit blocks and reads one", async ({ page }) => {
    await openCircuits(page);
    await pickTeleportPaper(page);
    const blocks = page.getByTestId("paper-blocks");
    await expect(blocks.getByRole("heading", { level: 3 })).toHaveText(TELEPORT_SOURCE);
    await expect(blocks).toContainText(`Id: ${TELEPORT_ID}`);
    const block = page.getByTestId("paper-block");
    await expect(block.nth(0)).toContainText("Block 1");
    await expect(block.nth(0)).toContainText("Read in part");
    await expect(block.nth(1)).toContainText("Block 2");
    await expect(block.nth(1)).toContainText("Read in full");
    await expectNoAxeViolations(page);

    await page.getByRole("button", { name: "Read this block: block 2" }).click();
    await expect(page.getByRole("tab", { name: "LaTeX", selected: true })).toBeVisible();
    await expect(latexBox(page)).toBeFocused();
    await expect(latexBox(page)).toHaveValue(bellBlock);
    await expect(page.getByText("Read in full")).toBeVisible();
    await expect(
      page.getByRole("table", { name: /Gates of circuit 1/ }).getByRole("row"),
    ).toHaveCount(3);
    await exportButton(page).click();
    expect(await program(page, "circuit-p01.qasm").textContent()).toBe(
      programText("qasm paper block 2"),
    );
  });

  test("exports every circuit of the paper: one program and one refusal", async ({ page }) => {
    await openCircuits(page);
    await pickTeleportPaper(page);
    await page.getByRole("button", { name: "Export this paper" }).click();
    const exported = page.getByTestId("paper-export");
    await expect(exported.getByRole("heading", { level: 3 })).toHaveText(
      `Programs of ${TELEPORT_SOURCE}`,
    );
    await expect(exported).toContainText("Programs: 1. Refused circuits: 1.");
    await expect(page.getByTestId("live-status")).toContainText(
      "Export of the paper finished. Programs: 1. Refused circuits: 1.",
    );
    const circuits = exported.getByTestId("paper-circuit");
    await expect(circuits).toHaveCount(2);
    await expect(circuits.nth(0).getByRole("heading", { level: 4 })).toHaveText(
      "Block 1, circuit 1",
    );
    // A paper's refusals are notes: the summary above is announced once, for all of them
    await expect(circuits.nth(0).getByTestId("export-refusal")).toHaveRole("note");
    await expect(circuits.nth(0).getByTestId("export-refusal")).toContainText(
      "status: the circuit was read as 'partial', not 'parsed'",
    );
    await expect(circuits.nth(0).getByTestId("qasm-program")).toHaveCount(0);
    await expect(exported.getByRole("alert")).toHaveCount(0);
    await expect(circuits.nth(1).getByRole("heading", { level: 4 })).toHaveText(
      "Block 2, circuit 1",
    );
    await expect(circuits.nth(1).getByRole("heading", { level: 5 })).toHaveText(TELEPORT_FILE);
    expect(await program(page, TELEPORT_FILE).textContent()).toBe(teleportProgram);
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: `Download ${TELEPORT_FILE}` }).click();
    expect(await saved(download)).toEqual({ name: TELEPORT_FILE, text: teleportProgram });
    await expectNoAxeViolations(page);
  });

  test("opens a paper from its address, and says when there is no such paper", async ({ page }) => {
    await openCircuits(page);
    await page.goto(`/circuits?doc=${TELEPORT_ID}`);
    await expect(page.getByRole("tab", { name: "Paper", selected: true })).toBeVisible();
    await expect(page.getByTestId("paper-blocks").getByRole("heading", { level: 3 })).toHaveText(
      TELEPORT_SOURCE,
    );
    await expect(page.getByTestId("paper-block")).toHaveCount(2);
    await expect(page).toHaveURL(new RegExp(`/circuits\\?doc=${TELEPORT_ID}$`));

    await page.goto("/circuits?doc=ffffffffffffffff");
    const problem = page.getByTestId("problem");
    await expect(problem).toContainText("Not found");
    await expect(problem).toContainText("No such document.");
    await expect(page.getByTestId("paper-blocks")).toHaveCount(0);
    await expectNoAxeViolations(page);

    // an address that is not a document id names no paper either (the API would say 422)
    await page.goto("/circuits?doc=not-a-paper");
    await expect(problem).toContainText("Not found");
    await expect(problem).toContainText("No stored paper has this id.");
  });

  test("says when a paper has no circuit to export", async ({ page }) => {
    await openCircuits(page);
    await page.goto(`/circuits?doc=${TRANSMON_ID}`);
    await expect(
      page.getByText("This paper has no circuit blocks: its text has no fenced latex block."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Export this paper" }).click();
    await expect(page.getByTestId("paper-export").getByTestId("export-notes")).toContainText(
      "synthetic-transmon-coherence.pdf: no circuits found",
    );
    await expect(page.getByTestId("qasm-program")).toHaveCount(0);
  });
});

test.describe("C10: from an answer's paper to the circuit screen", () => {
  /** Asks the question the fake engine answers from the teleportation paper, and opens it. */
  async function openTeleportPaper(page: Page) {
    await page.goto("/login");
    await signIn(page, DANI);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Ask" }).click();
    await page.getByLabel("Your question").fill("teleportation circuit");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    // The model servers answer one question at a time, so this one can wait behind another's
    await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("region", { name: "Sources" }).getByRole("button").first().click();
    return page.getByRole("dialog", { name: TELEPORT_TITLE });
  }

  test("opens a circuit of the cited page, read, and starts empty after a reload", async ({
    page,
  }) => {
    const panel = await openTeleportPaper(page);
    // The cited section holds both circuit blocks of the paper
    await expect(panel.getByRole("link", { name: "Open in Circuits" })).toHaveCount(2);
    await expectNoAxeViolations(page);
    await panel.getByRole("link", { name: "Open in Circuits" }).nth(1).click();
    await expect(page.getByRole("heading", { level: 1, name: "Circuits" })).toBeVisible();
    await expect(page).toHaveURL(/\/circuits$/);
    await expect(page.getByRole("tab", { name: "LaTeX", selected: true })).toBeVisible();
    await expect(latexBox(page)).toHaveValue(bellBlock);
    await expect(page.getByText("Read in full")).toBeVisible();
    await exportButton(page).click();
    expect(await program(page, "circuit-p01.qasm").textContent()).toBe(
      programText("qasm paper block 2"),
    );
    // The circuit came in the page's history state, which a reload does not keep
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "Circuits" })).toBeVisible();
    await expect(latexBox(page)).toHaveValue("");
    await expect(page.getByTestId("gate-table")).toHaveCount(0);
  });

  test("opens every circuit block of the cited paper", async ({ page }) => {
    const panel = await openTeleportPaper(page);
    await panel.getByRole("link", { name: "Circuits of this paper" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Circuits" })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/circuits\\?doc=${TELEPORT_ID}$`));
    await expect(page.getByRole("tab", { name: "Paper", selected: true })).toBeVisible();
    await expect(page.getByTestId("paper-block")).toHaveCount(2);
  });
});

test.describe("the recorded answers", () => {
  for (const entry of recorded) {
    test(`still match the API: ${entry.name}`, async ({ api }) => {
      const url = `/api/v1${entry.path}`;
      const answer =
        entry.request === null
          ? await api.get(url)
          : await api.post(url, {
              data:
                "raw" in entry.request ? String(entry.request.raw) : JSON.stringify(entry.request),
              headers: { "content-type": "application/json" },
            });
      expect(answer.status()).toBe(entry.status);
      expect(await answer.json()).toEqual(entry.response);
    });
  }
});

test.describe("themes, languages and small screens", () => {
  for (const colorScheme of ["light", "dark"] as const) {
    test.describe(`${colorScheme} theme`, () => {
      test.use({ colorScheme });
      test("has no accessibility violations with a result on the screen", async ({ page }) => {
        await openCircuits(page);
        await readExample(page, "hzhEqualsX");
        await page.getByRole("button", { name: "Simulate" }).click();
        await expect(page.getByTestId("state-table").first()).toBeVisible();
        await page.getByRole("radio", { name: "The drawn equality" }).click();
        await page.getByRole("button", { name: "Check" }).click();
        await expect(page.getByTestId("equal")).toBeVisible();
        await expectNoAxeViolations(page);
        await readExample(page, "bundle");
        await page.getByRole("button", { name: "Simulate" }).click();
        await expect(page.getByTestId("refusal")).toBeVisible();
        await expectNoAxeViolations(page);
      });

      test("has no accessibility violations with an export and a refusal on the screen", async ({
        page,
      }) => {
        await openCircuits(page);
        await readExample(page, "hzhEqualsX");
        await page.getByLabel("Parameter values").fill("theta = 1");
        await exportButton(page).click();
        await expect(page.getByTestId("qasm-program")).toHaveCount(2);
        await expect(page.getByTestId("export-unmatched")).toBeVisible();
        await program(page, "circuit-p01.qasm").focus();
        await expectNoAxeViolations(page);
        await page.getByLabel("Parameter values").fill("");
        await readExample(page, "rotation");
        await exportButton(page).click();
        await expect(page.getByTestId("export-refusal")).toBeVisible();
        await expect(page.getByTestId("export-unbound")).toBeVisible();
        await expectNoAxeViolations(page);
        await pickTeleportPaper(page);
        await page.getByText("LaTeX of block 1").click();
        await page.getByRole("button", { name: "Export this paper" }).click();
        await expect(page.getByTestId("paper-circuit")).toHaveCount(2);
        await expect(page.getByTestId("paper-export").getByTestId("export-refusal")).toBeVisible();
        await expectNoAxeViolations(page);
      });
    });
  }

  test("works in Spanish", async ({ page }) => {
    await openCircuits(page);
    await page.getByRole("button", { name: "Language: English" }).click();
    await page.getByRole("menuitemradio", { name: "Español" }).click();
    await page.getByLabel("Probar un ejemplo").selectOption("bell");
    await expect(page.getByText("Leído completo")).toBeVisible();
    await page.getByRole("button", { name: "Simular" }).click();
    await expect(page.getByTestId("state-table")).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("exports in Spanish", async ({ page }) => {
    await openCircuits(page);
    await page.getByRole("button", { name: "Language: English" }).click();
    await page.getByRole("menuitemradio", { name: "Español" }).click();
    await page.getByLabel("Probar un ejemplo").selectOption("bell");
    await expect(page.getByText("Leído completo")).toBeVisible();
    await page.getByRole("button", { name: "Exportar", exact: true }).click();
    const result = page.getByRole("region", { name: "Programas OpenQASM 3" });
    await expect(result).toContainText("Parte 1 \u00b7 Qubits: 2. Bits clásicos: 0.");
    expect(
      await page
        .getByRole("region", { name: "Programa OpenQASM 3 circuit-p01.qasm" })
        .textContent(),
    ).toBe(programText("qasm bell"));
    await expect(page.getByRole("button", { name: "Copiar circuit-p01.qasm" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Descargar circuit-p01.qasm" })).toBeVisible();
    await expect(page.getByTestId("live-status")).toContainText(
      "Exportación terminada. Programas: 1.",
    );
    await expectNoAxeViolations(page);

    await page.getByRole("tab", { name: "Artículo" }).click();
    await page.getByLabel("Título o id").fill("teleportation");
    await page.getByRole("button", { name: "Buscar artículos" }).click();
    await page.getByRole("button", { name: TELEPORT_TITLE }).click();
    await expect(page.getByTestId("paper-block").nth(1)).toContainText("Bloque 2");
    await expect(page.getByTestId("paper-block").nth(1)).toContainText("Leído completo");
    await page.getByRole("button", { name: "Exportar este artículo" }).click();
    const exported = page.getByTestId("paper-export");
    await expect(exported).toContainText(`Programas de ${TELEPORT_SOURCE}`);
    await expect(exported).toContainText("Programas: 1. Circuitos rechazados: 1.");
    // The reasons are the emitter's, word for word, in English
    await expect(exported.getByTestId("export-refusal")).toContainText("Rechazado");
    await expect(exported.getByTestId("export-refusal")).toContainText(
      "status: the circuit was read as 'partial', not 'parsed'",
    );
    await expect(page.getByRole("button", { name: `Descargar ${TELEPORT_FILE}` })).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("fits a phone without scrolling the page sideways", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await openCircuits(page);
    await page.getByRole("tab", { name: "circuit/1 JSON" }).click();
    await jsonBox(page).fill(
      JSON.stringify({
        format: "circuit/1",
        wires: 4,
        parts: [[[{ kind: "gate", label: "H", targets: [1] }]]],
      }),
    );
    await page.getByLabel("Also show the unitary (up to 8 wires)").click();
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByTestId("unitary-table")).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("fits a phone with programs and a paper's export on the screen", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await openCircuits(page);
    await readExample(page, "measured");
    await exportButton(page).click();
    await expect(page.getByTestId("qasm-program")).toHaveCount(1);
    await expectNoSidewaysScroll(page);
    /** Every program box ends inside the screen; a long line scrolls inside its own box. */
    const expectProgramsInside = async () => {
      const boxes = await page
        .getByTestId("qasm-program")
        .locator("pre")
        .evaluateAll((codes) =>
          codes.map((code) => ({
            right: code.getBoundingClientRect().right,
            screen: document.documentElement.clientWidth,
            wide: code.scrollWidth > code.clientWidth,
            focusable: code.tabIndex === 0,
          })),
        );
      expect(boxes.length).toBeGreaterThan(0);
      for (const box of boxes) {
        expect(box.right).toBeLessThanOrEqual(box.screen);
        if (box.wide) expect(box.focusable).toBe(true);
      }
    };
    await expectProgramsInside();
    await program(page, "circuit-p01.qasm").focus();
    await expectNoAxeViolations(page);
    await pickTeleportPaper(page);
    await expectNoSidewaysScroll(page);
    await page.getByRole("button", { name: "Export this paper" }).click();
    await expect(page.getByTestId("paper-export").getByTestId("qasm-program")).toHaveCount(1);
    await expectNoSidewaysScroll(page);
    await expectProgramsInside();
    await expectNoAxeViolations(page);
  });

  /** Simulate a circuit of `wires` wires with its unitary, on the screen as it is now. */
  async function simulateWithUnitary(page: Page, wires: number) {
    await page.getByRole("tab", { name: "circuit/1 JSON" }).click();
    await jsonBox(page).fill(
      JSON.stringify({
        format: "circuit/1",
        wires,
        parts: [[[{ kind: "gate", label: "H", targets: [1] }]]],
      }),
    );
    const unitary = page.getByLabel("Also show the unitary (up to 8 wires)");
    if (!(await unitary.isChecked())) await unitary.click();
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByTestId("unitary-table")).toBeVisible();
  }

  /**
   * Every table of the result stays inside its card, and one that is wider than the card scrolls in
   * its own box, which a keyboard can reach. A card that cuts a table off is the failure to catch.
   */
  async function expectTablesToScrollInsideTheirCards(page: Page) {
    const boxes = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-slot="table-container"]')].map((box) => {
        const card = box.closest<HTMLElement>('[data-slot="card"]')!;
        const inside = box.querySelector("table")!.getAttribute("data-testid") ?? "table";
        return {
          inside,
          boxRight: box.getBoundingClientRect().right,
          cardRight: card.getBoundingClientRect().right,
          boxWidth: box.clientWidth,
          cardWidth: card.clientWidth,
          wide: box.scrollWidth > box.clientWidth,
          focusable: box.tabIndex === 0 && box.getAttribute("role") === "region",
        };
      }),
    );
    expect(boxes.length).toBeGreaterThan(0);
    for (const box of boxes) {
      expect(box.boxRight, `${box.inside} ends inside its card`).toBeLessThanOrEqual(
        box.cardRight + 1,
      );
      expect(box.boxWidth, `${box.inside} is no wider than its card`).toBeLessThanOrEqual(
        box.cardWidth,
      );
      if (box.wide) expect(box.focusable, `${box.inside} can take focus`).toBe(true);
    }
    return boxes;
  }

  for (const [wires, width] of [
    [4, 375],
    [8, 375],
    [4, 1280],
    [8, 1280],
  ] as const) {
    test(`keeps a ${wires}-wire unitary inside its card at ${width} px, scrollable by keyboard`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await openCircuits(page);
      await simulateWithUnitary(page, wires);
      const boxes = await expectTablesToScrollInsideTheirCards(page);
      const unitary = boxes.find((box) => box.inside === "unitary-table")!;
      // 16 columns of complex numbers never fit a phone, and 8 wires do not fit a desktop card
      if (width === 375 || wires === 8) expect(unitary.wide).toBe(true);
      const region = page.getByRole("region", { name: /^Unitary, / });
      await region.focus();
      await expect(region).toBeFocused();
      if (unitary.wide) {
        // Only the arrow key moves the box: it scrolls a few dozen pixels per press
        const atEnd = () =>
          region.evaluate((box) => box.scrollLeft + box.clientWidth >= box.scrollWidth - 1);
        await page.keyboard.press("ArrowRight");
        await expect.poll(() => region.evaluate((box) => box.scrollLeft)).toBeGreaterThan(0);
        for (let presses = 0; presses < 200 && !(await atEnd()); presses += 1) {
          await page.keyboard.press("ArrowRight");
        }
        expect(await atEnd(), "the arrow key reaches the last column").toBe(true);
        const last = region.locator("tbody tr").first().locator("td").last();
        await expect(last).toBeInViewport();
      }
      if (width === 375) {
        const state = page.getByRole("region", { name: /^Final state/ });
        await state.focus();
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow).toBeLessThanOrEqual(0);
      }
      await expectNoAxeViolations(page);
    });
  }

  test("keeps the tables of a drawn equality and a check inside their cards on a phone", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 900 });
    await openCircuits(page);
    await readExample(page, "hzhEqualsX");
    await page.getByLabel("Also show the unitary (up to 8 wires)").click();
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByTestId("state-table")).toHaveCount(2);
    await expectTablesToScrollInsideTheirCards(page);
    await page.getByRole("radio", { name: "The drawn equality" }).click();
    await page.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("comparisons")).toBeVisible();
    await expectTablesToScrollInsideTheirCards(page);
    await expectNoAxeViolations(page);
  });

  test("reads with the keyboard", async ({ page }) => {
    await openCircuits(page);
    await latexBox(page).fill(BELL);
    await latexBox(page).press("Control+Enter");
    await expect(page.getByText("Read in full")).toBeVisible();
  });
});
