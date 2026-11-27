import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedBody, meAs, problemBody } from "@/test/sets-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";

preloadScreens(() => import("./FreezePage"));

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

const PATH = "/sets/demo-papers/v2/freeze";

async function openPlan(path = PATH) {
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: /Freeze demo-papers v2/ });
  return { user, app };
}

const group = (name: string) => {
  const term = screen.getByText(name, { selector: "dt" });
  return term.parentElement!;
};

const plan = () => capturedBody<Record<string, any>>("freeze demo-papers/v2").plan;

describe("the freeze plan (S5)", () => {
  it("says how many items go and lists each group by id", async () => {
    await openPlan();
    expect(
      await screen.findByText("The draft has 7 items; the frozen version will have 6."),
    ).toBeInTheDocument();
    expect(
      within(group("Accepted (kept) (2)")).getByRole("link", { name: "DP-001" }),
    ).toHaveAttribute("href", "/sets/demo-papers/v2/items/DP-001");
    expect(
      within(group("Rejected (removed) (1)")).getByRole("link", { name: "DP-004" }),
    ).toBeInTheDocument();
    expect(
      within(group("Edit proposed (the edit is applied) (1)")).getByRole("link", {
        name: "DP-002",
      }),
    ).toBeInTheDocument();
    expect(
      within(group("Accepted by one person only (kept) (1)")).getByRole("link", { name: "DP-007" }),
    ).toBeInTheDocument();
    expect(within(group("Not reviewed (kept as they are) (3)")).getAllByRole("link")).toHaveLength(
      3,
    );
    expect(
      within(group("Decided on an earlier state of the draft (0)")).getByText("None"),
    ).toBeInTheDocument();
  });

  it("explains what stale means only when a decision is stale", async () => {
    await openPlan();
    await screen.findByText(/The draft has 7 items/);
    expect(screen.queryByText("What stale means")).not.toBeInTheDocument();
  });

  it("explains it when there is one: the decision still applies", async () => {
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2/freeze", () => {
        const body = capturedBody<Record<string, any>>("freeze demo-papers/v2");
        body.plan.stale = ["DP-001"];
        return HttpResponse.json(body);
      }),
    );
    await openPlan();
    expect(await screen.findByText("What stale means")).toBeInTheDocument();
    expect(
      screen.getByText(/It still applies: an item with decisions keeps its text/),
    ).toBeInTheDocument();
    expect(
      within(group("Decided on an earlier state of the draft (1)")).getByRole("link", {
        name: "DP-001",
      }),
    ).toBeInTheDocument();
  });

  it("lists decisions about items that are gone without a link", async () => {
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2/freeze", () => {
        const body = capturedBody<Record<string, any>>("freeze demo-papers/v2");
        body.plan.unknown = ["GONE-1"];
        return HttpResponse.json(body);
      }),
    );
    await openPlan();
    const unknown = await screen.findByText("Decisions about items the draft no longer has (1)");
    expect(within(unknown.parentElement!).getByText("GONE-1")).toBeInTheDocument();
    expect(within(unknown.parentElement!).queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("freezing (S5, O-126 d)", () => {
  it("asks again before freezing, and not yet leaves the draft alone", async () => {
    const { user } = await openPlan();
    await user.click(await screen.findByRole("button", { name: "Freeze this version" }));
    expect(screen.getByText("Freeze demo-papers v2 for good?")).toBeInTheDocument();
    expect(screen.getByText(/The version will have 6 of its 7 items/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Not yet" }));
    expect(screen.queryByText("Freeze demo-papers v2 for good?")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Freeze this version" })).toBeInTheDocument();
  });

  it("sends the etag of the plan that was read and shows the md5 of the frozen file", async () => {
    const { user } = await openPlan();
    let ifMatch: string | null = null;
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/freeze", ({ request }) => {
        ifMatch = request.headers.get("if-match");
        const body = capturedBody<Record<string, any>>("freeze demo-papers/v2");
        body.version.status = "frozen";
        body.version.md5 = "f".repeat(32);
        body.plan.items_after = 6;
        return HttpResponse.json(body);
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Freeze this version" }));
    await user.click(screen.getByRole("button", { name: "Yes, freeze" }));
    expect(await screen.findByText("demo-papers v2 is frozen")).toBeInTheDocument();
    expect(screen.getByTestId("frozen-md5")).toHaveTextContent("f".repeat(32));
    expect(screen.getByText("It has 6 of the 7 items of the draft.")).toBeInTheDocument();
    expect(ifMatch).toBe(plan().etag);
  });

  it("says a decision or an item changed, and reads the plan again on request", async () => {
    const { user } = await openPlan();
    let reads = 0;
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2/freeze", () => {
        reads += 1;
        const body = capturedBody<Record<string, any>>("freeze demo-papers/v2");
        body.plan.etag = `"read-${reads}"`;
        return HttpResponse.json(body);
      }),
      http.post("*/api/v1/sets/demo-papers/versions/v2/freeze", ({ request }) =>
        Number(request.headers.get("if-match")?.replace(/\D/g, "")) >= 2
          ? HttpResponse.json({
              version: capturedBody("freeze demo-papers/v2").version,
              plan: plan(),
            })
          : problemBody(409, "conflict", "The plan changed since you read it."),
      ),
    );
    await screen.findByText(/The draft has 7 items/);
    await user.click(screen.getByRole("button", { name: "Freeze this version" }));
    await user.click(screen.getByRole("button", { name: "Yes, freeze" }));
    expect(await screen.findByText("A decision or an item changed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yes, freeze" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Read the plan again" }));
    await waitFor(() =>
      expect(screen.queryByText("A decision or an item changed")).not.toBeInTheDocument(),
    );
    expect(reads).toBeGreaterThanOrEqual(2);
    await user.click(await screen.findByRole("button", { name: "Freeze this version" }));
    await user.click(screen.getByRole("button", { name: "Yes, freeze" }));
    expect(await screen.findByText("demo-papers v2 is frozen")).toBeInTheDocument();
  });

  it("shows a refusal of the freeze with its reason", async () => {
    const { user } = await openPlan();
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/freeze", () =>
        problemBody(422, "invalid-input", "DP-001-es mirrors DP-001, which was rejected."),
      ),
    );
    await user.click(await screen.findByRole("button", { name: "Freeze this version" }));
    await user.click(screen.getByRole("button", { name: "Yes, freeze" }));
    expect(
      await screen.findByText("DP-001-es mirrors DP-001, which was rejected."),
    ).toBeInTheDocument();
    expect(screen.getByText("Refused by qset/1")).toBeInTheDocument();
  });

  it("lets another member read the plan but not freeze", async () => {
    server.use(meAs("dani"));
    await openPlan();
    expect(await screen.findByText(/The draft has 7 items/)).toBeInTheDocument();
    expect(screen.getByText("Only the owner or an admin freezes a draft")).toBeInTheDocument();
    expect(screen.getByText("This draft belongs to beto.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Freeze this version" })).not.toBeInTheDocument();
  });

  it("does not read a plan for a version that is already frozen", async () => {
    let reads = 0;
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v1/freeze", () => {
        reads += 1;
        return problemBody(403, "forbidden", "Frozen.");
      }),
    );
    renderApp("/sets/demo-papers/v1/freeze");
    expect(await screen.findByText("This version is already frozen")).toBeInTheDocument();
    expect(screen.getByText(/cb9cb733168663d4b04e75cdf16e5030/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Freeze this version" })).not.toBeInTheDocument();
    expect(reads).toBe(0);
  });

  it("shows a plan that cannot be read, and tries again", async () => {
    const user = userEvent.setup();
    let reads = 0;
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2/freeze", () => {
        reads += 1;
        return reads === 1
          ? problemBody(404, "not-found", "No plan today.")
          : HttpResponse.json(capturedBody("freeze demo-papers/v2"));
      }),
    );
    renderApp(PATH);
    expect(await screen.findByText("No plan today.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/The draft has 7 items/)).toBeInTheDocument();
  });
});
