import { downloadText, REVOKE_AFTER_MS } from "./download";

describe("downloadText", () => {
  let created: Blob[];
  let revoked: string[];
  let clicked: { name: string; href: string; attached: boolean }[];

  beforeEach(() => {
    created = [];
    revoked = [];
    clicked = [];
    URL.createObjectURL = (blob: Blob | MediaSource) => {
      created.push(blob as Blob);
      return `blob:file-${created.length}`;
    };
    URL.revokeObjectURL = (url: string) => void revoked.push(url);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push({ name: this.download, href: this.href, attached: this.isConnected });
    });
  });

  it("saves the text under its name with its type and leaves no link in the page", async () => {
    downloadText("circuit-p01.qasm", "OPENQASM 3.0;\n", "text/plain;charset=utf-8");
    expect(clicked).toEqual([{ name: "circuit-p01.qasm", href: "blob:file-1", attached: true }]);
    expect(created[0]!.type).toBe("text/plain;charset=utf-8");
    expect(await created[0]!.text()).toBe("OPENQASM 3.0;\n");
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("keeps the URL alive until the browser has taken the file, then revokes it", () => {
    vi.useFakeTimers();
    try {
      downloadText("a.json", "[]", "application/json");
      vi.advanceTimersByTime(REVOKE_AFTER_MS - 1);
      expect(revoked).toEqual([]);
      vi.advanceTimersByTime(1);
      expect(revoked).toEqual(["blob:file-1"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the bytes of text that is not ASCII", async () => {
    downloadText("x.qasm", "// source: Schrödinger, block 1\n", "text/plain;charset=utf-8");
    const bytes = Array.from(new Uint8Array(await created[0]!.arrayBuffer()));
    expect(bytes).toEqual(
      Array.from(new TextEncoder().encode("// source: Schrödinger, block 1\n")),
    );
  });
});
