import { closeDisplayMathOnItsOwnLine as fix } from "./displayMath";

describe("display math that does not close on a line of its own", () => {
  it("moves the text after a closing $$ to its own paragraph", () => {
    expect(fix("$$\nx + y\n$$ where y is a number.\n\nNext.")).toBe(
      "$$\nx + y\n$$\n\nwhere y is a number.\n\nNext.",
    );
  });

  it("puts the closing $$ after the last line of the formula on a line of its own", () => {
    expect(fix("$$\nx\n\\end{equation}$$ where y\nmore")).toBe(
      "$$\nx\n\\end{equation}\n$$\n\nwhere y\nmore",
    );
    expect(fix("$$\nx\n\\end{equation}$$")).toBe("$$\nx\n\\end{equation}\n$$");
  });

  it("moves the first line of the formula off the opening $$", () => {
    expect(fix("$$\\begin{equation}\nx\n\\end{equation}$$ where y")).toBe(
      "$$\n\\begin{equation}\nx\n\\end{equation}\n$$\n\nwhere y",
    );
    expect(fix("$$x\ny$$\n\nAfter.")).toBe("$$\nx\ny\n$$\n\nAfter.");
  });

  it("keeps the indentation of an opening $$ on the lines it makes", () => {
    expect(fix("- item\n\n  $$\\begin{equation}\n  x\n  \\end{equation}\n  $$")).toBe(
      "- item\n\n  $$\n  \\begin{equation}\n  x\n  \\end{equation}\n  $$",
    );
  });

  it("keeps the indentation of the opener on a $$ that closes in the middle of a line", () => {
    expect(fix("- item\n\n  $$\\begin{equation}\n  x\n  \\end{equation}$$ after")).toBe(
      "- item\n\n  $$\n  \\begin{equation}\n  x\n  \\end{equation}\n  $$\n\n  after",
    );
  });

  it("keeps the indentation inside a nested list", () => {
    expect(fix("- a\n - b\n\n   $$\\begin{equation}\n   x\n   \\end{equation}$$ after\n - c")).toBe(
      "- a\n - b\n\n   $$\n   \\begin{equation}\n   x\n   \\end{equation}\n   $$\n\n   after\n - c",
    );
  });

  it("keeps a block at column 0 without indentation", () => {
    expect(fix("$$\nx\n\\end{equation}$$ after")).toBe("$$\nx\n\\end{equation}\n$$\n\nafter");
  });

  it("indents the opener of a following block like the first one", () => {
    expect(fix("  $$\na\nb$$ $$\nc\n$$")).toBe("  $$\na\nb\n  $$\n\n  $$\nc\n$$");
  });

  it("opens the next block when the text after a closing $$ is another opening $$", () => {
    expect(
      fix("$$\\begin{align}\na\n\\end{align}$$ $$\\begin{align}\nb\n\\end{align}$$\n\nEnd."),
    ).toBe(
      "$$\n\\begin{align}\na\n\\end{align}\n$$\n\n$$\n\\begin{align}\nb\n\\end{align}\n$$\n\nEnd.",
    );
  });

  it("leaves a block that closes on a line of its own as it was", () => {
    const text = "Before.\n\n$$\nx\n$$\nwhere y\n\n$$ \n  z\n  $$  \n\nAfter $$ not a block.";
    expect(fix(text)).toBe(text);
  });

  it("leaves inline $$...$$ and single-dollar math alone", () => {
    for (const text of [
      "$$ x $$ where y.",
      "A line with $$x$$ in the middle $$y$$ and $z$.",
      "Costs $$5 and more",
      "$x$ then $$ y $$",
    ]) {
      expect(fix(text), text).toBe(text);
    }
  });

  it("leaves fenced code and an unclosed block alone", () => {
    const code = "```\n$$\nx\ny$$ z\n```";
    expect(fix(code)).toBe(code);
    expect(fix("text\n\n$$\nx\ny")).toBe("text\n\n$$\nx\ny");
  });

  it("does not stop a block for a single dollar inside it", () => {
    const text = "$$\n\\text{cost $5$ each}\n$$\n\nAfter.";
    expect(fix(text)).toBe(text);
  });

  it("returns text without $$ as it was", () => {
    expect(fix("plain $x$ text\n\nmore")).toBe("plain $x$ text\n\nmore");
  });
});
