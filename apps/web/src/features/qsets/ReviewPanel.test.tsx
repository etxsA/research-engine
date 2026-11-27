import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedBody, meAs, problem, problemBody } from "@/test/sets-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";

preloadScreens(() => import("./ItemPage"));

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

async function openItem(id: string) {
  const user = userEvent.setup();
  const app = renderApp(`/sets/demo-papers/v2/items/${id}`);
  await screen.findByRole("heading", { level: 1, name: id });
  await screen.findByRole("button", { name: "Accept" });
  return { user, app };
}

interface Posted {
  ifMatch: string | null;
  body: Record<string, any>;
}

function recordReviews(
  answer: () => Response | Promise<Response> = () => HttpResponse.json({}, { status: 201 }),
) {
  const posted: Posted[] = [];
  server.use(
    http.post("*/api/v1/sets/demo-papers/versions/v2/reviews", async ({ request }) => {
      posted.push({
        ifMatch: request.headers.get("if-match"),
        body: (await request.json()) as Record<string, any>,
      });
      return answer();
    }),
  );
  return posted;
}

const reviewRegion = () => screen.getByRole("region", { name: "Review" });
const etag = capturedBody<Record<string, any>>("item demo-papers/v2/DP-003").version.etag;

describe("what the review says about an item (S3)", () => {
  it("says nobody has decided yet", async () => {
    await openItem("DP-003");
    expect(within(reviewRegion()).getByText("Not reviewed")).toBeInTheDocument();
    expect(within(reviewRegion()).getByText("Nobody has decided yet.")).toBeInTheDocument();
    expect(screen.getByText("Nobody has decided on this item yet.")).toBeInTheDocument();
  });

  it("asks for a second reviewer until two different people accepted", async () => {
    await openItem("DP-007");
    expect(within(reviewRegion()).getByText("Needs a second reviewer")).toBeInTheDocument();
    expect(
      within(reviewRegion()).getByText(
        "Accepted by ana. A different person must accept it too before it counts as gold.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Gold")).not.toBeInTheDocument();
  });

  it("marks an item that two different people accepted as gold", async () => {
    await openItem("DP-001");
    expect(
      within(reviewRegion()).getByText("Accepted by ana, beto: two different people."),
    ).toBeInTheDocument();
    expect(screen.getByText("Gold")).toBeInTheDocument();
  });

  it("explains an edit and a rejection", async () => {
    await openItem("DP-002");
    expect(
      within(reviewRegion()).getByText(
        "The latest decision is an edit proposed by ana; freezing applies it.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Your latest decision: edit.")).toBeInTheDocument();
  });

  it("explains a rejection", async () => {
    await openItem("DP-004");
    expect(
      within(reviewRegion()).getByText(
        "The latest decision is a rejection by beto; freezing removes this item.",
      ),
    ).toBeInTheDocument();
  });

  it("lists who decided what, when, why and with which fields", async () => {
    await openItem("DP-002");
    const history = screen.getByRole("list", { name: "Decisions" });
    const entry = history.querySelector<HTMLElement>(":scope > li")!;
    expect(within(entry).getByText("ana")).toBeInTheDocument();
    expect(within(entry).getByText("edit")).toBeInTheDocument();
    expect(within(entry).getByText(/Jan 1, 2026.*UTC/)).toBeInTheDocument();
    expect(within(entry).getByText("Name the paper that introduces J.")).toBeInTheDocument();
    expect(within(entry).getByText("answer")).toBeInTheDocument();
    expect(within(entry).getByText(/replaces the scalar coupling J of Alpha/)).toBeInTheDocument();
    expect(
      within(entry).queryByText("Decided on an earlier state of the draft"),
    ).not.toBeInTheDocument();
  });

  it("marks a decision made before the draft last changed", async () => {
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2/items/DP-002", () => {
        const body = capturedBody<Record<string, any>>("item demo-papers/v2/DP-002");
        body.reviews[0].set_md5 = "0".repeat(32);
        return HttpResponse.json(body);
      }),
    );
    await openItem("DP-002");
    expect(screen.getByText("Decided on an earlier state of the draft")).toBeInTheDocument();
  });
});

describe("deciding (S3)", () => {
  it("accepts with the etag that was read and no note", async () => {
    const { user } = await openItem("DP-003");
    const posted = recordReviews();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("Recorded: accept.")).toBeInTheDocument();
    expect(posted).toEqual([{ ifMatch: etag, body: { item_id: "DP-003", verdict: "accept" } }]);
  });

  it("sends the note of an accept when there is one, and empties the box", async () => {
    const { user } = await openItem("DP-003");
    const posted = recordReviews();
    await user.type(screen.getByLabelText("Note"), "  Checked against the paper.  ");
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await screen.findByText("Recorded: accept.");
    expect(posted[0]!.body.note).toBe("Checked against the paper.");
    expect(screen.getByLabelText("Note")).toHaveValue("");
  });

  it("reads the item again after a decision", async () => {
    const { user } = await openItem("DP-003");
    recordReviews();
    let reads = 0;
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2/items/DP-003", () => {
        reads += 1;
        return HttpResponse.json(capturedBody("item demo-papers/v2/DP-003"));
      }),
    );
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(reads).toBeGreaterThan(0));
  });

  it("needs a note for a rejection and sends nothing without one", async () => {
    const { user } = await openItem("DP-003");
    const posted = recordReviews();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(
      await screen.findByText("Write a note: an edit or a reject needs one."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Note")).toHaveFocus();
    expect(screen.getByLabelText("Note")).toHaveAttribute("aria-invalid", "true");
    expect(posted).toHaveLength(0);
    await user.type(screen.getByLabelText("Note"), "Not answerable from the corpus.");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Recorded: reject.")).toBeInTheDocument();
    expect(posted[0]!.body).toEqual({
      item_id: "DP-003",
      verdict: "reject",
      note: "Not answerable from the corpus.",
    });
  });

  it("needs a note for an edit, and a change to propose", async () => {
    const { user } = await openItem("DP-003");
    const posted = recordReviews();
    await user.click(screen.getByRole("button", { name: "Propose edit" }));
    expect(
      await screen.findByText("Write a note: an edit or a reject needs one."),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("Note"), "A reason.");
    await user.click(screen.getByRole("button", { name: "Propose edit" }));
    expect((await screen.findAllByText(/You have not changed any field/)).length).toBeGreaterThan(
      0,
    );
    expect(posted).toHaveLength(0);
  });

  it("proposes the fields that were changed and nothing else", async () => {
    const { user } = await openItem("DP-003");
    const posted = recordReviews();
    const answer = screen.getByLabelText("Answer");
    await user.clear(answer);
    await user.type(answer, "A better answer.");
    await user.selectOptions(screen.getByLabelText("Split"), "dev");
    const changes = screen.getByRole("heading", {
      name: "Changes an edit would propose",
    }).parentElement!;
    expect(
      screen.getByText(/Accept and reject are about the item as it is saved/),
    ).toBeInTheDocument();
    expect(within(changes).getByText("answer")).toBeInTheDocument();
    expect(within(changes).getByText("A better answer.")).toBeInTheDocument();
    expect(within(changes).getByText("split")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Note"), "Say it better.");
    await user.click(screen.getByRole("button", { name: "Propose edit" }));
    expect(await screen.findByText("Recorded: edit.")).toBeInTheDocument();
    expect(posted[0]!.body).toEqual({
      item_id: "DP-003",
      verdict: "edit",
      note: "Say it better.",
      edit: { answer: "A better answer.", split: "dev" },
    });
  });

  it("says that a removed field cannot be proposed", async () => {
    const { user } = await openItem("DP-007");
    await user.click(screen.getByRole("button", { name: "Remove key fact 1" }));
    expect(screen.getByText(/Removing a field cannot be proposed.*key_facts/)).toBeInTheDocument();
  });

  it("lets a member who does not own the draft decide", async () => {
    server.use(meAs("dani"));
    const { user } = await openItem("DP-007");
    const posted = recordReviews();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("Recorded: accept.")).toBeInTheDocument();
    expect(posted[0]!.body.item_id).toBe("DP-007");
  });
});

describe("a draft that changed while the item was open (O-126 a)", () => {
  it("reads the item again, says so, and keeps the note", async () => {
    const { user } = await openItem("DP-003");
    const posted = recordReviews(() => problem("review 409 stale"));
    let reads = 0;
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2/items/DP-003", () => {
        reads += 1;
        const body = capturedBody<Record<string, any>>("item demo-papers/v2/DP-003");
        body.version.etag = '"newer"';
        return HttpResponse.json(body);
      }),
    );
    await user.type(screen.getByLabelText("Note"), "My careful note.");
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("The draft changed while you were reading")).toBeInTheDocument();
    expect(screen.getByText(/The item was read again/)).toBeInTheDocument();
    await waitFor(() => expect(reads).toBeGreaterThan(0));
    expect(screen.getByLabelText("Note")).toHaveValue("My careful note.");

    // Deciding again uses the etag of the item as it was read again.
    const again = recordReviews();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await screen.findByText("Recorded: accept.");
    expect(posted).toHaveLength(1);
    expect(again[0]!.ifMatch).toBe('"newer"');
    expect(again[0]!.body.note).toBe("My careful note.");
  });

  it("shows any other refusal in the server's words", async () => {
    const { user } = await openItem("DP-003");
    recordReviews(() => problemBody(403, "forbidden", "This version is frozen."));
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("This version is frozen.")).toBeInTheDocument();
    expect(screen.queryByText("The draft changed while you were reading")).not.toBeInTheDocument();
  });
});
