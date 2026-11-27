import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedBody, meAs, problem, problemBody } from "@/test/sets-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";

preloadScreens(() => import("./NewItemPage"));

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

async function openNew(path = "/sets/demo-papers/v2/new-item") {
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "New item" });
  return { user, app };
}

const etag = capturedBody<Record<string, any>>("page demo-papers/v2").version.etag as string;

describe("adding an item", () => {
  it("starts from the corpus of the version, with a new id to choose", async () => {
    await openNew();
    const id = await screen.findByLabelText("Id");
    expect(id).not.toHaveAttribute("readonly");
    expect(id).toHaveValue("");
    expect(screen.getByLabelText("Corpus")).toHaveValue("demo-corpus");
    expect(screen.getByLabelText("Form")).toHaveValue("single");
    expect(screen.getByRole("button", { name: "Add the item" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Delete item" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Review" })).not.toBeInTheDocument();
  });

  it("sends the item with the etag of the draft and opens it", async () => {
    const { user, app } = await openNew();
    let seen: { ifMatch: string | null; body: Record<string, any> } | undefined;
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/items", async ({ request }) => {
        seen = {
          ifMatch: request.headers.get("if-match"),
          body: (await request.json()) as Record<string, any>,
        };
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    await user.type(await screen.findByLabelText("Id"), "NEW-1");
    await user.type(screen.getByLabelText("Question"), "A new question?");
    await user.click(screen.getByRole("button", { name: "Add the item" }));
    await waitFor(() =>
      expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2/items/NEW-1"),
    );
    expect(seen?.ifMatch).toBe(etag);
    expect(seen?.body).toEqual({
      id: "NEW-1",
      lang: "en",
      form: "single",
      content: "other",
      corpus: "demo-corpus",
      question: "A new question?",
      gold_papers: [],
    });
  });

  it("shows what qset/1 refuses at the field", async () => {
    const { user } = await openNew();
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/items", () =>
        problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
          errors: [
            { path: "/id", message: "'' does not match '^[A-Za-z0-9][A-Za-z0-9._-]*$'" },
            { path: "/question", message: "'' is too short" },
          ],
        }),
      ),
    );
    await user.click(await screen.findByRole("button", { name: "Add the item" }));
    expect((await screen.findAllByText("'' is too short")).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Id")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Question")).toHaveAttribute("aria-invalid", "true");
  });

  it("reads one item of the draft for its etag, not a whole page", async () => {
    const limits: (string | null)[] = [];
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2", ({ request }) => {
        limits.push(new URL(request.url).searchParams.get("limit"));
        return HttpResponse.json(capturedBody("page demo-papers/v2"));
      }),
    );
    await openNew();
    expect(limits).toEqual(["1"]);
  });

  it("tells a member who does not own the draft that only the owner adds", async () => {
    server.use(meAs("dani"));
    renderApp("/sets/demo-papers/v2/new-item");
    expect(
      await screen.findByText(/Only the owner \(beto\) or an admin can add items/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add the item" })).not.toBeInTheDocument();
  });

  it("does not offer a frozen version", async () => {
    renderApp("/sets/demo-papers/v1/new-item");
    expect(
      await screen.findByText(/Only the owner \(ana\) or an admin can add items/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add the item" })).not.toBeInTheDocument();
  });
});

describe("the draft moved while the new item was being written (409)", () => {
  it("offers to keep the edits, not to load the latest, and adds with the etag it read again", async () => {
    const { user, app } = await openNew();
    const posted: (string | null)[] = [];
    let created = false;
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/items", ({ request }) => {
        posted.push(request.headers.get("if-match"));
        if (posted.length === 1) return problem("put 409 stale");
        created = true;
        return HttpResponse.json({}, { status: 201 });
      }),
      http.get("*/api/v1/sets/demo-papers/versions/v2", () => {
        const body = capturedBody<Record<string, any>>("page demo-papers/v2");
        if (posted.length > 0) body.version.etag = '"after-the-move"';
        return HttpResponse.json(body);
      }),
    );
    await user.type(await screen.findByLabelText("Id"), "NEW-2");
    await user.type(screen.getByLabelText("Question"), "A question that is kept?");
    await user.click(screen.getByRole("button", { name: "Add the item" }));
    expect(await screen.findByText("Changed since you opened it")).toBeInTheDocument();
    // A new item has no stored copy to load: the button would do nothing, so it is not there.
    expect(screen.queryByRole("button", { name: "Load the latest" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep my edits" }));
    expect(await screen.findByText(/Your edits are kept; save again/)).toBeInTheDocument();
    expect(screen.getByLabelText("Question")).toHaveValue("A question that is kept?");
    await user.click(screen.getByRole("button", { name: "Add the item" }));
    await waitFor(() => expect(created).toBe(true));
    expect(posted).toEqual([etag, '"after-the-move"']);
    await waitFor(() =>
      expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2/items/NEW-2"),
    );
  });

  it("moves to the first field that the server refuses", async () => {
    const { user } = await openNew();
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/items", () =>
        problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
          errors: [{ path: "/question", message: "'' is too short" }],
        }),
      ),
    );
    await user.click(await screen.findByRole("button", { name: "Add the item" }));
    await waitFor(() => expect(screen.getByLabelText("Question")).toHaveFocus());
  });
});
