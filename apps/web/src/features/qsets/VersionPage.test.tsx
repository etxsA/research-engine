import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedBody, meAs, pageAnswer, problemBody } from "@/test/sets-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";

preloadScreens(() => import("./VersionPage"));

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

// Every wait in this file is for a request plus a render of up to 50 rows. On a busy machine that
// takes longer than the 1 s a `findBy` or `waitFor` allows by default, so the waits of this file
// (not of the others) may take 10 s, and a test 20 s, before they fail. What each one expects is
// unchanged.
configure({ asyncUtilTimeout: 10_000 });
vi.setConfig({ testTimeout: 20_000 });

async function openVersion(path: string) {
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByRole("table", { name: /Items \d+ to \d+ of \d+/ });
  return { user, app };
}

/**
 * Waits for a page of the items table by the text of its caption, then asserts the table by role
 * and name as before. Polling `findByRole` on a table of 50 rows walks the accessibility tree on
 * every DOM change, which on a busy machine takes longer than the page itself does to arrive.
 */
async function findItemsTable(name: string): Promise<HTMLElement> {
  await screen.findByText(name, { selector: "caption" });
  return screen.getByRole("table", { name });
}

function rowIds(): string[] {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0]!.textContent ?? "");
}

describe("a version (S1)", () => {
  it("shows the header, the md5, the review progress and the items", async () => {
    await openVersion("/sets/demo-papers/v2");
    expect(screen.getByRole("heading", { level: 1, name: "demo-papers v2" })).toBeInTheDocument();
    expect(screen.getByTestId("version-md5")).toHaveTextContent("08ff57f82af04a41374b1f145e868a6b");
    expect(screen.getByText("beto")).toBeInTheDocument();
    expect(screen.getByText(/Copy of demo-papers\/v1/)).toBeInTheDocument();
    const progress = screen.getByRole("heading", { name: "Review progress" }).nextElementSibling!;
    expect(within(progress as HTMLElement).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByRole("table", { name: "Items 1 to 7 of 7" })).toBeInTheDocument();
    expect(rowIds()).toEqual([
      "DP-001",
      "DP-001-esmirror of DP-001",
      "DP-002",
      "DP-003",
      "DP-004",
      "DP-005",
      "DP-007",
    ]);
  });

  it("shows the review state of each item as words", async () => {
    await openVersion("/sets/demo-papers/v2");
    const row = (id: string) =>
      screen.getAllByRole("row").find((r) => within(r).queryByRole("link", { name: id }))!;
    expect(within(row("DP-001")).getByText("Accepted by two")).toBeInTheDocument();
    expect(within(row("DP-001")).getByText("Accepted by ana, beto")).toBeInTheDocument();
    expect(within(row("DP-007")).getByText("Needs a second reviewer")).toBeInTheDocument();
    expect(within(row("DP-002")).getByText("Edit proposed")).toBeInTheDocument();
    expect(within(row("DP-004")).getByText("Rejected")).toBeInTheDocument();
    expect(within(row("DP-003")).getByText("Not reviewed")).toBeInTheDocument();
    expect(within(row("DP-001")).getByText("category=notation")).toBeInTheDocument();
  });

  it("reads the filters from the address and sends them", async () => {
    let seen: URLSearchParams | undefined;
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2", ({ request }) => {
        seen = new URL(request.url).searchParams;
        return HttpResponse.json(pageAnswer("demo-papers", "v2", seen));
      }),
    );
    await openVersion("/sets/demo-papers/v2?review=needs_second&lang=en");
    expect(rowIds()).toEqual(["DP-007"]);
    expect(seen?.get("review")).toBe("needs_second");
    expect(seen?.get("lang")).toBe("en");
    expect(screen.getByLabelText("Review")).toHaveValue("needs_second");
    expect(screen.getByLabelText("Language")).toHaveValue("en");
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("keeps a filter chosen in a list in the address", async () => {
    const { user, app } = await openVersion("/sets/demo-papers/v2");
    await user.selectOptions(screen.getByLabelText("Form"), "single");
    await waitFor(() =>
      expect(rowIds()).toEqual(["DP-001", "DP-001-esmirror of DP-001", "DP-004", "DP-007"]),
    );
    expect(app.router.state.location.search).toBe("?form=single");
    await user.selectOptions(screen.getByLabelText("Split"), "dev");
    await waitFor(() =>
      expect(rowIds()).toEqual(["DP-001", "DP-001-esmirror of DP-001", "DP-004"]),
    );
    expect(app.router.state.location.search).toBe("?form=single&split=dev");
  });

  it("searches the text and the tag when the search is sent", async () => {
    const { user, app } = await openVersion("/sets/demo-papers/v2");
    await user.type(screen.getByLabelText("Text"), "Beta");
    await user.type(screen.getByLabelText("Tag"), "category=chain");
    expect(app.router.state.location.search).toBe("");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(rowIds()).toEqual(["DP-002"]));
    expect(new URLSearchParams(app.router.state.location.search).get("q")).toBe("Beta");
    expect(new URLSearchParams(app.router.state.location.search).get("tag")).toBe("category=chain");
  });

  it("filters by content, language and review state from the lists", async () => {
    const { user, app } = await openVersion("/sets/demo-papers/v2");
    await user.selectOptions(screen.getByLabelText("Content"), "other");
    await waitFor(() => expect(rowIds()).toEqual(["DP-003", "DP-004"]));
    await user.selectOptions(screen.getByLabelText("Language"), "en");
    // The address is read again before the next list is used: two changes within one render would
    // each start from the same address. The router holds the new address a moment before the page
    // has drawn it, and the page builds the next address from what it drew, so wait for the list.
    await waitFor(() => expect(screen.getByLabelText("Language")).toHaveValue("en"));
    expect(new URLSearchParams(app.router.state.location.search).get("lang")).toBe("en");
    await user.selectOptions(screen.getByLabelText("Review"), "rejected");
    await waitFor(() => expect(rowIds()).toEqual(["DP-004"]));
    expect(app.router.state.location.search).toBe("?content=other&lang=en&review=rejected");
  });

  it("clears every filter", async () => {
    const { user, app } = await openVersion("/sets/demo-papers/v2?form=single&q=alpha");
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(rowIds()).toHaveLength(7));
    expect(app.router.state.location.search).toBe("");
    expect(screen.getByLabelText("Text")).toHaveValue("");
  });

  it("says when no item matches", async () => {
    const user = userEvent.setup();
    renderApp("/sets/demo-papers/v2?q=zzzz-nothing");
    expect(await screen.findByText("No item matches these filters.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
    void user;
  });

  it("pages through a long list with the offset in the address", async () => {
    const seen: number[] = [];
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2", ({ request }) => {
        const params = new URL(request.url).searchParams;
        const offset = Number(params.get("offset") ?? 0);
        seen.push(offset);
        const body = capturedBody<Record<string, any>>("page demo-papers/v2");
        const template = body.items[0];
        body.total = 120;
        body.offset = offset;
        body.limit = 50;
        body.items = Array.from({ length: Math.min(50, 120 - offset) }, (_, index) => ({
          ...template,
          id: `X-${offset + index + 1}`,
          mirror_of: null,
        }));
        return HttpResponse.json(body);
      }),
    );
    const { user, app } = await openVersion("/sets/demo-papers/v2");
    expect(screen.getByRole("table", { name: "Items 1 to 50 of 120" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await findItemsTable("Items 51 to 100 of 120")).toBeInTheDocument();
    expect(app.router.state.location.search).toBe("?offset=50");
    // Focus goes to the table, a named region, once the new page is there.
    await waitFor(() => expect(screen.getByRole("region", { name: /scroll/i })).toHaveFocus());
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await findItemsTable("Items 101 to 120 of 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(seen).toEqual([0, 50, 100]);
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await findItemsTable("Items 51 to 100 of 120")).toBeInTheDocument();
  });

  describe("an address that names a page past the end", () => {
    function longList() {
      server.use(
        http.get("*/api/v1/sets/demo-papers/versions/v2", ({ request }) => {
          const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
          const body = capturedBody<Record<string, any>>("page demo-papers/v2");
          const template = body.items[0];
          body.total = 120;
          body.offset = offset;
          body.limit = 50;
          body.items = Array.from({ length: Math.max(0, Math.min(50, 120 - offset)) }, (_, i) => ({
            ...template,
            id: `X-${offset + i + 1}`,
            mirror_of: null,
          }));
          return HttpResponse.json(body);
        }),
      );
    }

    it("says so, instead of saying that no filter matches, and offers the last page", async () => {
      longList();
      const user = userEvent.setup();
      const app = renderApp("/sets/demo-papers/v2?offset=99999");
      expect(
        await screen.findByText(/starts at item 100000, but the list has only 120/),
      ).toBeInTheDocument();
      expect(screen.queryByText("No item matches these filters.")).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Go to the last page" }));
      expect(await findItemsTable("Items 101 to 120 of 120")).toBeInTheDocument();
      expect(app.router.state.location.search).toBe("?offset=100");
      // The button is gone by now: focus goes to the table, as it does after Previous and Next.
      await waitFor(() => expect(screen.getByRole("region", { name: /scroll/i })).toHaveFocus());
    });

    it("steps back to the last page, not 50 items from the address", async () => {
      longList();
      const user = userEvent.setup();
      renderApp("/sets/demo-papers/v2?offset=99999");
      await user.click(await screen.findByRole("button", { name: "Previous page" }));
      expect(await findItemsTable("Items 101 to 120 of 120")).toBeInTheDocument();
    });
  });

  it("keeps the page buttons focusable and quiet while the next page loads", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const seen: number[] = [];
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2", async ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        seen.push(offset);
        if (offset > 0) await gate;
        const body = capturedBody<Record<string, any>>("page demo-papers/v2");
        body.total = 120;
        body.offset = offset;
        body.items = Array.from({ length: Math.min(50, 120 - offset) }, (_, index) => ({
          ...body.items[0],
          id: `X-${offset + index + 1}`,
          mirror_of: null,
        }));
        return HttpResponse.json(body);
      }),
    );
    const { user } = await openVersion("/sets/demo-papers/v2");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    const next = screen.getByRole("button", { name: "Next page" });
    await waitFor(() => expect(next).toHaveAttribute("aria-disabled", "true"));
    expect(next).toBeEnabled();
    await user.click(next);
    release();
    await findItemsTable("Items 51 to 100 of 120");
    expect(seen).toEqual([0, 50]);
  });

  it("opens an item", async () => {
    const { user, app } = await openVersion("/sets/demo-papers/v2");
    await user.click(screen.getByRole("link", { name: "DP-003" }));
    await waitFor(() =>
      expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2/items/DP-003"),
    );
  });

  it("says when the version does not exist", async () => {
    renderApp("/sets/nothing/v9");
    expect(await screen.findByText("No such set.")).toBeInTheDocument();
    expect(screen.getByText("Not found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the question sets" })).toBeInTheDocument();
  });
});

describe("what a person may do with a version", () => {
  it("offers the owner or an admin the editing actions on a draft", async () => {
    await openVersion("/sets/demo-papers/v2");
    expect(screen.getByRole("link", { name: "Add item" })).toHaveAttribute(
      "href",
      "/sets/demo-papers/v2/new-item",
    );
    expect(screen.getByRole("link", { name: "Review the plan and freeze" })).toHaveAttribute(
      "href",
      "/sets/demo-papers/v2/freeze",
    );
    expect(screen.getByRole("button", { name: "Export file" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start the next version" })).toBeInTheDocument();
  });

  it("offers another member the plan but not the editing actions", async () => {
    server.use(meAs("dani"));
    await openVersion("/sets/demo-papers/v2");
    expect(screen.queryByRole("link", { name: "Add item" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Review the plan and freeze" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See the freeze plan" })).toBeInTheDocument();
  });

  it("offers the owner who is not an admin the editing actions", async () => {
    server.use(meAs("beto"));
    await openVersion("/sets/demo-papers/v2");
    expect(screen.getByRole("link", { name: "Add item" })).toBeInTheDocument();
  });

  it("closes the editing actions on a frozen version", async () => {
    await openVersion("/sets/demo-papers/v1");
    expect(document.querySelector('[data-status="frozen"]')).toHaveTextContent("Frozen");
    expect(screen.getByText("Cannot change")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Add item" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /freeze/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Frozen: its items, reviews and md5 never change/)).toBeInTheDocument();
  });

  it("starts the next version as a copy and opens it", async () => {
    const { user, app } = await openVersion("/sets/demo-papers/v1");
    let body: unknown;
    server.use(
      http.post("*/api/v1/sets", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(capturedBody("catalog")[1].versions[1], { status: 201 });
      }),
    );
    await user.click(screen.getByRole("button", { name: "Start the next version" }));
    await waitFor(() => expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2"));
    expect(body).toEqual({ name: "demo-papers", copy_from: "v1" });
  });

  it("shows why the next version could not be made", async () => {
    const { user } = await openVersion("/sets/demo-papers/v1");
    server.use(http.post("*/api/v1/sets", () => problemBody(409, "conflict", "Version taken.")));
    await user.click(screen.getByRole("button", { name: "Start the next version" }));
    expect(await screen.findByText("Version taken.")).toBeInTheDocument();
  });
});

describe("exporting (S4)", () => {
  it("saves the file as stored and says which md5 it has", async () => {
    const { user } = await openVersion("/sets/demo-papers/v1");
    const text = '{"format":"qset/1"}\n';
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v1/export", () =>
        HttpResponse.text(text, { headers: { "content-type": "application/x-ndjson" } }),
      ),
    );
    const created: Blob[] = [];
    URL.createObjectURL = (blob: Blob | MediaSource) => {
      created.push(blob as Blob);
      return "blob:test";
    };
    URL.revokeObjectURL = () => undefined;
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this.download);
    });
    await user.click(screen.getByRole("button", { name: "Export file" }));
    expect(
      await screen.findByText(/Saved demo-papers-v1.qset.jsonl.*cb9cb733168663d4b04e75cdf16e5030/),
    ).toBeInTheDocument();
    expect(clicked).toEqual(["demo-papers-v1.qset.jsonl"]);
    expect(await created[0]!.text()).toBe(text);
  });

  it("shows a failed export", async () => {
    const { user } = await openVersion("/sets/demo-papers/v1");
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v1/export", () =>
        problemBody(404, "not-found", "The file is gone."),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Export file" }));
    expect(await screen.findByText("The file is gone.")).toBeInTheDocument();
  });
});
