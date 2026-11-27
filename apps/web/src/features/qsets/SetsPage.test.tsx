import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedAnswer, capturedBody, problem, problemBody } from "@/test/sets-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";

preloadScreens(() => import("./SetsPage"));

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

async function openSets() {
  const user = userEvent.setup();
  const app = renderApp("/sets");
  await screen.findByRole("heading", { level: 1, name: "Question sets" });
  return { user, app };
}

describe("the catalog (S1)", () => {
  it("lists every version with its status, items, review progress and md5", async () => {
    await openSets();
    const table = await screen.findByRole("table", { name: /3 versions/ });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    const byName = (name: string) => rows.find((row) => within(row).queryByRole("link", { name }))!;

    const frozen = byName("demo-papers v1");
    expect(within(frozen).getByText("Frozen")).toBeInTheDocument();
    expect(within(frozen).getByText("cb9cb733")).toHaveAttribute(
      "title",
      "cb9cb733168663d4b04e75cdf16e5030",
    );
    expect(within(frozen).getByText("ana")).toBeInTheDocument();

    const draft = byName("demo-papers v2");
    expect(within(draft).getByText("Draft")).toBeInTheDocument();
    expect(within(draft).getByText("4 of 7 decided")).toBeInTheDocument();
    expect(
      within(draft).getByText("Accepted: 1, needs a second reviewer: 1, edited: 1, rejected: 1"),
    ).toBeInTheDocument();
    expect(within(draft).getByText("demo-corpus")).toBeInTheDocument();
    expect(within(draft).getByText(/Jan 1, 2026.*UTC/)).toBeInTheDocument();
  });

  it("links each version to its page", async () => {
    const { user, app } = await openSets();
    await user.click(await screen.findByRole("link", { name: "demo-papers v2" }));
    await waitFor(() => expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2"));
  });

  it("says when there is nothing yet", async () => {
    server.use(http.get("*/api/v1/sets", () => HttpResponse.json([])));
    await openSets();
    expect(await screen.findByText(/There are no question sets yet/)).toBeInTheDocument();
  });

  it("shows a failed read with the server's words and tries again", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      http.get("*/api/v1/sets", () => {
        calls += 1;
        return calls === 1
          ? problemBody(403, "forbidden", "The catalog could not be read.")
          : HttpResponse.json(capturedAnswer("catalog").response);
      }),
    );
    renderApp("/sets");
    expect(await screen.findByText("The catalog could not be read.")).toBeInTheDocument();
    expect(screen.getByText("Not allowed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: /3 versions/ })).toBeInTheDocument();
  });
});

describe("making a draft", () => {
  it("refuses a name the API would refuse, without sending it", async () => {
    const { user } = await openSets();
    let sent = false;
    server.use(
      http.post("*/api/v1/sets", () => {
        sent = true;
        return HttpResponse.json({});
      }),
    );
    await user.type(await screen.findByLabelText("Name"), "-bad name");
    await user.click(screen.getByRole("button", { name: "Make draft" }));
    expect(await screen.findByText(/Use letters, digits, dot/)).toBeInTheDocument();
    expect(sent).toBe(false);
  });

  it("makes an empty draft with a corpus and opens it", async () => {
    const { user, app } = await openSets();
    let body: unknown;
    server.use(
      http.post("*/api/v1/sets", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(capturedBody("catalog")[0].versions[0], { status: 201 });
      }),
    );
    await user.type(await screen.findByLabelText("Name"), "my-set.v1");
    await user.type(screen.getByLabelText("Corpus"), "mine");
    await user.click(screen.getByRole("button", { name: "Make draft" }));
    await waitFor(() => expect(app.router.state.location.pathname).toBe("/sets/demo-circuits/v1"));
    expect(body).toEqual({ name: "my-set.v1", corpus: "mine" });
  });

  it("makes the next version as a copy and takes the name from the set", async () => {
    const { user } = await openSets();
    let body: unknown;
    server.use(
      http.post("*/api/v1/sets", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(capturedBody("catalog")[1].versions[1], { status: 201 });
      }),
    );
    await screen.findByRole("option", { name: "A copy of demo-papers v1" });
    await user.selectOptions(screen.getByLabelText("Start from"), "A copy of demo-papers v1");
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.getByText(/becomes the next version of demo-papers/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Make draft" }));
    await waitFor(() => expect(body).toEqual({ name: "demo-papers", copy_from: "v1" }));
  });

  it("shows the API's refusal when the name exists", async () => {
    const { user } = await openSets();
    server.use(
      http.post("*/api/v1/sets", () =>
        problemBody(409, "conflict", "A set called demo-papers exists."),
      ),
    );
    await user.type(await screen.findByLabelText("Name"), "demo-papers");
    await user.click(screen.getByRole("button", { name: "Make draft" }));
    expect(await screen.findByText("A set called demo-papers exists.")).toBeInTheDocument();
    expect(screen.getByText("Conflict")).toBeInTheDocument();
  });
});

describe("importing a file (S4)", () => {
  function file(text: string, name = "set.jsonl") {
    return new File([text], name, { type: "application/x-ndjson" });
  }

  it("asks for a file first", async () => {
    const { user } = await openSets();
    await user.click(await screen.findByRole("button", { name: "Import" }));
    expect(await screen.findByText("Choose a file first.")).toBeInTheDocument();
  });

  it("refuses a file over the cap without sending it", async () => {
    const { user } = await openSets();
    const big = file("x");
    Object.defineProperty(big, "size", { value: 21 * 1024 * 1024 });
    await user.upload(await screen.findByLabelText("File"), big);
    await user.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText(/over 20 MiB/)).toBeInTheDocument();
  });

  it("sends the file itself as the body and reports the items read", async () => {
    const { user } = await openSets();
    let seen: { url: URL; type: string | null; text: string } | undefined;
    server.use(
      http.post("*/api/v1/sets/import", async ({ request }) => {
        seen = {
          url: new URL(request.url),
          type: request.headers.get("content-type"),
          text: await request.text(),
        };
        return HttpResponse.json(
          { version: capturedBody("catalog")[1].versions[0], items_read: 6, shape: null },
          { status: 201 },
        );
      }),
    );
    await user.upload(await screen.findByLabelText("File"), file("line one\nline two\n"));
    await user.type(screen.getByLabelText("Set name"), "imported");
    await user.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText("Read 6 items into demo-papers v1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the new draft" })).toHaveAttribute(
      "href",
      "/sets/demo-papers/v1",
    );
    expect(seen?.text).toBe("line one\nline two\n");
    expect(seen?.type).toBe("application/octet-stream");
    expect(seen?.url.searchParams.get("name")).toBe("imported");
    expect(seen?.url.searchParams.get("source_file")).toBe("set.jsonl");
    expect(seen?.url.searchParams.has("shape")).toBe(false);
    expect(seen?.url.searchParams.has("version")).toBe(false);
  });

  it("lists the lines and fields that made the API refuse the whole file", async () => {
    const { user } = await openSets();
    server.use(http.post("*/api/v1/sets/import", () => problem("import 422")));
    await user.upload(await screen.findByLabelText("File"), file("bad"));
    await user.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText("The file is not a valid qset/1 file.")).toBeInTheDocument();
    expect(screen.getByText("/lines/2")).toBeInTheDocument();
    expect(screen.getByText(/not valid JSON/)).toBeInTheDocument();
    expect(screen.getByText(/The file was not imported/)).toBeInTheDocument();
    expect(screen.getByText("Refused by qset/1")).toBeInTheDocument();
  });

  it("shows only the first problems of a long list and counts the rest", async () => {
    const { user } = await openSets();
    const errors = Array.from({ length: 30 }, (_, index) => ({
      path: `/lines/${index + 2}/lang`,
      message: "bad",
    }));
    server.use(
      http.post("*/api/v1/sets/import", () =>
        problemBody(422, "invalid-input", "The file is not a valid qset/1 file.", { errors }),
      ),
    );
    await user.upload(await screen.findByLabelText("File"), file("bad"));
    await user.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText("and 18 more problems")).toBeInTheDocument();
    expect(screen.getAllByText("bad")).toHaveLength(12);
  });

  it("converts an older shape: the corpus is asked for and sent with the shape", async () => {
    const { user } = await openSets();
    let url: URL | undefined;
    server.use(
      http.post("*/api/v1/sets/import", ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json(
          { version: capturedBody("catalog")[1].versions[0], items_read: 1, shape: "mathcirc-v1" },
          { status: 201 },
        );
      }),
    );
    const section = await screen.findByRole("region", { name: "Import a file" });
    expect(within(section).queryByLabelText("Corpus")).not.toBeInTheDocument();
    await user.selectOptions(within(section).getByLabelText("Kind of file"), "mathcirc-v1");
    expect(screen.getByText(/one gold paper per question/)).toBeInTheDocument();
    await user.type(within(section).getByLabelText("Corpus"), "papers");
    await user.upload(screen.getByLabelText("File"), file("{}", "old.jsonl"));
    await user.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText("Converted from the shape mathcirc-v1.")).toBeInTheDocument();
    expect(url?.searchParams.get("shape")).toBe("mathcirc-v1");
    expect(url?.searchParams.get("corpus")).toBe("papers");
  });
});
