import { copyText, selectContents } from "./clipboard";

const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");

function stubClipboard(value: Partial<Clipboard> | undefined) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true });
}

function codeBlock(text: string): HTMLElement {
  const pre = document.createElement("pre");
  pre.tabIndex = 0;
  pre.textContent = text;
  document.body.append(pre);
  return pre;
}

afterEach(() => {
  if (original) Object.defineProperty(navigator, "clipboard", original);
  else Reflect.deleteProperty(navigator, "clipboard");
  window.getSelection()?.removeAllRanges();
  document.body.replaceChildren();
});

describe("copyText", () => {
  it("writes the text to the clipboard and selects nothing", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    stubClipboard({ writeText });
    const pre = codeBlock("h q[0];\n");
    await expect(copyText("h q[0];\n", pre)).resolves.toBe("copied");
    expect(writeText).toHaveBeenCalledWith("h q[0];\n");
    expect(window.getSelection()?.toString()).toBe("");
  });

  it("selects the text and focuses its block when the browser refuses", async () => {
    stubClipboard({
      writeText: () => Promise.reject(new DOMException("denied", "NotAllowedError")),
    });
    const pre = codeBlock("h q[0];\ncx q[0], q[1];\n");
    await expect(copyText("h q[0];\ncx q[0], q[1];\n", pre)).resolves.toBe("selected");
    expect(window.getSelection()?.toString()).toBe("h q[0];\ncx q[0], q[1];\n");
    expect(pre).toHaveFocus();
  });

  it("selects the text when the page has no clipboard at all (no secure context)", async () => {
    stubClipboard(undefined);
    const pre = codeBlock("x q[0];\n");
    await expect(copyText("x q[0];\n", pre)).resolves.toBe("selected");
    expect(window.getSelection()?.toString()).toBe("x q[0];\n");
  });

  it("says it failed when the browser refuses and there is nothing to select", async () => {
    stubClipboard({ writeText: () => Promise.reject(new Error("denied")) });
    await expect(copyText("x q[0];\n")).resolves.toBe("failed");
    await expect(copyText("x q[0];\n", null)).resolves.toBe("failed");
  });
});

describe("selectContents", () => {
  it("replaces whatever was selected before", () => {
    const first = codeBlock("first");
    const second = codeBlock("second");
    selectContents(first);
    selectContents(second);
    expect(window.getSelection()?.toString()).toBe("second");
  });
});
