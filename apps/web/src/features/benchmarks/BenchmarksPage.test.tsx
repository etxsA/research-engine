import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { CAPTURE, createJobStand, problem, RUNS, type JobStand } from "@/test/benchmarks-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";
import { polling } from "./api";
import type { Model } from "./types";

preloadScreens(() => import("./BenchmarksPage"));

type User = ReturnType<typeof userEvent.setup>;

const originalPolling = { ...polling };

beforeEach(() => {
  polling.jobMs = 20;
  polling.idleMs = 40;
  polling.modelsMs = 30;
  polling.statusMs = 30;
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

afterEach(() => {
  Object.assign(polling, originalPolling);
});

async function openPage(stand?: JobStand): Promise<User> {
  if (stand) server.use(...stand.handlers);
  const user = userEvent.setup({ delay: null });
  renderApp("/benchmarks");
  await screen.findByRole("heading", { level: 1, name: "Benchmarks" });
  return user;
}

const form = () => screen.getByRole("region", { name: "Start a run" });

describe("the start form", () => {
  it("lists the frozen versions of the sets with their md5, and no draft", async () => {
    await openPage();
    const select = await screen.findByLabelText("Question set");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(1));
    const options = within(select).getAllByRole("option");
    expect(options[0]).toHaveTextContent("demo-papers v1 (md5 cb9cb733, 6 questions)");
    expect(select).toHaveValue("demo-papers/v1");
    expect(screen.queryByRole("option", { name: /demo-circuits/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /demo-papers v2/ })).not.toBeInTheDocument();
  });

  it("points to the question sets when none is frozen", async () => {
    server.use(http.get("*/api/v1/sets", () => HttpResponse.json([])));
    await openPage();
    expect(await screen.findByText(/There is no frozen question set yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the question sets" })).toHaveAttribute(
      "href",
      "/sets",
    );
  });

  it("offers the models of the service and does not let a model that is down be chosen", async () => {
    await openPage();
    const up = await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    const down = screen.getByRole("checkbox", { name: "fake-offline (demo, down)" });
    expect(up).toBeEnabled();
    expect(up).toBeChecked();
    expect(down).toBeDisabled();
    expect(down).not.toBeChecked();
    expect(down).toHaveAccessibleDescription(/Not answering, so it cannot be chosen/);
    expect(up).toHaveAccessibleDescription(/revision synthetic-0. Answering/);
  });

  it("lets the oracle arm be chosen when the service runs live", async () => {
    let reads = 0;
    server.use(
      http.get("*/api/v1/status", () => {
        reads += 1;
        return HttpResponse.json({ ...CAPTURE.status, mode: "live" });
      }),
    );
    const user = await openPage();
    const oracle = await screen.findByRole("checkbox", { name: "oracle" });
    await waitFor(() => expect(reads).toBeGreaterThan(1));
    expect(oracle).toBeEnabled();
    expect(oracle).toHaveAccessibleDescription(/gold passages/);
    expect(oracle).toHaveAccessibleDescription(/no text is refused/);
    await user.click(oracle);
    expect(oracle).toBeChecked();
    const seen = reads;
    await waitFor(() => expect(reads).toBeGreaterThan(seen));
    expect(oracle).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "harness" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "bare" })).toBeEnabled();
  });

  it("lets the oracle arm be chosen on test data", async () => {
    await openPage();
    const oracle = await screen.findByRole("checkbox", { name: "oracle" });
    expect(oracle).toBeEnabled();
    expect(oracle).toHaveAccessibleDescription(/gold passages/);
  });

  it("shows the message of the server when it refuses an oracle run on a set without gold text", async () => {
    const message =
      "The oracle arm answers from the gold passage texts (md_text), and 7 of 9 questions of demo-papers v1 have a gold passage without one: DP-001, DP-002. Run it on a set whose gold passages all have it, or leave the oracle arm out.";
    const bodies: unknown[] = [];
    server.use(
      http.get("*/api/v1/status", () => HttpResponse.json({ ...CAPTURE.status, mode: "live" })),
      http.post("*/api/v1/runs", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(
          {
            type: "urn:research-api:problem:invalid-input",
            title: "Unprocessable Content",
            status: 422,
            detail: message,
            instance: "/api/v1/runs",
            errors: [{ path: "/arms/0", message }],
          },
          { status: 422, headers: { "content-type": "application/problem+json" } },
        );
      }),
    );
    const user = await openPage();
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await user.click(screen.getByRole("checkbox", { name: "bare" }));
    await user.click(screen.getByRole("checkbox", { name: "harness" }));
    await user.click(screen.getByRole("checkbox", { name: "oracle" }));
    await user.click(screen.getByRole("button", { name: "Start" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Request refused");
    expect(alert).toHaveTextContent(message);
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toMatchObject({ arms: [{ name: "oracle" }] });
    expect(within(form()).queryByText(/^Started/)).not.toBeInTheDocument();
  });

  it("counts the jobs that the choice makes", async () => {
    const user = await openPage();
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await waitFor(() =>
      expect(within(form()).getByText(/This creates 2 jobs/)).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("checkbox", { name: "oracle" }));
    expect(
      within(form()).getByText(/This creates 3 jobs, one for each arm and model/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "bare" }));
    await user.click(screen.getByRole("checkbox", { name: "harness" }));
    expect(within(form()).getByText("This creates 1 job.")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "oracle" }));
    expect(within(form()).getByText("Pick at least one arm and one model.")).toBeInTheDocument();
  });

  it("starts one job per arm and model and shows them queued, in order", async () => {
    const stand = createJobStand();
    const user = await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await user.click(screen.getByRole("checkbox", { name: "oracle" }));
    const repeats = screen.getByLabelText("Repeats");
    await user.clear(repeats);
    await user.type(repeats, "2");
    await user.type(screen.getByLabelText("Notes"), "first try");
    await user.click(screen.getByRole("button", { name: "Start" }));

    expect(await within(form()).findByText("Started 3 jobs")).toBeInTheDocument();
    expect(stand.requests).toEqual([
      {
        set: { name: "demo-papers", version: "v1" },
        arms: [{ name: "bare" }, { name: "harness" }, { name: "oracle" }],
        models: ["fake-model"],
        repeats: 2,
        notes: "first try",
      },
    ]);
    const started = within(form()).getAllByRole("listitem");
    expect(started.map((item) => item.textContent)).toEqual([
      "bare on fake-modelRunning",
      "harness on fake-modelQueued1 job ahead",
      "oracle on fake-modelQueued2 jobs ahead",
    ]);
    // the jobs table has them too, with the state as a word
    const table = await screen.findByRole("table", { name: "Benchmark jobs, newest first" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]!).getByText("Queued")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Running")).toBeInTheDocument();
  });

  it("asks for an arm and a model before it calls the server, and says where the problem is", async () => {
    const stand = createJobStand();
    const user = await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await user.click(screen.getByRole("checkbox", { name: "bare" }));
    await user.click(screen.getByRole("checkbox", { name: "harness" }));
    await user.click(screen.getByRole("checkbox", { name: "fake-model (demo)" }));
    await user.click(screen.getByRole("button", { name: "Start" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The run cannot start yet:");
    expect(alert).toHaveTextContent("Pick at least one arm.");
    expect(alert).toHaveTextContent("Pick at least one model.");
    await waitFor(() => expect(alert).toHaveFocus());
    expect(stand.requests).toEqual([]);
  });

  it("refuses repeats outside 1 to 10", async () => {
    const stand = createJobStand();
    const user = await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    const repeats = screen.getByLabelText("Repeats");
    await user.clear(repeats);
    await user.type(repeats, "11");
    await user.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Repeats must be a whole number from 1 to 10.",
    );
    expect(repeats).toBeInvalid();
    expect(stand.requests).toEqual([]);
  });

  it("shows the words of the server when it refuses the run", async () => {
    server.use(
      http.post("*/api/v1/runs", () =>
        problem(503, "The answering model is not ready yet.", { "retry-after": "30" }),
      ),
    );
    const user = await openPage();
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await user.click(screen.getByRole("button", { name: "Start" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The service cannot do this now");
    expect(alert).toHaveTextContent("The answering model is not ready yet.");
    expect(alert).toHaveTextContent("Try again in 30 s.");
  });

  it("does not send a model that went down after it was chosen", async () => {
    const stand = createJobStand();
    const user = await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    const models: Model[] = CAPTURE.models;
    // the next reading of the models says the chosen one stopped answering
    server.use(
      http.get("*/api/v1/models", () =>
        HttpResponse.json(
          models.map((model) =>
            model.name === "fake-model" ? { ...model, available: false } : model,
          ),
        ),
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "fake-model (demo)" })).toBeDisabled(),
    );
    // it is not chosen any more, and the count does not include it
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "fake-model (demo)" })).not.toBeChecked(),
    );
    expect(within(form()).getByText("Pick at least one arm and one model.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick at least one model.");
    expect(stand.requests).toEqual([]);
  });

  it("sends one request when Start is clicked twice before the page can disable it", async () => {
    const stand = createJobStand();
    await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    const button = screen.getByRole("button", { name: "Start" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(await within(form()).findByText("Started 2 jobs")).toBeInTheDocument();
    expect(stand.requests).toHaveLength(1);
    expect(stand.jobs).toHaveLength(2);
  });

  it("follows the state of the started jobs", async () => {
    const stand = createJobStand();
    const user = await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await user.click(screen.getByRole("button", { name: "Start" }));
    await within(form()).findByText("Started 2 jobs");
    const started = () => within(form()).getAllByRole("listitem");
    expect(started().map((item) => item.textContent)).toEqual([
      "bare on fake-modelRunning",
      "harness on fake-modelQueued1 job ahead",
    ]);
    stand.tick();
    stand.tick();
    stand.tick();
    stand.tick();
    stand.tick();
    stand.tick();
    await waitFor(() =>
      expect(started().map((item) => item.textContent)).toEqual([
        "bare on fake-modelDone",
        "harness on fake-modelRunning",
      ]),
    );
  });
});

describe("the jobs", () => {
  it("lists the jobs with state, progress and a link to each", async () => {
    await openPage();
    const table = await screen.findByRole("table", { name: "Benchmark jobs, newest first" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(4);
    for (const row of rows) {
      expect(within(row).getByText("Done")).toBeInTheDocument();
      expect(within(row).getByText("12 of 12 answers")).toBeInTheDocument();
      expect(within(row).getByRole("link")).toHaveAttribute(
        "href",
        expect.stringMatching(/^\/benchmarks\/jobs\/[0-9a-z]{26}$/),
      );
    }
    for (const header of ["Job", "Question set", "State", "Progress", "Started by", "Created"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(screen.getByRole("status", { name: "" })).toBeDefined();
  });

  it("follows the progress of a job without a reload", async () => {
    const stand = createJobStand({ total: 3 });
    const user = await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await user.click(screen.getByRole("button", { name: "Start" }));
    const table = await screen.findByRole("table", { name: "Benchmark jobs, newest first" });
    expect(await within(table).findAllByText("0 of 3 answers")).toHaveLength(1);
    stand.tick();
    expect(await within(table).findByText("1 of 3 answers")).toBeInTheDocument();
    stand.tick();
    stand.tick();
    // the first job is done and the second goes on: both are read again
    await waitFor(() => expect(within(table).getByText("3 of 3 answers")).toBeInTheDocument());
    expect(within(table).getAllByText("Done")).toHaveLength(1);
    expect(within(table).getByText("Running")).toBeInTheDocument();
  });

  it("says how many jobs are open, in a polite live region", async () => {
    const stand = createJobStand({ total: 1 });
    const user = await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await user.click(screen.getByRole("button", { name: "Start" }));
    const jobs = screen.getByRole("region", { name: "Jobs" });
    expect(await within(jobs).findByText("2 jobs are running or waiting.")).toBeInTheDocument();
    stand.tick();
    expect(await within(jobs).findByText("1 job is running or waiting.")).toBeInTheDocument();
    stand.tick();
    expect(await within(jobs).findByText("0 jobs are running or waiting.")).toBeInTheDocument();
    expect(within(jobs).getByRole("status")).toBeInTheDocument();
  });

  it("shows a panel with Try again when the jobs cannot be read", async () => {
    let fail = true;
    server.use(
      http.get("*/api/v1/jobs", () =>
        fail ? problem(403, "The job table is closed to you.") : HttpResponse.json([]),
      ),
    );
    const user = await openPage();
    const jobs = await screen.findByRole("region", { name: "Jobs" });
    expect(await within(jobs).findByRole("alert")).toHaveTextContent(
      "The job table is closed to you.",
    );
    fail = false;
    await user.click(within(jobs).getByRole("button", { name: "Try again" }));
    expect(await within(jobs).findByText("No jobs yet. Start a run above.")).toBeInTheDocument();
  });
});

describe("the runs", () => {
  const table = async () => screen.findByRole("table", { name: "Runs of the benchmark" });
  const pick = (user: User, run: string) =>
    user.click(screen.getByRole("checkbox", { name: `Pick run ${run}` }));

  it("lists every run with its arm, model, answers and state", async () => {
    await openPage();
    const rows = within(await table())
      .getAllByRole("row")
      .slice(1);
    expect(rows).toHaveLength(4);
    const base = within(await table())
      .getAllByRole("row")
      .find((row) => row.getAttribute("data-run") === RUNS.base)!;
    expect(within(base).getByText("bare")).toBeInTheDocument();
    expect(within(base).getByText("fake-model")).toBeInTheDocument();
    expect(within(base).getByText("12 of 12")).toBeInTheDocument();
    expect(within(base).getByText("Done")).toBeInTheDocument();
    expect(within(base).getByText("demo-papers/v1")).toBeInTheDocument();
  });

  it("opens the results of one run and the comparison of two, base first", async () => {
    const user = await openPage();
    await table();
    expect(
      screen.getByText(/Pick one run to see its results, or two to compare them/),
    ).toBeInTheDocument();
    await pick(user, RUNS.base);
    expect(screen.getByRole("link", { name: "See results" })).toHaveAttribute(
      "href",
      `/benchmarks/compare?runs=${encodeURIComponent(RUNS.base)}`,
    );
    await pick(user, RUNS.harness);
    expect(screen.getByText(`Base: ${RUNS.base}. Variant: ${RUNS.harness}.`)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Compare" })).toHaveAttribute(
      "href",
      `/benchmarks/compare?runs=${encodeURIComponent(`${RUNS.base},${RUNS.harness}`)}`,
    );
    await user.click(screen.getByRole("button", { name: "Swap base and variant" }));
    expect(screen.getByText(`Base: ${RUNS.harness}. Variant: ${RUNS.base}.`)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Compare" })).toHaveAttribute(
      "href",
      `/benchmarks/compare?runs=${encodeURIComponent(`${RUNS.harness},${RUNS.base}`)}`,
    );
  });

  it("takes at most two runs", async () => {
    const user = await openPage();
    await table();
    await pick(user, RUNS.base);
    await pick(user, RUNS.harness);
    expect(screen.getByRole("checkbox", { name: `Pick run ${RUNS.oracle}` })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByRole("checkbox", { name: `Pick run ${RUNS.oracle}` })).toBeEnabled();
  });

  it("does not offer to compare runs of different sets", async () => {
    server.use(
      http.get("*/api/v1/runs", () =>
        HttpResponse.json(
          CAPTURE.runs.map((run, index) =>
            index === 1 ? { ...run, set_md5: "0".repeat(32) } : run,
          ),
        ),
      ),
    );
    const user = await openPage();
    await table();
    const second = CAPTURE.runs[1]!.run_id;
    await pick(user, CAPTURE.runs[0]!.run_id);
    await pick(user, second);
    expect(screen.getByText(/different sets or different versions of one/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Compare" })).not.toBeInTheDocument();
  });

  it("shows a run that is still going, not yet partial, and does not let it be scored", async () => {
    const stand = createJobStand({ total: 4 });
    const user = await openPage(stand);
    await screen.findByRole("checkbox", { name: "fake-model (demo)" });
    await user.click(screen.getByRole("button", { name: "Start" }));
    stand.tick();
    const runs = await table();
    const run = await within(runs).findByText("1 of 4");
    const row = run.closest("tr")!;
    expect(within(row).getByText("Running")).toBeInTheDocument();
    expect(within(row).queryByText("partial")).not.toBeInTheDocument();
    expect(within(row).getByRole("checkbox")).toBeDisabled();
  });

  it("says what to do when there is no run yet", async () => {
    server.use(http.get("*/api/v1/runs", () => HttpResponse.json([])));
    await openPage();
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
  });
});
