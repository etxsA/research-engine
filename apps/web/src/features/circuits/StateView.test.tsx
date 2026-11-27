import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "@/shared/i18n";
import { MATRIX_PREVIEW, ProbabilityBars, StateTable, UnitaryMatrix } from "./StateView";
import type { Complex, SimulatedPart } from "./types";

function part(wires: number, overrides: Partial<SimulatedPart> = {}): SimulatedPart {
  return {
    part: 1,
    wires,
    measuredWires: [],
    state: null,
    probabilities: null,
    unitary: null,
    sign: null,
    operations: null,
    parameters: [],
    ...overrides,
  };
}

/** A state spread over every basis state, so no row is hidden as zero. */
function uniform(wires: number): Complex[] {
  const size = 2 ** wires;
  return Array.from({ length: size }, () => [1 / Math.sqrt(size), 0] as const);
}

describe("StateTable", () => {
  it("says so when the state is not listed", () => {
    render(<StateTable part={part(2)} />);
    expect(
      screen.getByText("The state is not listed for a circuit of this size."),
    ).toBeInTheDocument();
  });

  it("shows the first rows and the rest on demand", async () => {
    const user = userEvent.setup();
    render(<StateTable part={part(8, { state: uniform(8) })} />);
    const table = screen.getByTestId("state-table");
    expect(within(table).getAllByRole("row")).toHaveLength(65);
    expect(screen.getByText("Showing 64 of 256")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show more" }));
    expect(within(table).getAllByRole("row")).toHaveLength(257);
    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
  });

  it("labels each row with its basis state, first wire first", () => {
    render(
      <StateTable
        part={part(3, {
          state: [
            [0, 0],
            [0, 0],
            [0, 0],
            [0, 0],
            [0, 0],
            [0, 0],
            [0, 1],
            [0, 0],
          ],
        })}
      />,
    );
    expect(within(screen.getByTestId("state-table")).getByText("|110⟩")).toBeInTheDocument();
    expect(screen.getByText("i")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });
});

describe("zero rows", () => {
  const oneZero: Complex[] = [
    [1, 0],
    [0, 0],
  ];

  it("names a single hidden row in the singular", () => {
    render(<StateTable part={part(1, { state: oneZero })} />);
    expect(screen.getByRole("button", { name: "Show the zero row" })).toBeInTheDocument();
  });

  it("agrees with the number in Spanish", async () => {
    await i18n.changeLanguage("es");
    try {
      const { unmount } = render(<StateTable part={part(1, { state: oneZero })} />);
      expect(screen.getByRole("button", { name: "Mostrar la fila en cero" })).toBeInTheDocument();
      unmount();
      render(
        <StateTable
          part={part(2, {
            state: [
              [1, 0],
              [0, 0],
              [0, 0],
              [0, 0],
            ],
          })}
        />,
      );
      expect(
        screen.getByRole("button", { name: "Mostrar las 3 filas en cero" }),
      ).toBeInTheDocument();
    } finally {
      await i18n.changeLanguage("en");
    }
  });
});

describe("ProbabilityBars", () => {
  it("shows nothing for a result without probabilities", () => {
    const { container } = render(<ProbabilityBars part={part(2)} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the wires the outcomes belong to and labels outcomes with that many bits", () => {
    render(
      <ProbabilityBars
        part={part(3, { probabilities: { wires: [1, 3], values: [0.25, 0.25, 0.25, 0.25] } })}
      />,
    );
    expect(screen.getByText("Outcome probabilities of wires 1, 3")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("probabilities")).getAllByRole("listitem")[3],
    ).toHaveTextContent("|11⟩25%");
  });

  it("hides the zero outcomes unless asked, and shows more of a long list", async () => {
    const user = userEvent.setup();
    const values = Array.from({ length: 1024 }, (_, index) => (index < 100 ? 0.01 : 0));
    render(
      <ProbabilityBars
        part={part(10, {
          probabilities: { wires: Array.from({ length: 10 }, (_, i) => i + 1), values },
        })}
      />,
    );
    const list = screen.getByTestId("probabilities");
    expect(within(list).getAllByRole("listitem")).toHaveLength(64);
    await user.click(screen.getByRole("button", { name: "Show more" }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(100);
    await user.click(screen.getByRole("button", { name: "Show the 924 zero rows" }));
    // the limit already grown to 320 rows applies to the longer list
    expect(within(list).getAllByRole("listitem")).toHaveLength(320);
    await user.click(screen.getByRole("button", { name: "Hide the zero rows" }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(100);
  });
});

describe("UnitaryMatrix", () => {
  const identity = (size: number): Complex[][] =>
    Array.from({ length: size }, (_, row) =>
      Array.from({ length: size }, (_, column) => [row === column ? 1 : 0, 0] as const),
    );

  it("shows nothing when there is no unitary", () => {
    const { container } = render(<UnitaryMatrix part={part(2)} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a small matrix whole", () => {
    render(<UnitaryMatrix part={part(2, { unitary: identity(4) })} />);
    const table = screen.getByTestId("unitary-table");
    expect(within(table).getAllByRole("row")).toHaveLength(5);
    expect(within(table).getByRole("rowheader", { name: "|10⟩" })).toBeInTheDocument();
    expect(screen.queryByText(/Showing the top left/)).not.toBeInTheDocument();
  });

  it("keeps the download alive until the browser has taken it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const revoked: string[] = [];
    URL.createObjectURL = () => "blob:late";
    URL.revokeObjectURL = (url: string) => void revoked.push(url);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    try {
      const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
      render(<UnitaryMatrix part={part(2, { unitary: identity(4) })} />);
      await user.click(screen.getByRole("button", { name: "Download the unitary (JSON)" }));
      expect(revoked).toEqual([]);
      vi.advanceTimersByTime(10_000);
      expect(revoked).toEqual(["blob:late"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows only a corner of a large one and offers the whole as a download", async () => {
    const user = userEvent.setup();
    const created: Blob[] = [];
    URL.createObjectURL = (blob: Blob | MediaSource) => {
      created.push(blob as Blob);
      return "blob:test";
    };
    URL.revokeObjectURL = () => undefined;
    const names: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      names.push(this.download);
    });
    render(<UnitaryMatrix part={part(6, { unitary: identity(64) })} />);
    const table = screen.getByTestId("unitary-table");
    expect(within(table).getAllByRole("row")).toHaveLength(MATRIX_PREVIEW + 1);
    expect(screen.getByText("Showing the top left 16 by 16 of 64 by 64.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Download the unitary (JSON)" }));
    expect(click).toHaveBeenCalledOnce();
    expect(names).toEqual(["unitary.json"]);
    expect(created[0]!.type).toBe("application/json");
    const body = JSON.parse(await created[0]!.text()) as unknown[][];
    expect(body).toHaveLength(64);
  });
});
