import { act, render, screen } from "@testing-library/react";
import { isWebUrl, MarkdownMath } from "./MarkdownMath";

describe("which addresses become links", () => {
  it("accepts web addresses only", () => {
    expect(isWebUrl("https://example.org/a?b=c")).toBe(true);
    expect(isWebUrl("http://example.org")).toBe(true);
    for (const href of [
      undefined,
      "",
      "javascript:alert(1)",
      "data:text/html,x",
      "mailto:a@b.c",
      "/relative/path",
      "//example.org/x",
      "figure.png",
      "#cite-1",
    ]) {
      expect(isWebUrl(href), String(href)).toBe(false);
    }
  });
});

describe("a formula wider than its line", () => {
  /** The width each formula needs, by its TeX source; the line they sit in is 300 wide. */
  const needs: Record<string, number> = {};
  const observers = new Set<() => void>();
  const remeasure = () => act(() => observers.forEach((callback) => callback()));

  beforeEach(() => {
    class FakeResizeObserver {
      private readonly callback: () => void;
      constructor(callback: () => void) {
        this.callback = callback;
      }
      observe() {
        observers.add(this.callback);
      }
      disconnect() {
        observers.delete(this.callback);
      }
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      const source = this.classList.contains("katex")
        ? this.querySelector("annotation")?.textContent
        : undefined;
      // The paragraph itself is as wide as the widest thing in it, which is what once misled.
      if (this.tagName === "P") return Math.max(300, ...Object.values(needs));
      return source === undefined ? 0 : (needs[source] ?? 0);
    });
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.tagName === "P" ? 300 : 0;
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    observers.clear();
    for (const key of Object.keys(needs)) delete needs[key];
  });

  const formula = (container: HTMLElement, source: string) =>
    Array.from(container.querySelectorAll(".katex")).find(
      (node) => node.querySelector("annotation")?.textContent === source,
    );

  it("scrolls inside its own box and takes the keyboard focus", () => {
    needs["x^2 + y^2"] = 500;
    const { container } = render(<MarkdownMath>{"A line with $x^2 + y^2$ in it."}</MarkdownMath>);
    const box = container.querySelector(".katex");
    expect(box).toHaveClass("math-scroll");
    expect(box).toHaveAttribute("tabindex", "0");
  });

  it("stays a plain part of the line when it fits, and follows the line as it changes", () => {
    needs["x^2"] = 300;
    const { container } = render(<MarkdownMath>{"A line with $x^2$ in it."}</MarkdownMath>);
    const box = () => container.querySelector(".katex");
    expect(box()).not.toHaveClass("math-scroll");
    expect(box()).not.toHaveAttribute("tabindex");
    needs["x^2"] = 420;
    remeasure();
    expect(box()).toHaveClass("math-scroll");
    expect(box()).toHaveAttribute("tabindex", "0");
    needs["x^2"] = 280;
    remeasure();
    expect(box()).not.toHaveClass("math-scroll");
    expect(box()).not.toHaveAttribute("tabindex");
  });

  it("marks only the formula that is wider than the line, not its neighbours", () => {
    needs["x^2"] = 40;
    needs["\\alpha_1\\hat{x}_1"] = 700;
    needs.y = 12;
    const { container } = render(
      <MarkdownMath>{"Short $x^2$, long $\\alpha_1\\hat{x}_1$, short $y$."}</MarkdownMath>,
    );
    expect(container.querySelectorAll(".katex")).toHaveLength(3);
    expect(formula(container, "x^2")).not.toHaveClass("math-scroll");
    expect(formula(container, "\\alpha_1\\hat{x}_1")).toHaveClass("math-scroll");
    expect(formula(container, "y")).not.toHaveClass("math-scroll");
    expect(container.querySelectorAll("[tabindex]")).toHaveLength(1);
  });

  it("does not touch display formulas or other spans", () => {
    needs["x^2"] = 500;
    const { container } = render(<MarkdownMath>{"$$\nx^2\n$$"}</MarkdownMath>);
    expect(container.querySelector(".math-scroll")).toBeNull();
    expect(container.querySelector(".katex-display")).not.toBeNull();
  });
});

describe("Markdown with math", () => {
  it("renders formulas as HTML with MathML for a screen reader", () => {
    const { container } = render(
      <MarkdownMath>{"Energy $E = mc^2$ and\n\n$$\n\\int_0^1 x\\,dx\n$$"}</MarkdownMath>,
    );
    expect(container.querySelectorAll(".katex").length).toBe(2);
    expect(container.querySelector(".katex-display")).not.toBeNull();
    expect(container.querySelector("math")).not.toBeNull();
    expect(container.querySelector(".katex-html")).toHaveAttribute("aria-hidden", "true");
  });

  it("puts no syntax node on the elements it renders", () => {
    const { container } = render(
      <MarkdownMath>
        {"Energy $E = mc^2$, a **bold** [link](https://example.org) and\n\n$$\nx\n$$"}
      </MarkdownMath>,
    );
    expect(container.querySelectorAll(".katex span").length).toBeGreaterThan(10);
    expect(container.querySelectorAll("[node]")).toHaveLength(0);
    expect(container.innerHTML).not.toContain("[object Object]");
  });

  it("shows a formula it cannot read as typed, without failing", () => {
    const { container } = render(<MarkdownMath>{"Broken $\\frac{1}{$ here"}</MarkdownMath>);
    expect(container.textContent).toContain("here");
    expect(container.querySelector(".katex-error")).not.toBeNull();
  });

  it("does not follow a formula that asks for a link", () => {
    const { container } = render(
      <MarkdownMath>{"$\\href{https://evil.example}{click}$"}</MarkdownMath>,
    );
    expect(container.querySelector("a")).toBeNull();
  });

  it("drops \\label{...} from a formula instead of showing it as an error", () => {
    const { container } = render(
      <MarkdownMath>
        {
          "$$x \\label{eq:1}$$ and\n\n$$\n\\begin{equation}\ny^2 \\label{eq:a_b}\n\\end{equation}\n$$"
        }
      </MarkdownMath>,
    );
    expect(container.querySelectorAll(".katex")).toHaveLength(2);
    expect(container.querySelector(".katex-error")).toBeNull();
    const shown = Array.from(container.querySelectorAll(".katex-html"), (n) => n.textContent);
    expect(shown.join(" ")).not.toMatch(/eq|label/);
    expect(shown[0]).toBe("x");
    expect(shown[1]).toContain("y2");
  });

  it("keeps a macro a formula defines to that formula", () => {
    const { container } = render(
      <MarkdownMath>
        {"$\\gdef\\alpha{BETA}\\alpha$ then $\\alpha$ and $x \\label{a}$"}
      </MarkdownMath>,
    );
    const shown = Array.from(container.querySelectorAll(".katex-html"), (n) => n.textContent);
    expect(shown[0]).toContain("BETA");
    expect(shown[1]).not.toContain("BETA");
    expect(shown[2]).toBe("x");
  });

  it("caps the size a formula can ask for", () => {
    const { container } = render(
      <MarkdownMath>{"$\\rule{1000em}{1000em}$ and $$\\rule{900em}{900em}$$"}</MarkdownMath>,
    );
    expect(container.querySelector(".katex")).not.toBeNull();
    const rules = Array.from(container.querySelectorAll<HTMLElement>(".katex-rule"));
    expect(rules).toHaveLength(2);
    for (const rule of rules) {
      expect(rule.style.borderRightWidth).toBe("10em");
      expect(rule.style.borderTopWidth).toBe("10em");
    }
  });

  it("shows raw HTML as the text it was written as and renders none of it", () => {
    const { container } = render(
      <MarkdownMath>
        {'Before\n\n<script>window.__x = 1</script>\n\n<b onclick="x()">bold</b>\n\nAfter'}
      </MarkdownMath>,
    );
    expect(container.querySelector("script, b")).toBeNull();
    expect(container.querySelector("[onclick]")).toBeNull();
    expect(container.textContent).toContain("Before");
    expect(container.textContent).toContain("After");
    expect(container.textContent).toContain("<script>window.__x = 1</script>");
  });

  it("keeps a comparison in a paper's text instead of reading it as a tag", () => {
    const { container } = render(<MarkdownMath>{"When a <b and c> d holds, a<b."}</MarkdownMath>);
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("When a <b and c> d holds, a<b.");
  });

  it("links web addresses and nothing else", () => {
    render(
      <MarkdownMath>
        {"[web](https://example.org) [js](javascript:alert(1)) [rel](/papers/1) [file](x.pdf)"}
      </MarkdownMath>,
    );
    const link = screen.getByRole("link", { name: "web" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText("js")).toBeInTheDocument();
    expect(screen.getByText("rel")).toBeInTheDocument();
  });

  it("loads no image and shows its description instead", () => {
    const { container } = render(
      <MarkdownMath>
        {"![Figure 1: levels](figure1.png) and ![](https://example.org/p.png)"}
      </MarkdownMath>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("[Figure 1: levels]")).toBeInTheDocument();
  });

  it("starts the headings of a paper below the heading of the page", () => {
    const { container } = render(
      <MarkdownMath>
        {"# One\n\n## Two\n\n### Three\n\n#### Four\n\n##### Five\n\n###### Six"}
      </MarkdownMath>,
    );
    expect([...container.querySelectorAll("h1, h2, h3, h4, h5, h6")].map((h) => h.tagName)).toEqual(
      ["H3", "H4", "H5", "H6", "H6", "H6"],
    );
  });

  it("lets a caller replace an element", () => {
    render(
      <MarkdownMath components={{ em: ({ children }) => <mark>{children}</mark> }}>
        {"*marked*"}
      </MarkdownMath>,
    );
    expect(screen.getByText("marked").tagName).toBe("MARK");
  });

  it("keeps lists, code and quotes", () => {
    const { container } = render(
      <MarkdownMath>{"- a\n- b\n\n1. x\n\n> quote\n\n```\ncode\n```"}</MarkdownMath>,
    );
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelector("ol li")).not.toBeNull();
    expect(container.querySelector("blockquote")).not.toBeNull();
    expect(container.querySelector("pre code")).toHaveTextContent("code");
  });
});

describe("display math closed in the middle of a line", () => {
  it("ends the formula there and shows the text after it as text", () => {
    const { container } = render(
      <MarkdownMath>
        {"$$\\begin{equation}\nx^2\n\\end{equation}$$ where $x$ is a number.\n\nNext paragraph."}
      </MarkdownMath>,
    );
    const display = container.querySelectorAll(".katex-display");
    expect(display).toHaveLength(1);
    expect(display[0]).not.toHaveTextContent("Next paragraph");
    expect(screen.getByText(/where/)).toBeInTheDocument();
    expect(screen.getByText("Next paragraph.")).toBeInTheDocument();
    expect(container.querySelectorAll(".katex-error")).toHaveLength(0);
  });
});

describe("pipe tables", () => {
  const table =
    "| Gate | Qubits | Note |\n| :--- | ---: | --- |\n| H | 1 | one qubit |\n| CNOT | 2 | two qubits |";

  it("renders a pipe table as a table, with a header row and aligned columns", () => {
    const { container } = render(<MarkdownMath>{table}</MarkdownMath>);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Gate",
      "Qubits",
      "Note",
    ]);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(screen.getByRole("cell", { name: "CNOT" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "2" })).toHaveStyle({ textAlign: "right" });
    expect(container.textContent).not.toContain("| H |");
  });

  it("renders math inside a cell, in the header and the body", () => {
    const { container } = render(
      <MarkdownMath>
        {"| State $\\vert\\psi\\rangle$ | Value |\n| --- | --- |\n| $x^2$ | $\\alpha_1$ |"}
      </MarkdownMath>,
    );
    const annotations = (selector: string) =>
      Array.from(container.querySelectorAll(`${selector} annotation`)).map((n) => n.textContent);
    expect(annotations("th")).toEqual(["\\vert\\psi\\rangle"]);
    expect(annotations("td")).toEqual(["x^2", "\\alpha_1"]);
    expect(container.textContent).not.toContain("$x^2$");
  });

  it("keeps a bar inside a formula of a cell from cutting the row", () => {
    const { container } = render(
      <MarkdownMath>
        {"| State | Norm |\n| --- | --- |\n| $|\\psi\\rangle$ | $||x||$ |\n| a | $|S|$ |"}
      </MarkdownMath>,
    );
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    for (const row of container.querySelectorAll("tr")) {
      expect(row.querySelectorAll("th, td")).toHaveLength(2);
    }
    expect(container.querySelectorAll("td .katex")).toHaveLength(3);
  });

  it("shows a footnote as the text it was, with no Footnotes section", () => {
    const { container } = render(
      <MarkdownMath>
        {"A claim[^a] here.\n\n[^a]: The note.\n\n[^b]: A note whose reference is on another page."}
      </MarkdownMath>,
    );
    expect(container.querySelector("section, sup, h2, h3, h4")).toBeNull();
    expect(container.textContent).toContain("A claim[^a] here.");
    expect(container.textContent).toContain("[^a]: The note.");
    expect(container.textContent).toContain("[^b]: A note whose reference is on another page.");
    expect(container.textContent).not.toContain("Footnotes");
  });

  it("does not read a single tilde as strikethrough, in text, in a cell or in a formula", () => {
    const { container } = render(
      <MarkdownMath>
        {
          "Between ~5 and 10~ qubits, or ~10 gates.\n\n| a | b |\n| --- | --- |\n| ~1 to 5~ | 2 |\n\nThen $x \\sim y$ and $a~b$."
        }
      </MarkdownMath>,
    );
    expect(container.querySelector("del, s")).toBeNull();
    expect(container.textContent).toContain("Between ~5 and 10~ qubits, or ~10 gates.");
    expect(screen.getByRole("cell", { name: "~1 to 5~" })).toBeInTheDocument();
  });

  it("still strikes text through with a double tilde", () => {
    const { container } = render(<MarkdownMath>{"This is ~~wrong~~ text."}</MarkdownMath>);
    expect(container.querySelector("del")).toHaveTextContent("wrong");
  });

  it("puts the table in a box of its own that scrolls sideways", () => {
    const { container } = render(<MarkdownMath>{table}</MarkdownMath>);
    const box = container.querySelector(".table-scroll");
    expect(box).not.toBeNull();
    expect(box?.querySelector(":scope > table")).not.toBeNull();
    expect(container.querySelectorAll("table")).toHaveLength(1);
  });

  it("gives the box the keyboard focus only while the table is wider than it", () => {
    const observers = new Set<() => void>();
    let wide = true;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        private readonly callback: () => void;
        constructor(callback: () => void) {
          this.callback = callback;
        }
        observe() {
          observers.add(this.callback);
        }
        disconnect() {
          observers.delete(this.callback);
        }
      },
    );
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.classList.contains("table-scroll") ? (wide ? 900 : 300) : 0;
    });
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.classList.contains("table-scroll") ? 300 : 0;
    });
    try {
      const { container } = render(<MarkdownMath>{table}</MarkdownMath>);
      const box = () => container.querySelector(".table-scroll");
      expect(box()).toHaveAttribute("tabindex", "0");
      wide = false;
      act(() => observers.forEach((callback) => callback()));
      expect(box()).not.toHaveAttribute("tabindex");
    } finally {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    }
  });

  it("adds no raw HTML path: tags in a cell stay text and links stay safe", () => {
    const { container } = render(
      <MarkdownMath>
        {
          '| a | b |\n| --- | --- |\n| <b onclick="x()">hi</b> | [js](javascript:alert(1)) [ok](https://example.org) |\n\nSee www.example.org and <script>1</script>.'
        }
      </MarkdownMath>,
    );
    expect(container.querySelector("b, script, [onclick]")).toBeNull();
    expect(container.textContent).toContain('<b onclick="x()">hi</b>');
    expect(container.querySelector("input, img")).toBeNull();
    for (const link of container.querySelectorAll("a")) {
      expect(link.getAttribute("href")).toMatch(/^https?:\/\//);
    }
  });
});

describe("an action under each latex block", () => {
  const CIRCUIT = "\\begin{quantikz}\n& \\gate{H} & \\qw\n\\end{quantikz}";
  const action = (source: string) => (
    <button type="button" data-source={source}>
      Use it
    </button>
  );

  it("is shown under a fenced latex block, with the block's source as written", () => {
    const { container } = render(
      <MarkdownMath latexBlockAction={action}>
        {`Before.\n\n\`\`\`latex\n${CIRCUIT}\n\`\`\`\n\nAfter.`}
      </MarkdownMath>,
    );
    const buttons = screen.getAllByRole("button", { name: "Use it" });
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAttribute("data-source", CIRCUIT);
    // the block itself is still there, above its action
    const pre = container.querySelector("pre")!;
    expect(pre.textContent).toBe(`${CIRCUIT}\n`);
    expect(
      pre.compareDocumentPosition(buttons[0]!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(container.querySelector("[node]")).toBeNull();
  });

  it("is shown once per latex block", () => {
    render(
      <MarkdownMath latexBlockAction={action}>
        {`\`\`\`latex\n${CIRCUIT}\n\`\`\`\n\ntext\n\n\`\`\`latex\nsecond\n\`\`\``}
      </MarkdownMath>,
    );
    expect(screen.getAllByRole("button", { name: "Use it" }).map((b) => b.dataset.source)).toEqual([
      CIRCUIT,
      "second",
    ]);
  });

  it("is not shown for code in another language, code with no language, or inline code", () => {
    const { container } = render(
      <MarkdownMath latexBlockAction={action}>
        {[
          "```python\nprint(1)\n```",
          "```tex\n\\begin{quantikz}\\end{quantikz}\n```",
          "```\nplain\n```",
          "    indented code",
          "Inline `\\begin{quantikz}` and `latex` code.",
        ].join("\n\n")}
      </MarkdownMath>,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(container.querySelectorAll("pre")).toHaveLength(4);
  });

  it("is not shown when no action is given", () => {
    const { container } = render(<MarkdownMath>{`\`\`\`latex\n${CIRCUIT}\n\`\`\``}</MarkdownMath>);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(container.querySelector("pre")?.textContent).toBe(`${CIRCUIT}\n`);
  });
});

describe("the action under a latex block", () => {
  const text = [
    "```latex\n\\gate{H}\n```",
    "```python\nprint(1)\n```",
    "`\\gate{X}`",
    "```latex\n\\gate{Z}\n```",
  ].join("\n\n");

  it("gets the source and the number of the block among the latex blocks", () => {
    const seen: [string, number][] = [];
    render(
      <MarkdownMath
        latexBlockAction={(source, block) => {
          seen.push([source, block]);
          return <span>{`action ${block}`}</span>;
        }}
      >
        {text}
      </MarkdownMath>,
    );
    expect(seen).toEqual([
      ["\\gate{H}", 1],
      ["\\gate{Z}", 2],
    ]);
    expect(screen.getByText("action 1")).toBeInTheDocument();
    expect(screen.getByText("action 2")).toBeInTheDocument();
  });

  it("leaves no trace of the numbering in the page", () => {
    const { container } = render(
      <MarkdownMath latexBlockAction={() => <span>action</span>}>{text}</MarkdownMath>,
    );
    expect(container.querySelector("[data-latex-block]")).toBeNull();
    render(<MarkdownMath>{text}</MarkdownMath>);
    expect(document.querySelector("[data-latex-block]")).toBeNull();
  });
});
