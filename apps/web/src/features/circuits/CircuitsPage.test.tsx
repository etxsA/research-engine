import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedAnswer } from "@/test/circuits-api";
import { renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";
import { initialCircuitState, useCircuitStore } from "./store";
import { exampleById } from "./examples";

type User = ReturnType<typeof userEvent.setup>;

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

/** Puts text in a box at once: typing LaTeX character by character is slow and adds nothing. */
async function paste(user: User, box: HTMLElement, text: string) {
  await user.click(box);
  await user.paste(text);
}

async function readExample(user: User, id: string) {
  await user.selectOptions(screen.getByLabelText("Try an example"), id);
}

describe("the circuit check screen", () => {
  it("opens with the input, the two actions and the conventions", async () => {
    await openPage();
    expect(screen.getByRole("tab", { name: "LaTeX", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read circuit" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Simulate" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();
    expect(await screen.findByText(/wire 1 is the top wire of the drawing/)).toBeInTheDocument();
  });

  it("asks for a circuit when there is none to read", async () => {
    const user = await openPage();
    await user.click(screen.getByRole("button", { name: "Read circuit" }));
    expect(await screen.findByText("Paste a circuit first.")).toBeInTheDocument();
  });

  it("reads LaTeX and lists the gates of each step", async () => {
    const user = await openPage();
    await paste(
      user,
      screen.getByLabelText("LaTeX code of one circuit"),
      exampleById("bell")!.text,
    );
    await user.click(screen.getByRole("button", { name: "Read circuit" }));
    expect(await screen.findByText("Read in full")).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: /Gates of circuit 1/ });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(
      within(rows[0]!)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["1", "H", "1", "\u2013none", "\u2013none"]);
    expect(
      within(rows[1]!)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["2", "X(CNOT)", "2", "1", "\u2013none"]);
    expect(screen.getByText(/Step 2: CNOT: control wire 1, target wire 2/)).toBeInTheDocument();
  });

  it("lists the parts that were not read and says they block the simulation", async () => {
    const user = await openPage();
    await readExample(user, "partial");
    expect(await screen.findByText("Read in part")).toBeInTheDocument();
    const unread = await screen.findByTestId("unread");
    expect(within(unread).getByText("Parts that were not read")).toBeInTheDocument();
    expect(within(unread).getByText(/Step 2, wire 2:/)).toBeInTheDocument();
    expect(within(unread).getByText(/iddots/)).toBeInTheDocument();
    // the gate table marks the same item
    expect(screen.getAllByText("Not read").length).toBeGreaterThan(0);
  });

  it("says when no circuit was found", async () => {
    const user = await openPage();
    await paste(user, screen.getByLabelText("LaTeX code of one circuit"), "no circuit here");
    await user.click(screen.getByRole("button", { name: "Read circuit" }));
    expect(await screen.findByText("Not read")).toBeInTheDocument();
    expect(screen.getByText("No circuit was found in this text.")).toBeInTheDocument();
    expect(screen.getByText("There is no circuit to show.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Simulate" })).toBeDisabled();
  });

  it("lets a person choose among several circuits", async () => {
    const user = await openPage();
    const request = capturedAnswer("parse two circuits").request as { latex: string };
    await paste(user, screen.getByLabelText("LaTeX code of one circuit"), request.latex);
    await user.click(screen.getByRole("button", { name: "Read circuit" }));
    const choice = await screen.findByRole("radiogroup", { name: "Circuit to use" });
    const radios = within(choice).getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(radios[0]).toBeChecked();
    await user.click(radios[1]!);
    expect(
      await screen.findByRole("table", { name: /Circuit 2, left side of the equality/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: /Circuit 2, right side, after "="/ }),
    ).toBeInTheDocument();
  });

  it("shows both sides of a drawn equality", async () => {
    const user = await openPage();
    await readExample(user, "hzhEqualsX");
    expect(
      await screen.findByRole("table", { name: /left side of the equality/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /right side, after "="/ })).toBeInTheDocument();
  });

  describe("a circuit/1 document", () => {
    it("is read as it is", async () => {
      const user = await openPage();
      await readExample(user, "bellJson");
      expect(await screen.findByText("Read in full")).toBeInTheDocument();
      expect(
        screen.getByRole("tab", { name: "circuit/1 JSON", selected: true }),
      ).toBeInTheDocument();
      expect(screen.getByRole("table", { name: /Gates of circuit 1/ })).toBeInTheDocument();
      expect(screen.getByText("Wires: 2, numbered from 1 at the top.")).toBeInTheDocument();
    });

    it("is refused with the name of a key written twice", async () => {
      const user = await openPage();
      await user.click(screen.getByRole("tab", { name: "circuit/1 JSON" }));
      await paste(
        user,
        screen.getByLabelText("circuit/1 document as JSON"),
        '{"format": "circuit/1", "wires": 1, "wires": 2, "parts": []}',
      );
      await user.click(screen.getByRole("button", { name: "Read circuit" }));
      const problem = await screen.findByTestId("problem");
      expect(within(problem).getByText("A key is written twice")).toBeInTheDocument();
      expect(within(problem).getByText(/The key "wires" appears twice/)).toBeInTheDocument();
      // and the same text is not sent to be simulated either
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      expect(await screen.findByText(/The key "wires" appears twice/)).toBeInTheDocument();
    });

    it("is refused when it is not JSON or not a circuit", async () => {
      const user = await openPage();
      await user.click(screen.getByRole("tab", { name: "circuit/1 JSON" }));
      const box = screen.getByLabelText("circuit/1 document as JSON");
      await paste(user, box, "{ nope");
      await user.click(screen.getByRole("button", { name: "Read circuit" }));
      expect(await screen.findByText("Not valid JSON")).toBeInTheDocument();
      await user.clear(box);
      await paste(user, box, '{"a": 1}');
      await user.click(screen.getByRole("button", { name: "Read circuit" }));
      expect(await screen.findByText("Not a circuit/1 document")).toBeInTheDocument();
    });

    it("lets the server refuse what is JSON but not a circuit", async () => {
      const user = await openPage();
      await user.click(screen.getByRole("tab", { name: "circuit/1 JSON" }));
      await paste(user, screen.getByLabelText("circuit/1 document as JSON"), '{"a": 1}');
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      const refusal = await screen.findByTestId("refusal");
      expect(
        within(refusal).getByText("format: expected 'circuit/1', got 'None'"),
      ).toBeInTheDocument();
    });
  });

  describe("simulating", () => {
    it("shows the state, the probabilities with the first wire most significant, and the unitary", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByLabelText("Also show the unitary (up to 8 wires)"));
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      const state = await screen.findByTestId("state-table");
      const stateRows = within(state).getAllByRole("row").slice(1);
      expect(stateRows.map((row) => row.textContent)).toEqual([
        "|00\u27E90.707150%",
        "|11\u27E90.707150%",
      ]);
      const bars = within(screen.getByTestId("probabilities")).getAllByRole("listitem");
      expect(bars.map((item) => item.textContent)).toEqual(["|00\u27E950%", "|11\u27E950%"]);
      expect(screen.getAllByRole("button", { name: "Show the 2 zero rows" })).toHaveLength(2);
      const unitary = screen.getByTestId("unitary-table");
      expect(within(unitary).getByText("Unitary, 4 by 4")).toBeInTheDocument();
      expect(within(unitary).getAllByRole("row")).toHaveLength(5);
    });

    it("shows the zero rows when asked", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      await user.click(screen.getAllByRole("button", { name: "Show the 2 zero rows" })[0]!);
      expect(within(screen.getByTestId("state-table")).getAllByRole("row")).toHaveLength(5);
      await user.click(screen.getAllByRole("button", { name: "Hide the zero rows" })[0]!);
      expect(within(screen.getByTestId("state-table")).getAllByRole("row")).toHaveLength(3);
    });

    it("uses the input state that was given", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.type(screen.getByLabelText("Input state"), "10");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      const state = await screen.findByTestId("state-table");
      expect(
        within(state)
          .getAllByRole("row")
          .slice(1)
          .map((row) => row.textContent),
        // H on |1> gives (|0> - |1>)/sqrt 2 and the CNOT copies the sign onto |11>
      ).toEqual(["|00\u27E90.707150%", "|11\u27E9\u22120.707150%"]);
      const simulation = screen.getByRole("region", { name: "Simulation" });
      expect(within(simulation).getByText("Input state").nextElementSibling).toHaveTextContent(
        "10",
      );
    });

    it("tells which wires were measured", async () => {
      const user = await openPage();
      await readExample(user, "measured");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      expect(await screen.findByText("Measured wires: 1, 2")).toBeInTheDocument();
    });

    it("shows the two sides of a drawn equality one after the other", async () => {
      const user = await openPage();
      await readExample(user, "hzhEqualsX");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      expect(await screen.findByRole("heading", { name: "Left side" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: 'Right side, after "="' })).toBeInTheDocument();
      expect(screen.getAllByTestId("state-table")).toHaveLength(2);
    });

    it("asks for the value of a symbol, then simulates with it", async () => {
      const user = await openPage();
      await readExample(user, "rotation");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      const unbound = await screen.findByTestId("unbound");
      expect(within(unbound).getByText(/symbols with no value: theta/)).toBeInTheDocument();
      expect(screen.queryByTestId("state-table")).not.toBeInTheDocument();
      await user.click(
        within(unbound).getByRole("button", { name: "Add the names to the values" }),
      );
      const values = screen.getByLabelText("Parameter values");
      expect(values).toHaveValue("theta = ");
      await user.type(values, "\\pi/2");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      const state = await screen.findByTestId("state-table");
      expect(
        within(state)
          .getAllByRole("row")
          .slice(1)
          .map((row) => row.textContent),
      ).toEqual(["|0\u27E90.707150%", "|1\u27E90.707150%"]);
      expect(screen.getByText("theta = 1.5707963267948966")).toBeInTheDocument();
    });

    it("refuses a bad value line before sending anything", async () => {
      const user = await openPage();
      await readExample(user, "rotation");
      await screen.findByText("Read in full");
      await user.type(screen.getByLabelText("Parameter values"), "theta");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      expect(
        await screen.findByText("Line 1 (theta): write name = value, once per name."),
      ).toBeInTheDocument();
    });

    it("refuses a key that is not one symbol before sending anything", async () => {
      const user = await openPage();
      await readExample(user, "rotation");
      await screen.findByText("Read in full");
      const sent: string[] = [];
      server.events.on("request:start", ({ request }) => void sent.push(request.url));
      try {
        await user.type(screen.getByLabelText("Parameter values"), "thetta = 1");
        await user.click(screen.getByRole("button", { name: "Simulate" }));
        expect(
          await screen.findByText(
            'Line 1: "thetta" is not a parameter name. Write one symbol, such as theta, \\theta or \\theta_{1}.',
          ),
        ).toBeInTheDocument();
        expect(sent.filter((url) => url.includes("/circuits/run"))).toEqual([]);
      } finally {
        server.events.removeAllListeners();
      }
    });

    it("refuses an input state that is not a label or a list", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.type(screen.getByLabelText("Input state"), "ket");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      expect(await screen.findByText("The state is not valid")).toBeInTheDocument();
    });

    it("shows a circuit the simulator refuses as a refusal with every reason, never as a result", async () => {
      const user = await openPage();
      await readExample(user, "bundle");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      const refusal = await screen.findByTestId("refusal");
      expect(within(refusal).getByText("Refused")).toBeInTheDocument();
      expect(
        within(refusal).getByText(
          "wire 2: the legend '2 = a, bundle m' shows a bundle, a register, a row of dots or a classical wire",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("state-table")).not.toBeInTheDocument();
    });

    it("explains why a partly read circuit is refused", async () => {
      const user = await openPage();
      await readExample(user, "partial");
      await screen.findByText("Read in part");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      expect(
        await screen.findByText("status: the circuit was read as 'partial', not 'parsed'"),
      ).toBeInTheDocument();
    });
  });

  describe("checking", () => {
    async function readBell(user: User) {
      await readExample(user, "bell");
      await screen.findByText("Read in full");
    }

    it("says equal up to global phase, with the method", async () => {
      const user = await openPage();
      await readBell(user);
      await paste(
        user,
        screen.getByLabelText("Target state"),
        "[0.7071067811865476, 0, 0, 0.7071067811865476]",
      );
      await user.click(screen.getByRole("button", { name: "Check" }));
      const verdict = await screen.findByTestId("equal");
      expect(within(verdict).getByText("Equal up to global phase")).toBeInTheDocument();
      expect(within(screen.getByTestId("comparisons")).getByText("arrays")).toBeInTheDocument();
    });

    it("says not equal, with the largest deviation, and not as a refusal", async () => {
      const user = await openPage();
      await readBell(user);
      await user.type(screen.getByLabelText("Target state"), "11");
      await user.click(screen.getByRole("button", { name: "Check" }));
      const verdict = await screen.findByTestId("not-equal");
      expect(within(verdict).getByText("Not equal")).toBeInTheDocument();
      expect(within(verdict).getByText("Largest deviation: 0.707107")).toBeInTheDocument();
      expect(screen.queryByTestId("refusal")).not.toBeInTheDocument();
    });

    it("compares with a unitary", async () => {
      const user = await openPage();
      await user.click(screen.getByRole("tab", { name: "circuit/1 JSON" }));
      const request = capturedAnswer("check hzh unitary x").request as { circuit_json: string };
      await paste(user, screen.getByLabelText("circuit/1 document as JSON"), request.circuit_json);
      await user.click(screen.getByRole("button", { name: "Read circuit" }));
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("radio", { name: "A unitary" }));
      await paste(user, screen.getByLabelText("Target unitary"), "[[0, 1], [1, 0]]");
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(await screen.findByTestId("equal")).toBeInTheDocument();
      expect(within(screen.getByTestId("comparisons")).getByText("unitary")).toBeInTheDocument();
    });

    it("compares with another circuit written in LaTeX", async () => {
      const user = await openPage();
      await user.click(screen.getByRole("tab", { name: "circuit/1 JSON" }));
      await readExample(user, "bellJson");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("radio", { name: "Another circuit" }));
      await user.click(screen.getByLabelText("Second circuit"));
      await user.paste(exampleById("bell")!.text);
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(await screen.findByTestId("equal")).toBeInTheDocument();
      expect(within(screen.getByTestId("comparisons")).getByText("unitary")).toBeInTheDocument();
    });

    it("checks the two sides of a drawn equality, left against right", async () => {
      const user = await openPage();
      await readExample(user, "hzhEqualsX");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("radio", { name: "The drawn equality" }));
      await user.click(screen.getByRole("button", { name: "Check" }));
      const verdict = await screen.findByTestId("equal");
      expect(within(verdict).getByText("Equal up to global phase")).toBeInTheDocument();
      expect(
        within(screen.getByTestId("comparisons")).getByText("part 1 / part 2"),
      ).toBeInTheDocument();
    });

    it("does not offer a drawn equality when the circuit has no equality", async () => {
      const user = await openPage();
      await readBell(user);
      expect(screen.getByRole("radio", { name: "The drawn equality" })).toBeDisabled();
    });

    it("refuses a target that is not valid before sending anything", async () => {
      const user = await openPage();
      await readBell(user);
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(await screen.findByText("No target given")).toBeInTheDocument();
      await user.type(screen.getByLabelText("Target state"), "nope");
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(await screen.findByText("The state is not valid")).toBeInTheDocument();
      await user.click(screen.getByRole("radio", { name: "A unitary" }));
      await paste(user, screen.getByLabelText("Target unitary"), "x");
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(await screen.findByText("The unitary is not valid")).toBeInTheDocument();
    });

    it("shows the simulator's refusal of a target of the wrong size", async () => {
      const user = await openPage();
      await readBell(user);
      await user.click(screen.getByRole("radio", { name: "A unitary" }));
      await user.click(screen.getByLabelText("Target unitary"));
      await user.paste("[[1, 0], [0, 1]]");
      await user.click(screen.getByRole("button", { name: "Check" }));
      const refusal = await screen.findByTestId("refusal");
      expect(
        within(refusal).getByText(
          "a circuit of 2 wires is a 4 x 4 matrix, but the array has shape (2, 2)",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("not-equal")).not.toBeInTheDocument();
    });

    it("says when the second circuit holds none or several", async () => {
      const user = await openPage();
      await readBell(user);
      await user.click(screen.getByRole("radio", { name: "Another circuit" }));
      await user.click(screen.getByLabelText("Second circuit"));
      await user.paste("no circuit here");
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(await screen.findByText("The second circuit was not read")).toBeInTheDocument();
      await user.clear(screen.getByLabelText("Second circuit"));
      await user.click(screen.getByLabelText("Second circuit"));
      await user.paste((capturedAnswer("parse two circuits").request as { latex: string }).latex);
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(
        await screen.findByText("More than one circuit in the second text"),
      ).toBeInTheDocument();
    });
  });

  describe("problems from the server", () => {
    it("shows the reason and the field of a request over the caps", async () => {
      const user = await openPage();
      await user.click(screen.getByRole("tab", { name: "circuit/1 JSON" }));
      const request = capturedAnswer("run thirteen wires").request as { circuit_json: string };
      await paste(user, screen.getByLabelText("circuit/1 document as JSON"), request.circuit_json);
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      const problem = await screen.findByTestId("problem");
      expect(within(problem).getByText("The circuit could not be processed")).toBeInTheDocument();
      expect(within(problem).getByText(/this check takes at most 12/)).toBeInTheDocument();
    });

    it("tells when the circuit service is busy and when to try again", async () => {
      server.use(
        http.post("*/api/v1/circuits/run", () =>
          HttpResponse.json(
            {
              type: "urn:x",
              title: "Service Unavailable",
              status: 503,
              detail: "All circuit jobs are busy.",
              instance: "/",
            },
            {
              status: 503,
              headers: { "content-type": "application/problem+json", "retry-after": "5" },
            },
          ),
        ),
      );
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      const problem = await screen.findByTestId("problem");
      expect(within(problem).getByText("The circuit service is busy")).toBeInTheDocument();
      expect(within(problem).getByText("Try again in 5 seconds.")).toBeInTheDocument();
    });

    it("shows a problem when the server cannot be reached, and works again after", async () => {
      server.use(http.post("*/api/v1/circuits/parse", () => HttpResponse.error()));
      const user = await openPage();
      await readExample(user, "bell");
      expect(await screen.findByText("The server could not be reached")).toBeInTheDocument();
      server.resetHandlers();
      await user.click(screen.getByRole("button", { name: "Read circuit" }));
      expect(await screen.findByText("Read in full")).toBeInTheDocument();
      expect(screen.queryByTestId("problem")).not.toBeInTheDocument();
    });

    it("does not crash on an answer that is not what the contract says", async () => {
      server.use(
        http.post("*/api/v1/circuits/parse", () => HttpResponse.json({ status: "weird" })),
      );
      const user = await openPage();
      await readExample(user, "bell");
      expect(await screen.findByText("The server's answer could not be read")).toBeInTheDocument();
    });
  });

  describe("what a screen reader hears and what the layout does", () => {
    /** What each of the two status regions says right now. */
    const regions = () =>
      within(screen.getByTestId("live-status"))
        .getAllByRole("status")
        .map((region) => region.textContent);

    it("announces a reading and a result with one short sentence, not with the tables", async () => {
      const user = await openPage();
      expect(regions()).toEqual(["", ""]);
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      expect(screen.getByTestId("live-status")).toHaveTextContent(
        "Reading finished: every part understood. Circuits found: 1.",
      );
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      expect(screen.getByTestId("live-status")).toHaveTextContent("Simulation finished.");
      expect(document.querySelectorAll("[aria-live]")).toHaveLength(0);
    });

    it("keeps what it said while the person edits, and says nothing new", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      const said = regions();
      expect(said.filter(Boolean)).toEqual(["Simulation finished."]);
      // Typing clears the result on the screen, but nothing is announced
      await user.type(screen.getByLabelText("Input state"), "1");
      expect(screen.queryByTestId("state-table")).not.toBeInTheDocument();
      expect(regions()).toEqual(said);
      await user.type(screen.getByLabelText("Parameter values"), "x");
      expect(regions()).toEqual(said);
    });

    it("announces a second identical result again, in the other region", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      const first = regions();
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await waitFor(() => expect(regions()).not.toEqual(first));
      const second = regions();
      // The same sentence, now added to the region that was empty and removed from the other
      expect(second.filter(Boolean)).toEqual(["Simulation finished."]);
      expect(second.findIndex(Boolean)).not.toBe(first.findIndex(Boolean));
    });

    it("clears the hidden sentence when a refusal arrives, so it never describes a result that is not there", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      expect(regions().filter(Boolean)).toEqual(["Simulation finished."]);
      await readExample(user, "bundle");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("refusal");
      expect(regions()).toEqual(["", ""]);
      expect(screen.getByTestId("live-status")).not.toHaveTextContent("Simulation finished.");
    });

    it("clears the hidden sentence when a problem arrives, and speaks again after", async () => {
      const user = await openPage();
      await readExample(user, "rotation");
      await screen.findByText("Read in full");
      expect(regions().filter(Boolean)).toHaveLength(1);
      await user.type(screen.getByLabelText("Parameter values"), "theta");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByText("Line 1 (theta): write name = value, once per name.");
      expect(regions()).toEqual(["", ""]);
      await user.clear(screen.getByLabelText("Parameter values"));
      await user.type(screen.getByLabelText("Parameter values"), "theta = \\pi/2");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      expect(regions().filter(Boolean)).toEqual(["Simulation finished."]);
    });

    it("clears the hidden sentence when the server answers with a problem", async () => {
      server.use(
        http.post("*/api/v1/circuits/run", () =>
          HttpResponse.json(
            {
              type: "urn:x",
              title: "Service Unavailable",
              status: 503,
              detail: "Busy.",
              instance: "/",
            },
            {
              status: 503,
              headers: { "content-type": "application/problem+json", "retry-after": "5" },
            },
          ),
        ),
      );
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      expect(regions().filter(Boolean)).toHaveLength(1);
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("problem");
      expect(regions()).toEqual(["", ""]);
    });

    it("announces the verdict of a check", async () => {
      const user = await openPage();
      await readExample(user, "hzhEqualsX");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("radio", { name: "The drawn equality" }));
      await user.click(screen.getByRole("button", { name: "Check" }));
      await screen.findByTestId("equal");
      expect(screen.getByTestId("live-status")).toHaveTextContent(
        "Check finished: equal up to global phase.",
      );
    });

    it("lets a keyboard reach every table that can scroll sideways", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByLabelText("Also show the unitary (up to 8 wires)"));
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("unitary-table");
      for (const name of [
        /^Gates of circuit 1/,
        /^Final state/,
        "Unitary, 4 by 4",
        "Gates the simulator knows, by name",
      ]) {
        const region = screen.getByRole("region", { name });
        expect(region, String(name)).toHaveAttribute("tabindex", "0");
        expect(region.querySelector("table")).not.toBeNull();
      }
    });

    it("starts a new result with its own zero-row switches", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      await user.click(screen.getAllByRole("button", { name: "Show the 2 zero rows" })[0]!);
      expect(within(screen.getByTestId("state-table")).getAllByRole("row")).toHaveLength(5);
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await waitFor(() =>
        expect(within(screen.getByTestId("state-table")).getAllByRole("row")).toHaveLength(3),
      );
      expect(screen.getAllByRole("button", { name: "Show the 2 zero rows" })).toHaveLength(2);
    });

    it("keeps the heading order: probabilities sit one level under the result, or under a side", async () => {
      const user = await openPage();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      const simulation = screen.getByRole("region", { name: "Simulation" });
      expect(
        within(simulation).getByRole("heading", { level: 3, name: /^Outcome probabilities/ }),
      ).toBeInTheDocument();
      await readExample(user, "hzhEqualsX");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findAllByTestId("state-table");
      const both = screen.getByRole("region", { name: "Simulation" });
      expect(
        within(both)
          .getAllByRole("heading", { level: 3 })
          .map((h) => h.textContent),
      ).toEqual(["Left side", 'Right side, after "="']);
      expect(within(both).getAllByRole("heading", { level: 4 })).toHaveLength(2);
    });
  });

  describe("conventions", () => {
    it("shows what the simulator assumes, then what the last result stated", async () => {
      const user = await openPage();
      expect(
        await screen
          .findByText("As stated in the last result.", {}, { timeout: 50 })
          .catch(() => null),
      ).toBeNull();
      const card = await screen.findByRole("region", { name: "Conventions" });
      expect(within(card).getByText("Wire order")).toBeInTheDocument();
      expect(within(card).getByText(/most significant bit/)).toBeInTheDocument();
      expect(
        within(card).getByText(/R_a\(theta\) = exp\(-i theta sigma_a \/ 2\)/),
      ).toBeInTheDocument();
      expect(within(card).getByText(/max \|a - e\^\{i phi\} b\| < 1e-9/)).toBeInTheDocument();
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await screen.findByTestId("state-table");
      expect(within(card).getByText("As stated in the last result.")).toBeInTheDocument();
    });

    it("lists the gate table and opens it from the result", async () => {
      const user = await openPage();
      const table = await screen.findByTestId("gate-table-reference");
      expect(within(table).getByText("rzz")).toBeInTheDocument();
      expect(within(table).getByText("SWAP: |ab> -> |ba>")).toBeInTheDocument();
      const details = table.closest("details")!;
      expect(details).not.toHaveAttribute("open");
      await readExample(user, "bell");
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Simulate" }));
      await user.click(
        await screen.findByRole("link", { name: "See the conventions and the gate table" }),
      );
      expect(details).toHaveAttribute("open");
    });

    it("writes a limit the server did not give as a question mark, like the others", async () => {
      const answer = capturedAnswer("conventions").response as { limits: Record<string, number> };
      const { ops, ...rest } = answer.limits;
      void ops;
      server.use(
        http.get("*/api/v1/circuits/conventions", () =>
          HttpResponse.json({ ...answer, limits: { ...rest, statevector_wires: 12 } }),
        ),
      );
      await openPage();
      expect(
        await screen.findByText(
          "The simulator itself takes up to 12 wires for a state, 10 for a unitary and ? operations.",
        ),
      ).toBeInTheDocument();
    });

    it("says so when the conventions cannot be loaded", async () => {
      server.use(http.get("*/api/v1/circuits/conventions", () => HttpResponse.json({ nope: 1 })));
      await openPage();
      expect(await screen.findByText("The conventions could not be loaded.")).toBeInTheDocument();
    });
  });

  it("drops an answer for a circuit that has been edited since", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("*/api/v1/circuits/parse", async () => {
        await gate;
        return HttpResponse.json(capturedAnswer("parse bell").response);
      }),
    );
    const user = await openPage();
    const box = screen.getByLabelText("LaTeX code of one circuit");
    await paste(user, box, exampleById("bell")!.text);
    await user.click(screen.getByRole("button", { name: "Read circuit" }));
    expect(await screen.findByRole("button", { name: "Reading" })).toBeDisabled();
    await user.type(box, " ");
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: "Read circuit" })).toBeEnabled());
    expect(screen.queryByText("Read in full")).not.toBeInTheDocument();
  });

  it("keeps a reading when only the options are edited while it is on its way", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("*/api/v1/circuits/parse", async () => {
        await gate;
        return HttpResponse.json(capturedAnswer("parse bell").response);
      }),
    );
    const user = await openPage();
    await paste(
      user,
      screen.getByLabelText("LaTeX code of one circuit"),
      exampleById("bell")!.text,
    );
    await user.click(screen.getByRole("button", { name: "Read circuit" }));
    await screen.findByRole("button", { name: "Reading" });
    await user.type(screen.getByLabelText("Input state"), "10");
    release();
    expect(await screen.findByText("Read in full")).toBeInTheDocument();
    expect(screen.getByLabelText("Input state")).toHaveValue("10");
  });

  it("clears everything with Clear", async () => {
    const user = await openPage();
    await readExample(user, "bell");
    await screen.findByText("Read in full");
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByText("Read in full")).not.toBeInTheDocument();
    expect(screen.getByLabelText("LaTeX code of one circuit")).toHaveValue("");
  });

  it("reads on Ctrl+Enter", async () => {
    const user = await openPage();
    await paste(user, screen.getByLabelText("LaTeX code of one circuit"), exampleById("ghz")!.text);
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(await screen.findByText("Read in full")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Wires (3)" })).toBeInTheDocument();
  });

  it("changes the stored result when what it depends on changes", async () => {
    const user = await openPage();
    await readExample(user, "bell");
    await screen.findByText("Read in full");
    await user.click(screen.getByRole("button", { name: "Simulate" }));
    await screen.findByTestId("state-table");
    await user.type(screen.getByLabelText("Input state"), "1");
    expect(screen.queryByTestId("state-table")).not.toBeInTheDocument();
  });

  it("is in Spanish when the language is", async () => {
    const user = await openPage();
    await user.click(screen.getByRole("button", { name: "Language: English" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Español" }));
    expect(await screen.findByRole("button", { name: "Leer circuito" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Probar un ejemplo"), "bell");
    expect(await screen.findByText("Leído completo")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Simular" }));
    expect(
      await screen.findByText("Estado final (cables: 2). El cable 1 es el bit más significativo."),
    ).toBeInTheDocument();
  });
});

describe("a circuit sent by another screen", () => {
  const BELL = exampleById("bell")!.text;
  const TELEPORT_ID = "c7f460355b1958c3e6cf45b6d3234766cd8c9d49a5a9265864030a0606907a44";

  /** Opens the app at `/ask`, then at the Circuits screen as a link with `state` leaves it. */
  async function arrive(search: string, state: unknown) {
    const app = renderApp(["/ask", { pathname: "/circuits", search, state }]);
    await screen.findByRole("heading", { level: 1, name: "Circuits" });
    return { app, user: userEvent.setup() };
  }

  it("is put in the LaTeX tab and read", async () => {
    const { app } = await arrive("", { latex: BELL });
    expect(screen.getByRole("tab", { name: "LaTeX", selected: true })).toBeInTheDocument();
    expect(screen.getByLabelText("LaTeX code of one circuit")).toHaveValue(BELL);
    expect(await screen.findByText("Read in full")).toBeInTheDocument();
    expect(app.router.state.location.state).toBeNull();
  });

  it("is read once: coming back to the entry does not read it again", async () => {
    const parsed: string[] = [];
    server.events.on("request:start", ({ request }) => {
      if (request.url.endsWith("/circuits/parse")) parsed.push(request.url);
    });
    try {
      const { app, user } = await arrive("", { latex: BELL });
      await screen.findByText("Read in full");
      await user.click(screen.getByRole("button", { name: "Clear" }));
      await act(() => app.router.navigate(-1));
      await screen.findByRole("heading", { level: 1, name: "Ask" });
      await act(() => app.router.navigate(1));
      await screen.findByRole("heading", { level: 1, name: "Circuits" });
      expect(screen.getByLabelText("LaTeX code of one circuit")).toHaveValue("");
      expect(parsed).toHaveLength(1);
    } finally {
      server.events.removeAllListeners();
    }
  });

  it("ignores a state that holds no LaTeX", async () => {
    await arrive("", { latex: 3 });
    expect(screen.getByLabelText("LaTeX code of one circuit")).toHaveValue("");
    expect(screen.queryByText("Read in full")).not.toBeInTheDocument();
  });

  it("opens a paper named by ?doc= when no LaTeX comes with it", async () => {
    await arrive("?doc=c7f460355b1958c3e6cf45b6d3234766cd8c9d49a5a9265864030a0606907a44", null);
    expect(screen.getByRole("tab", { name: "Paper", selected: true })).toBeInTheDocument();
    expect(await screen.findAllByTestId("paper-block")).toHaveLength(2);
  });

  it("says Not found for a ?doc= that is not a document id, without asking the API", async () => {
    const sent: string[] = [];
    server.events.on("request:start", ({ request }) => void sent.push(request.url));
    try {
      await arrive("?doc=not-a-paper", null);
      const problem = await screen.findByTestId("problem");
      expect(within(problem).getByText("Not found")).toBeInTheDocument();
      expect(within(problem).getByText(/No stored paper has this id\./)).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "Paper", selected: true })).toBeInTheDocument();
      expect(sent.filter((url) => url.includes("/documents/"))).toEqual([]);
    } finally {
      server.events.removeAllListeners();
    }
  });

  it("reads the LaTeX rather than the paper when both come", async () => {
    const { app } = await arrive("?doc=ffffffffffffffff", { latex: BELL });
    expect(await screen.findByText("Read in full")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "LaTeX", selected: true })).toBeInTheDocument();
    expect(screen.queryByTestId("problem")).not.toBeInTheDocument();
    expect(app.router.state.location.search).toBe("?doc=ffffffffffffffff");
  });

  /**
   * Starts a reading that the server holds until `release` is called, then leaves the screen: a
   * request of an earlier visit is still running when a link brings the person back.
   */
  async function leaveWithAReadingRunning() {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const parsed: string[] = [];
    server.use(
      http.post("*/api/v1/circuits/parse", async ({ request }) => {
        const { latex } = (await request.clone().json()) as { latex: string };
        parsed.push(latex);
        if (latex !== "slow") return undefined; // the recordings answer
        await held;
        return HttpResponse.json(capturedAnswer("parse unparsed").response);
      }),
    );
    const app = renderApp("/circuits");
    await screen.findByRole("heading", { level: 1, name: "Circuits" });
    const user = userEvent.setup();
    await paste(user, screen.getByLabelText("LaTeX code of one circuit"), "slow");
    await user.click(screen.getByRole("button", { name: "Read circuit" }));
    await waitFor(() => expect(useCircuitStore.getState().busy).toBe("read"));
    await act(() => app.router.navigate("/ask"));
    await screen.findByRole("heading", { level: 1, name: "Ask" });
    return { app, release, parsed };
  }

  it("waits for a request of an earlier visit, then reads the LaTeX it was sent", async () => {
    const { app, release, parsed } = await leaveWithAReadingRunning();
    await act(() => app.router.navigate("/circuits", { state: { latex: BELL } }));
    await screen.findByRole("heading", { level: 1, name: "Circuits" });
    expect(parsed).toEqual(["slow"]);
    release();
    expect(await screen.findByText("Read in full")).toBeInTheDocument();
    expect(screen.getByLabelText("LaTeX code of one circuit")).toHaveValue(BELL);
    expect(parsed).toEqual(["slow", BELL]);
    expect(app.router.state.location.state).toBeNull();
  });

  it("waits for a request of an earlier visit, then lists the paper of ?doc=", async () => {
    const { app, release } = await leaveWithAReadingRunning();
    await act(() => app.router.navigate(`/circuits?doc=${TELEPORT_ID}`));
    await screen.findByRole("heading", { level: 1, name: "Circuits" });
    expect(screen.queryByTestId("paper-block")).not.toBeInTheDocument();
    release();
    expect(await screen.findAllByTestId("paper-block")).toHaveLength(2);
    expect(screen.getByRole("tab", { name: "Paper", selected: true })).toBeInTheDocument();
  });
});
