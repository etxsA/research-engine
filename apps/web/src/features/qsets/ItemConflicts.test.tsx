import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedBody, meAs, problem, problemBody } from "@/test/sets-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";

// These tests type into a whole editor: a busy machine must not turn the default 5 s into a failure.
vi.setConfig({ testTimeout: 20_000 });
import { invalidateSets } from "./api";

preloadScreens(() => import("./ItemPage"));

// These tests run a draft that behaves like the API for the conflicts that matter here: the etag
// names the whole draft, a write or a review with an old etag is refused with a 409, and a write
// moves the etag. Someone else's change is `served.change(...)`.

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

const BASE = "*/api/v1/sets/demo-papers/versions/v2";

interface Posted {
  ifMatch: string | null;
  body: Record<string, any>;
}

interface ServeOptions {
  /** The answer to a PUT waits for this, so a test can type while a save is in flight. */
  gate?: Promise<void>;
  /** The server stores something other than what it was sent (a normalized text, say). */
  transform?: (body: Record<string, any>) => Record<string, any>;
}

function serve(id: string, options: ServeOptions = {}) {
  const original = capturedBody<Record<string, any>>(`item demo-papers/v2/${id}`);
  const state = {
    item: original.item as Record<string, any>,
    etag: original.version.etag as string,
    moves: 0,
    puts: [] as Posted[],
    reviews: [] as Posted[],
    reads: 0,
  };
  const detail = () => ({
    ...original,
    item: state.item,
    version: { ...original.version, etag: state.etag },
  });
  const move = () => {
    state.moves += 1;
    state.etag = `"moved-${state.moves}"`;
  };
  server.use(
    http.get(`${BASE}/items/${id}`, () => {
      state.reads += 1;
      return HttpResponse.json(detail(), { headers: { etag: state.etag } });
    }),
    http.put(`${BASE}/items/${id}`, async ({ request }) => {
      const posted = {
        ifMatch: request.headers.get("if-match"),
        body: (await request.json()) as Record<string, any>,
      };
      state.puts.push(posted);
      await options.gate;
      if (posted.ifMatch !== state.etag) return problem("put 409 stale");
      state.item = options.transform ? options.transform(posted.body) : posted.body;
      move();
      return HttpResponse.json(detail());
    }),
    http.post(`${BASE}/reviews`, async ({ request }) => {
      const posted = {
        ifMatch: request.headers.get("if-match"),
        body: (await request.json()) as Record<string, any>,
      };
      state.reviews.push(posted);
      if (posted.ifMatch !== state.etag) return problem("put 409 stale");
      return HttpResponse.json({}, { status: 201 });
    }),
  );
  return {
    state,
    /** Someone else saves this item: other text, and the draft's etag moves. */
    change(fields: Record<string, unknown>) {
      state.item = { ...state.item, ...fields };
      move();
    },
    /** Someone else saves another item: the draft's etag moves, this item stays as it is. */
    touchOther() {
      move();
    },
  };
}

async function openItem(id: string) {
  const user = userEvent.setup();
  const app = renderApp(`/sets/demo-papers/v2/items/${id}`);
  await screen.findByRole("heading", { level: 1, name: id });
  await screen.findByRole("button", { name: "Check against qset/1" });
  return { user, app };
}

const question = () => screen.getByLabelText<HTMLTextAreaElement>("Question");
const notes = () => screen.getByLabelText<HTMLTextAreaElement>("Notes", { exact: true });
const save = () => screen.getByRole("button", { name: "Save changes" });
const rationale = () => screen.getByLabelText<HTMLTextAreaElement>("Rationale");
const otherBox = () => screen.getByLabelText<HTMLTextAreaElement>("Other fields (JSON)");

/** Open the promise a test holds a save on. */
function gate() {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { wait, release };
}

/** Replace the text of a box of JSON with `value`. */
async function setBox(user: ReturnType<typeof userEvent.setup>, box: HTMLElement, value: unknown) {
  await user.clear(box);
  await user.paste(JSON.stringify(value, null, 2));
}

/**
 * Add text to a box of JSON so that it cannot be read: the person is half way through an edit.
 * The text is added to the end, so the last text that could be read is still the one saved.
 */
async function breakBox(user: ReturnType<typeof userEvent.setup>) {
  await user.type(otherBox(), " nope");
  await screen.findAllByText(/Not valid JSON/);
}

describe("someone else changes the same item while it is open (S2, O-126)", () => {
  it("keeps their change and mine when I keep my edits after a refused save", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "THE OWNER REWROTE THIS QUESTION" });

    await user.click(save());
    expect(await screen.findByText("Changed since you opened it")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep my edits" }));

    expect(await screen.findByText("Merged with the latest version")).toBeInTheDocument();
    expect(screen.getByText(/because you had not changed them: question/)).toBeInTheDocument();
    expect(question()).toHaveValue("THE OWNER REWROTE THIS QUESTION");
    expect(notes().value).toMatch(/ mine$/);

    await user.click(save());
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    const ok = served.state.puts.at(-1)!;
    expect(ok.ifMatch).not.toBe(served.state.puts[0]!.ifMatch);
    expect(served.state.item.question).toBe("THE OWNER REWROTE THIS QUESTION");
    expect(String(served.state.item.notes)).toMatch(/ mine$/);
  });

  it("lists a field that both of us changed, keeps mine, and lets me take theirs", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(question(), " mine");
    const mine = question().value;
    served.change({ question: "Their version of the question" });

    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));

    expect(await screen.findByText("Merged with the latest version")).toBeInTheDocument();
    expect(screen.getByText(/You and someone else both changed these fields/)).toBeInTheDocument();
    expect(screen.getByText(`Yours (kept): ${mine}`)).toBeInTheDocument();
    expect(
      screen.getByText("Latest on the server: Their version of the question"),
    ).toBeInTheDocument();
    expect(question()).toHaveValue(mine);

    await user.click(screen.getByRole("button", { name: "Use the latest value of question" }));
    expect(question()).toHaveValue("Their version of the question");
    expect(screen.queryByText("Merged with the latest version")).not.toBeInTheDocument();
  });

  it("does not need a merge when only another item changed", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.touchOther();
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    expect(await screen.findByText(/Your edits are kept; save again/)).toBeInTheDocument();
    expect(screen.queryByText("Merged with the latest version")).not.toBeInTheDocument();
    await user.click(save());
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(served.state.puts).toHaveLength(2);
  });

  it("stops and asks when the item is read again under my edits (a reconnect)", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });

    expect(await screen.findByText("This item changed while you were editing")).toBeInTheDocument();
    expect(screen.getByText(/Someone else changed question\./)).toBeInTheDocument();
    // What I see is still what I had, and nothing can be saved or decided on it.
    expect(question()).not.toHaveValue("Changed while I typed");
    expect(save()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByText(/no decision can be sent until then/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Merge with my edits" }));
    expect(question()).toHaveValue("Changed while I typed");
    expect(notes().value).toMatch(/ mine$/);
    expect(screen.queryByText("This item changed while you were editing")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();

    await user.click(save());
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(served.state.puts).toHaveLength(1);
    expect(served.state.puts[0]!.body.question).toBe("Changed while I typed");
    expect(String(served.state.item.notes)).toMatch(/ mine$/);
  });

  it("can drop my edits and load what the server holds", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    await user.click(
      await screen.findByRole("button", { name: "Load the latest and drop my edits" }),
    );
    expect(question()).toHaveValue("Changed while I typed");
    expect(notes().value).not.toMatch(/ mine$/);
    expect(save()).toBeDisabled();
    expect(served.state.puts).toHaveLength(0);
  });

  it("says the latest version was loaded after a refused save, and moves focus there", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed by the owner" });
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Load the latest" }));
    const loaded = await screen.findByText(/The latest version was loaded/);
    expect(question()).toHaveValue("Changed by the owner");
    expect(notes().value).not.toMatch(/ mine$/);
    await waitFor(() => expect(loaded).toHaveFocus());
  });

  it("keeps the editor and its edits when a read fails while they are open", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " unsaved words");
    server.use(
      http.get(`${BASE}/items/DP-003`, () => problemBody(404, "not-found", "No such item.")),
    );
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    expect(await screen.findByText("No such item.")).toBeInTheDocument();
    expect(notes().value).toMatch(/ unsaved words$/);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("shows the latest text, and says so, when there is nothing of mine to lose", async () => {
    const served = serve("DP-003");
    const { app } = await openItem("DP-003");
    served.change({ question: "Someone rewrote the question" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    expect(await screen.findByText("This item changed while you had it open")).toBeInTheDocument();
    expect(screen.getByText(/Someone else changed question\./)).toBeInTheDocument();
    expect(question()).toHaveValue("Someone rewrote the question");
    expect(save()).toBeDisabled();
  });

  it("is not thrown off by its own save", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await user.click(save());
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    await waitFor(() => expect(served.state.reads).toBeGreaterThan(1));
    expect(notes().value).toMatch(/ mine$/);
    expect(screen.queryByText("This item changed while you had it open")).not.toBeInTheDocument();
    expect(screen.queryByText("This item changed while you were editing")).not.toBeInTheDocument();
    expect(save()).toBeDisabled();
    // A second change goes out with the etag the first one returned.
    await user.type(notes(), " again");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(2));
    expect(served.state.puts[1]!.ifMatch).toBe('"moved-1"');
  });

  it("keeps text typed while a save is in flight as unsaved changes", async () => {
    const held = gate();
    const served = serve("DP-003", { gate: held.wait });
    const { user } = await openItem("DP-003");
    await user.type(notes(), " first");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(1));
    await user.type(rationale(), "typed during the save");
    held.release();

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    // What was sent is saved; what came after is still the person's, and can be saved.
    expect(rationale()).toHaveValue("typed during the save");
    expect(notes().value).toMatch(/ first$/);
    expect(served.state.puts[0]!.body.rationale).toBeUndefined();
    await waitFor(() => expect(served.state.reads).toBeGreaterThan(1));
    expect(screen.queryByText("This item changed while you were editing")).not.toBeInTheDocument();
    expect(screen.queryByText("This item changed while you had it open")).not.toBeInTheDocument();
    expect(screen.getByText("1 field changed")).toBeInTheDocument();
    expect(save()).toBeEnabled();

    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(2));
    expect(served.state.puts[1]!.ifMatch).toBe('"moved-1"');
    expect(served.state.item.rationale).toBe("typed during the save");
    expect(String(served.state.item.notes)).toMatch(/ first$/);
  });

  it("shows what the server stored and keeps text typed while the save was in flight", async () => {
    const held = gate();
    const served = serve("DP-003", {
      gate: held.wait,
      transform: (body) => ({ ...body, question: `${String(body.question)} (normalized)` }),
    });
    const { user } = await openItem("DP-003");
    await user.type(notes(), " first");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(1));
    await user.type(rationale(), "typed during the save");
    held.release();

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    await waitFor(() => expect(question().value).toMatch(/\(normalized\)$/));
    expect(rationale()).toHaveValue("typed during the save");
    expect(notes().value).toMatch(/ first$/);
    expect(screen.queryByText("This item changed while you were editing")).not.toBeInTheDocument();
    expect(save()).toBeEnabled();
  });

  it("sends the etag of the text it was built from when a save and a read cross", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    await screen.findByText("This item changed while you were editing");
    // Save is refused in the page, so the old copy is never put over the new text.
    await user.click(save());
    expect(served.state.puts).toHaveLength(0);
    expect(served.state.item.question).toBe("Changed while I typed");
  });
});

describe("a review against text that moved (O-126 a)", () => {
  it("shows the reviewer the new text, keeps the note, and records against what they saw", async () => {
    server.use(meAs("dani"));
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    served.change({ question: "THE OWNER REWROTE THIS QUESTION" });

    await user.type(screen.getByLabelText("Note"), "Looks fine to me.");
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("The draft changed while you were reading")).toBeInTheDocument();
    expect(served.state.reviews).toHaveLength(1);

    // The editor was rebuilt from the item that was read again, and the reviewer is told.
    await waitFor(() => expect(question()).toHaveValue("THE OWNER REWROTE THIS QUESTION"));
    expect(screen.getByText("This item changed while you had it open")).toBeInTheDocument();
    expect(
      screen.getByText(/The text above changed while you had it open \(question\)/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Note")).toHaveValue("Looks fine to me.");
    // The change list shows nothing of the owner's change as if it were the reviewer's.
    expect(screen.getByText(/You have not changed any field/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("Recorded: accept.")).toBeInTheDocument();
    expect(served.state.reviews).toHaveLength(2);
    expect(served.state.reviews[1]!.ifMatch).toBe(served.state.etag);
    expect(
      screen.queryByText(/The text above changed while you had it open/),
    ).not.toBeInTheDocument();
  });

  it("asks a reviewer who also has edits to choose before deciding again", async () => {
    server.use(meAs("dani"));
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " dani's idea");
    served.change({ question: "THE OWNER REWROTE THIS QUESTION" });
    await user.type(screen.getByLabelText("Note"), "A note.");
    await user.click(screen.getByRole("button", { name: "Propose edit" }));
    expect(await screen.findByText("The draft changed while you were reading")).toBeInTheDocument();
    expect(await screen.findByText("This item changed while you were editing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Propose edit" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Merge with my edits" }));
    await user.click(screen.getByRole("button", { name: "Propose edit" }));
    expect(await screen.findByText("Recorded: edit.")).toBeInTheDocument();
    // Only the reviewer's own field is proposed.
    expect(Object.keys(served.state.reviews[1]!.body.edit)).toEqual(["notes"]);
  });

  it("goes on when only another item moved: the reviewer decides again with the new etag", async () => {
    server.use(meAs("dani"));
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    served.touchOther();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("The draft changed while you were reading")).toBeInTheDocument();
    expect(screen.queryByText("This item changed while you had it open")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("Recorded: accept.")).toBeInTheDocument();
  });
});

describe("focus after a refused form (scope 5.7)", () => {
  it("moves to the first field with a problem and keeps the save button focusable while it runs", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.put(`${BASE}/items/DP-003`, async () => {
        await gate;
        return problem("put 422");
      }),
    );
    await user.type(notes(), " x");
    await user.click(save());
    // Busy: the button says so, is not removed from the keyboard, and ignores a second click.
    await waitFor(() => expect(save()).toHaveAttribute("aria-disabled", "true"));
    expect(save()).toHaveFocus();
    await user.click(save());
    release();
    expect(await screen.findByText("The item is not a valid qset/1 item.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Language")).toHaveFocus());
    expect(served.state.puts).toHaveLength(0);
  });

  it("moves to the problems of a part this form does not show", async () => {
    serve("DP-004");
    const { user } = await openItem("DP-004");
    server.use(
      http.put(`${BASE}/items/DP-004`, () =>
        problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
          errors: [{ path: "/necessity", message: "'necessity' is a required property" }],
        }),
      ),
    );
    await user.type(question(), " x");
    await user.click(save());
    const group = await screen.findByRole("group", {
      name: "Problems at parts this form does not show",
    });
    await waitFor(() => expect(group).toHaveFocus());
  });

  it("moves to the problems of a check", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    server.use(
      http.post("*/api/v1/sets/validate", () =>
        HttpResponse.json({
          ok: false,
          errors: [{ path: "/lang", message: "'fr' is not one of ['en', 'es']" }],
        }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Check against qset/1" }));
    await waitFor(() => expect(screen.getByLabelText("Language")).toHaveFocus());
  });

  it("moves to a refusal that is not about a field", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    server.use(http.put(`${BASE}/items/DP-003`, () => problem("put 403 member")));
    await user.type(notes(), " x");
    await user.click(save());
    const alert = (await screen.findByText("Not allowed")).closest("[data-first-error]");
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it("moves to the refusal of a delete", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    server.use(
      http.delete(`${BASE}/items/DP-003`, () =>
        problemBody(409, "conflict", "DP-003 is the original of a mirror."),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Delete item" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    const alert = (await screen.findByText("DP-003 is the original of a mirror.")).closest(
      "[data-first-error]",
    );
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it("moves to the notice after a merge, since the button that was pressed is gone", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed by the owner" });
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    const heading = await screen.findByText("Merged with the latest version");
    await waitFor(() => expect(heading.closest("[data-note-focus]")).toHaveFocus());
  });
});

describe("the whole item as JSON shows where each problem is", () => {
  it("prints the whole pointer of every problem", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    server.use(
      http.put(`${BASE}/items/DP-003`, () =>
        problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
          errors: [
            { path: "/question", message: "'' is too short" },
            { path: "/key_facts/2/rel", message: "-1 is less than the minimum of 0" },
            { path: "", message: "the item as a whole" },
          ],
        }),
      ),
    );
    await user.type(notes(), " x");
    await user.click(save());
    await screen.findByText("The item is not a valid qset/1 item.");
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    const box = screen.getByLabelText("The whole item as JSON");
    const described = (box.getAttribute("aria-describedby") ?? "")
      .split(" ")
      .map((id) => document.getElementById(id)?.textContent ?? "")
      .join(" ");
    expect(described).toContain("/question: '' is too short");
    expect(described).toContain("/key_facts/2/rel: -1 is less than the minimum of 0");
    expect(described).toContain("the item as a whole");
    expect(within(box.parentElement!).getAllByRole("listitem").length).toBeGreaterThanOrEqual(3);
  });
});

describe("edits that are not saved are not lost by leaving", () => {
  const back = () => screen.getByRole("link", { name: "Back to demo-papers v2" });

  it("asks before a link leaves the page with edits, and stays on request", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await user.click(back());

    const alert = await screen.findByText("You have unsaved edits");
    expect(alert).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2/items/DP-003");
    await waitFor(() => expect(alert.closest('[role="alert"]')).toHaveFocus());

    await user.click(screen.getByRole("button", { name: "Stay and keep editing" }));
    expect(screen.queryByText("You have unsaved edits")).not.toBeInTheDocument();
    expect(notes().value).toMatch(/ mine$/);
  });

  it("leaves and drops the edits when asked", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await user.click(back());
    await user.click(await screen.findByRole("button", { name: "Leave and drop my edits" }));
    await waitFor(() => expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2"));
  });

  it("does not ask when there is nothing to lose, or once the edits are saved", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await user.click(save());
    await screen.findByText("Saved.");
    await user.click(back());
    await waitFor(() => expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2"));
    expect(screen.queryByText("You have unsaved edits")).not.toBeInTheDocument();
  });

  it("warns the browser before the tab is closed, only while there are edits", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    const closing = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(closing()).toBe(false);
    await user.type(notes(), " mine");
    expect(closing()).toBe(true);
  });

  it("does not stand in the way of the sign-in a lost session leads to", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await act(async () => {
      await app.router.navigate("/login?next=%2Fsets", { replace: true });
    });
    // The signed-in test user is sent on to `next`: the page was left, nothing asked first.
    expect(app.router.state.location.pathname).toBe("/sets");
    expect(screen.queryByText("You have unsaved edits")).not.toBeInTheDocument();
  });

  it("keeps the person in the view whose text cannot be read, and says why", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    const box = screen.getByLabelText("Other fields (JSON)");
    await user.click(box);
    await user.clear(box);
    await user.paste("{ nope");
    await screen.findAllByText(/Not valid JSON/);
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    expect(screen.getByRole("tab", { name: "Fields" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("before you change the view");
    // The text is still there to fix.
    expect(screen.getByLabelText<HTMLTextAreaElement>("Other fields (JSON)").value).toBe("{ nope");

    await user.clear(screen.getByLabelText("Other fields (JSON)"));
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    expect(screen.getByRole("tab", { name: "JSON" })).toHaveAttribute("aria-selected", "true");
  });

  it("asks before a link leaves the page while a box holds text that cannot be read", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await breakBox(user);
    await user.click(back());

    expect(await screen.findByText("You have unsaved edits")).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2/items/DP-003");
    await user.click(screen.getByRole("button", { name: "Stay and keep editing" }));
    expect(otherBox().value).toMatch(/ nope$/);
  });

  it("warns the browser before the tab is closed while a box cannot be read", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    await breakBox(user);
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});

describe("text that cannot be read yet is an edit (blocker 2 of round 2)", () => {
  it("does not rebuild the editor over it when someone else saves", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await breakBox(user);
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });

    expect(await screen.findByText("This item changed while you were editing")).toBeInTheDocument();
    expect(screen.queryByText("This item changed while you had it open")).not.toBeInTheDocument();
    expect(screen.queryByText(/You had no edits/)).not.toBeInTheDocument();
    expect(otherBox().value).toMatch(/ nope$/);
    expect(question()).not.toHaveValue("Changed while I typed");
    // The merge waits until the box can be read; saving stays off.
    expect(screen.getByRole("button", { name: "Merge with my edits" })).toBeDisabled();
    expect(screen.getByText(/Fix that box to merge your edits/)).toBeInTheDocument();
    expect(save()).toBeDisabled();
  });

  it("lets the person drop the text and load the latest with one click", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await breakBox(user);
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    await user.click(
      await screen.findByRole("button", { name: "Load the latest and drop my edits" }),
    );

    expect(question()).toHaveValue("Changed while I typed");
    expect(otherBox().value).toContain('"writer": "ana"');
    expect(screen.queryByText(/Not valid JSON/)).not.toBeInTheDocument();
    expect(screen.queryByText("This item changed while you were editing")).not.toBeInTheDocument();
  });

  it("can be discarded with the button when it is the only edit", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    const original = otherBox().value;
    await breakBox(user);
    const discard = screen.getByRole("button", { name: "Discard changes" });
    expect(discard).toBeEnabled();
    await user.click(discard);

    await waitFor(() => expect(otherBox().value).toBe(original));
    expect(screen.queryByText(/Not valid JSON/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard changes" })).toBeDisabled();
  });

  it("merges once the box can be read again", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    const original = otherBox().value;
    await breakBox(user);
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    await screen.findByText("This item changed while you were editing");

    await user.clear(otherBox());
    await user.paste(original);
    await user.click(screen.getByRole("button", { name: "Merge with my edits" }));
    expect(question()).toHaveValue("Changed while I typed");
    expect(notes().value).toMatch(/ mine$/);
  });
});

describe("text that cannot be read, round 3", () => {
  it("takes the latest value of a field that no broken box shows, and the broken box keeps its text", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(question(), " mine");
    served.change({ question: "Their version of the question" });
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    await screen.findByText("Merged with the latest version");

    await breakBox(user);
    const useLatest = screen.getByRole("button", { name: "Use the latest value of question" });
    expect(useLatest).toBeEnabled();
    expect(screen.queryByText(/Fix that box before you take the latest value/)).toBeNull();
    await user.click(useLatest);
    expect(question()).toHaveValue("Their version of the question");
    expect(otherBox().value).toMatch(/ nope$/);
    expect(screen.getAllByText(/Not valid JSON/).length).toBeGreaterThan(0);
  });

  it("waits for the box that shows the field, and takes the value once it can be read", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await setBox(user, otherBox(), { writer: "mine" });
    served.change({ writer: "theirs", question: "Changed too" });
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    await screen.findByText("Merged with the latest version");

    await breakBox(user);
    const useLatest = screen.getByRole("button", { name: "Use the latest value of writer" });
    expect(useLatest).toBeDisabled();
    expect(screen.getByText(/Fix that box before you take the latest value/)).toBeInTheDocument();
    await user.click(useLatest);
    expect(otherBox().value).toMatch(/ nope$/);

    await user.clear(otherBox());
    await user.paste(JSON.stringify({ writer: "mine" }));
    await user.click(screen.getByRole("button", { name: "Use the latest value of writer" }));
    expect(JSON.parse(otherBox().value)).toEqual({ writer: "theirs" });
  });

  it("holds every field while the box of the whole item cannot be read", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(question(), " mine");
    served.change({ question: "Their version of the question" });
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    await screen.findByText("Merged with the latest version");

    await user.click(screen.getByRole("tab", { name: "JSON" }));
    await user.type(screen.getByLabelText("The whole item as JSON"), " nope");
    await screen.findAllByText(/Not valid JSON/);
    expect(screen.getByRole("button", { name: "Use the latest value of question" })).toBeDisabled();
  });

  it("says why Keep my edits is off while a box cannot be read", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed while I typed" });
    await user.click(save());
    const keep = await screen.findByRole("button", { name: "Keep my edits" });
    expect(keep).toBeEnabled();

    await breakBox(user);
    expect(screen.getByRole("button", { name: "Keep my edits" })).toBeDisabled();
    expect(screen.getByText(/Fix that box to keep your edits/)).toBeInTheDocument();
    expect(otherBox().value).toMatch(/ nope$/);
  });

  it("does not discard while a save is in flight", async () => {
    const held = gate();
    const served = serve("DP-003", { gate: held.wait });
    const { user } = await openItem("DP-003");
    await user.type(notes(), " first");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(1));
    const discard = screen.getByRole("button", { name: "Discard changes" });
    expect(discard).toHaveAttribute("aria-disabled", "true");
    await user.click(discard);
    expect(notes().value).toMatch(/ first$/);
    held.release();
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(notes().value).toMatch(/ first$/);
    expect(screen.getByRole("button", { name: "Discard changes" })).toBeDisabled();
  });

  it("keeps focus and a tag row just added when the server stores other text", async () => {
    const held = gate();
    const served = serve("DP-003", {
      gate: held.wait,
      transform: (body) => ({ ...body, question: `${String(body.question)} (normalized)` }),
    });
    const { user } = await openItem("DP-003");
    await user.type(notes(), " first");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Add a tag" }));
    await user.type(rationale(), "typed during the save");
    const tagsBefore = screen.getAllByLabelText(/^Tag \d+ name$/).length;
    held.release();

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    await waitFor(() => expect(question().value).toMatch(/\(normalized\)$/));
    expect(rationale()).toHaveFocus();
    expect(rationale()).toHaveValue("typed during the save");
    expect(screen.getAllByLabelText(/^Tag \d+ name$/)).toHaveLength(tagsBefore);
  });

  it("shows readable boxes what the server stored even when another box is broken", async () => {
    const held = gate();
    const served = serve("DP-003", {
      gate: held.wait,
      transform: (body) => ({ ...body, added_by_server: 7 }),
    });
    const { user } = await openItem("DP-003");
    await user.type(notes(), " first");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(1));
    const gold = () => screen.getByLabelText<HTMLTextAreaElement>("Gold papers (JSON)");
    await user.type(gold(), " nope");
    await screen.findAllByText(/Not valid JSON/);
    held.release();

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    await waitFor(() => expect(otherBox().value).toContain("added_by_server"));
    expect(gold().value).toMatch(/ nope$/);
  });
});

describe("a field shown in a part that keeps its own text", () => {
  async function mergeWith(
    edit: (user: ReturnType<typeof userEvent.setup>) => Promise<void>,
    theirs: Record<string, unknown>,
  ) {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await edit(user);
    served.change(theirs);
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    await screen.findByText("Merged with the latest version");
    return { user };
  }

  it("shows the latest value of a field in Other fields (JSON)", async () => {
    const { user } = await mergeWith((user) => setBox(user, otherBox(), { writer: "mine" }), {
      writer: "theirs",
    });
    await user.click(screen.getByRole("button", { name: "Use the latest value of writer" }));
    expect(JSON.parse(otherBox().value)).toEqual({ writer: "theirs" });
  });

  it("shows the latest value of a field in its own box of JSON", async () => {
    const box = () => screen.getByLabelText<HTMLTextAreaElement>("Search record (JSON)");
    const { user } = await mergeWith(
      (user) => setBox(user, box(), { corpus: "demo-corpus", terms: ["mine"] }),
      { search_record: { corpus: "demo-corpus", terms: ["theirs"] } },
    );
    await user.click(screen.getByRole("button", { name: "Use the latest value of search_record" }));
    expect(JSON.parse(box().value)).toEqual({ corpus: "demo-corpus", terms: ["theirs"] });
  });

  it("shows the latest value of the tags", async () => {
    const { user } = await mergeWith(
      async (user) => {
        await user.click(screen.getByRole("button", { name: "Add a tag" }));
        await user.type(screen.getByLabelText("Tag 1 name"), "topic");
        await user.type(screen.getByLabelText("Tag 1 value"), "mine");
      },
      { tags: { topic: "theirs" } },
    );
    await user.click(screen.getByRole("button", { name: "Use the latest value of tags" }));
    expect(screen.getByLabelText("Tag 1 name")).toHaveValue("topic");
    expect(screen.getByLabelText("Tag 1 value")).toHaveValue("theirs");
  });

  it("shows the latest value of the key facts", async () => {
    const { user } = await mergeWith(
      async (user) => {
        await user.click(screen.getByRole("button", { name: "Add a key fact" }));
      },
      { key_facts: [{ type: "text", value: "their fact", alts: [] }] },
    );
    await user.click(screen.getByRole("button", { name: "Use the latest value of key_facts" }));
    expect(screen.getByDisplayValue("their fact")).toBeInTheDocument();
  });
});

describe("focus when the server changes the field in use", () => {
  /** Hold a save, put focus in a control with `focus`, then let the save finish. */
  async function focusDuringSave(
    transform: (body: Record<string, any>) => Record<string, any>,
    focus: (user: ReturnType<typeof userEvent.setup>) => Promise<void>,
    prepare?: (user: ReturnType<typeof userEvent.setup>) => Promise<void>,
  ) {
    const held = gate();
    const served = serve("DP-003", { gate: held.wait, transform });
    const { user } = await openItem("DP-003");
    await prepare?.(user);
    await user.type(notes(), " first");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(1));
    await focus(user);
    held.release();
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  }

  it("keeps focus in the box of JSON of a field the server changed", async () => {
    const box = () => screen.getByLabelText<HTMLTextAreaElement>("Search record (JSON)");
    await focusDuringSave(
      (body) => ({ ...body, search_record: { corpus: "stored", terms: [] } }),
      async (user) => {
        await user.click(box());
        await user.keyboard(" ");
      },
    );
    await waitFor(() => expect(box().value).toContain("stored"));
    expect(box()).toHaveFocus();
  });

  it("keeps focus in Other fields (JSON) when the server adds a field", async () => {
    await focusDuringSave(
      (body) => ({ ...body, added_by_server: 7 }),
      async (user) => {
        await user.click(otherBox());
        await user.keyboard(" ");
      },
    );
    await waitFor(() => expect(otherBox().value).toContain("added_by_server"));
    expect(otherBox()).toHaveFocus();
  });

  it("keeps focus in the tags when the server adds a tag", async () => {
    await focusDuringSave(
      (body) => ({ ...body, tags: { ...(body.tags ?? {}), extra: "1" } }),
      async (user) => {
        await user.click(screen.getByLabelText("Tag 1 value"));
      },
      async (user) => {
        await user.click(screen.getByRole("button", { name: "Add a tag" }));
        await user.type(screen.getByLabelText("Tag 1 name"), "topic");
        await user.type(screen.getByLabelText("Tag 1 value"), "mine");
      },
    );
    await screen.findByLabelText("Tag 2 name");
    expect(screen.getByLabelText("Tag 1 value")).toHaveFocus();
  });

  it("keeps focus in the whole-item box when the server changes a field", async () => {
    const held = gate();
    const served = serve("DP-003", {
      gate: held.wait,
      transform: (body) => ({ ...body, added_by_server: 7 }),
    });
    const { user } = await openItem("DP-003");
    await user.type(notes(), " first");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(1));
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    const whole = () => screen.getByLabelText<HTMLTextAreaElement>("The whole item as JSON");
    await user.click(whole());
    await user.keyboard(" ");
    held.release();
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    await waitFor(() => expect(whole().value).toContain("added_by_server"));
    expect(whole()).toHaveFocus();
  });
});
