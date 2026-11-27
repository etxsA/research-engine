import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { CAPTURE, createJobStand, logPage, problem, type JobStand } from "@/test/benchmarks-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";
import { polling } from "./api";
import type { Job } from "./types";

preloadScreens(() => import("./JobPage"));

type User = ReturnType<typeof userEvent.setup>;

const originalPolling = { ...polling };

beforeEach(() => {
  polling.jobMs = 20;
  polling.idleMs = 40;
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

afterEach(() => {
  Object.assign(polling, originalPolling);
});

const DONE = CAPTURE.jobs[0]!;

/** Opens the page of a job and waits until the job is on it, unless the test is about a job that is not. */
async function openJob(id: string, loaded = true): Promise<User> {
  const user = userEvent.setup({ delay: null });
  renderApp(`/benchmarks/jobs/${id}`);
  await screen.findByRole("heading", { level: 1 });
  if (loaded) await screen.findByTestId("job-status");
  return user;
}

/** A stand with two runs of the closed-book arm and the harness: the first runs and the second waits. */
function twoJobs(total = 3) {
  const stand = createJobStand({ total });
  server.use(...stand.handlers);
  const [first, second] = stand.start({
    set: { name: "demo-papers", version: "v1" },
    arms: [{ name: "bare" }, { name: "harness" }],
    models: ["fake-model"],
    repeats: 1,
    notes: "a note",
  }) as [Job, Job];
  return { stand, first, second };
}

const status = () => screen.getByTestId("job-status");
const bar = () => screen.getByRole("progressbar", { name: "Answers done" });

describe("a finished job", () => {
  it("shows what was run, how far it went and where its results are", async () => {
    await openJob(DONE.id);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/^bare on fake-model \(/);
    expect(status()).toHaveTextContent("Done. 12 of 12 answers");
    expect(bar()).toHaveAttribute("aria-valuenow", "12");
    expect(bar()).toHaveAttribute("aria-valuemax", "12");
    const details = screen.getByRole("region", { name: "Details" });
    expect(within(details).getByText("Question set").nextSibling).toHaveTextContent(
      "demo-papers v1",
    );
    expect(within(details).getByText("Model").nextSibling).toHaveTextContent("fake-model");
    expect(within(details).getByText("Repeats").nextSibling).toHaveTextContent("2");
    expect(within(details).getByText("Notes").nextSibling).toHaveTextContent(
      "captured for the tests",
    );
    expect(within(details).getByText("Started by").nextSibling).toHaveTextContent("dani");
    expect(screen.getByRole("link", { name: "See results" })).toHaveAttribute(
      "href",
      `/benchmarks/compare?runs=${encodeURIComponent(DONE.run?.run_id ?? "")}`,
    );
    expect(screen.queryByRole("button", { name: "Cancel job" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the benchmarks" })).toHaveAttribute(
      "href",
      "/benchmarks",
    );
  });

  it("shows the log of the job in a region a keyboard can reach", async () => {
    await openJob(DONE.id);
    const log = await screen.findByRole("log", { name: "Job log" });
    expect(log).toHaveAttribute("tabindex", "0");
    await waitFor(() => expect(log).toHaveTextContent("done: 12 answers, 0 asks shared the GPU"));
    expect(log).toHaveTextContent("1/12 DP-001 repeat 1: answered");
  });

  it("says when the log is empty", async () => {
    server.use(
      http.get(
        "*/api/v1/jobs/:id/log",
        () => new HttpResponse("", { headers: { "x-log-next-offset": "0", "x-log-size": "0" } }),
      ),
    );
    await openJob(DONE.id);
    expect(await screen.findByText("The log is empty so far.")).toBeInTheDocument();
  });
});

describe("a job that is waiting or running", () => {
  it("says where a queued job stands and why it waits", async () => {
    const { stand, second } = twoJobs();
    stand.jobs[1]!.waiting_for = "The model server of fake-model does not answer.";
    await openJob(second.id);
    expect(status()).toHaveTextContent("Queued. 1 job ahead");
    expect(bar()).toHaveAttribute("aria-valuenow", "0");
    expect(
      screen.getByText(/The model server of fake-model does not answer\./),
    ).toBeInTheDocument();
  });

  it("follows the answers, the position and the log without a reload", async () => {
    const { stand, first, second } = twoJobs(3);
    await openJob(second.id);
    expect(status()).toHaveTextContent("Queued. 1 job ahead");
    stand.tick(first.id);
    stand.tick(first.id);
    stand.tick(first.id);
    // the first job is done: this one is next, then it starts
    await waitFor(() => expect(status()).toHaveTextContent("Running. 0 of 3 answers"));
    stand.tick();
    await waitFor(() => expect(status()).toHaveTextContent("Running. 1 of 3 answers"));
    expect(bar()).toHaveAttribute("aria-valuenow", "1");
    expect(screen.getByRole("progressbar").firstElementChild).toHaveStyle({ width: "33%" });
    const log = screen.getByRole("log", { name: "Job log" });
    await waitFor(() => expect(log).toHaveTextContent("1/3 answered"));
    stand.tick();
    stand.tick();
    await waitFor(() => expect(status()).toHaveTextContent("Done. 3 of 3 answers"));
    await waitFor(() => expect(log).toHaveTextContent("3/3 answered"));
    // each line once: the log was read from where it stopped
    expect(log.textContent?.match(/1\/3 answered/g)).toHaveLength(1);
    expect(screen.getByRole("link", { name: "See results" })).toBeInTheDocument();
  });

  it("reads the log from the byte where it stopped", async () => {
    const { stand, first } = twoJobs(3);
    const offsets: string[] = [];
    server.use(
      http.get("*/api/v1/jobs/:id/log", ({ request, params }) => {
        offsets.push(new URL(request.url).searchParams.get("offset") ?? "");
        const text = `${stand.log(String(params.id))}\n`;
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        return new HttpResponse(text.slice(offset), {
          headers: { "x-log-next-offset": String(text.length), "x-log-size": String(text.length) },
        });
      }),
    );
    await openJob(first.id);
    stand.tick();
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("1/3 answered"));
    expect(offsets[0]).toBe("0");
    await waitFor(() => expect(Number(offsets.at(-1))).toBeGreaterThan(0));
  });

  it("reads the log once more after the job settles, for the lines written as it ended", async () => {
    const { stand, first } = twoJobs(2);
    // the closing lines are not in the file yet while the job is seen running
    let frozen: string | null = null;
    server.use(
      http.get("*/api/v1/jobs/:id/log", ({ request, params }) => {
        const text = frozen ?? `${stand.log(String(params.id))}\n`;
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        const page = logPage(text, offset, text.length);
        return new HttpResponse(page.chunk, {
          headers: { "x-log-next-offset": String(page.next), "x-log-size": String(page.size) },
        });
      }),
      // they are there once the job is read as settled
      http.get("*/api/v1/jobs/:id", ({ params }) => {
        const job = stand.jobs.find((entry) => entry.id === params.id);
        if (!job) return problem(404, "No such job.");
        const body = structuredClone(job);
        if (job.settled) frozen = null;
        return HttpResponse.json(body);
      }),
    );
    await openJob(first.id);
    stand.tick(first.id);
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("1/2 answered"));
    frozen = `${stand.log(first.id)}\n`;
    stand.tick(first.id);
    await waitFor(() => expect(status()).toHaveTextContent("Done. 2 of 2 answers"));
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("2/2 answered"));
    expect(screen.getByRole("log").textContent?.trimEnd().endsWith("done")).toBe(true);
  });

  it("does not close the log on the read made at once when the job settles", async () => {
    const { stand, first } = twoJobs(2);
    // the runner settles the job, and the closing line reaches the file a moment later: it is
    // not there for the first read after the job is seen settled, only for the one after that
    let frozen: string | null = null;
    let settledSeen = false;
    let readsAfter = 0;
    server.use(
      http.get("*/api/v1/jobs/:id/log", ({ request, params }) => {
        if (settledSeen) {
          readsAfter += 1;
          if (readsAfter >= 2) frozen = null;
        }
        const text = frozen ?? `${stand.log(String(params.id))}\n`;
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        const page = logPage(text, offset, text.length);
        return new HttpResponse(page.chunk, {
          headers: { "x-log-next-offset": String(page.next), "x-log-size": String(page.size) },
        });
      }),
      http.get("*/api/v1/jobs/:id", ({ params }) => {
        const job = stand.jobs.find((entry) => entry.id === params.id);
        if (!job) return problem(404, "No such job.");
        if (job.settled) settledSeen = true;
        return HttpResponse.json(structuredClone(job));
      }),
    );
    await openJob(first.id);
    stand.tick(first.id);
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("1/2 answered"));
    frozen = `${stand.log(first.id)}\n`;
    stand.tick(first.id);
    await waitFor(() => expect(status()).toHaveTextContent("Done. 2 of 2 answers"));
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("2/2 answered"));
    expect(screen.getByRole("log").textContent?.trimEnd().endsWith("done")).toBe(true);
  });

  it("stops reading the log once the job is settled and a read found nothing new", async () => {
    const { stand, first } = twoJobs(1);
    stand.tick(first.id);
    await openJob(first.id);
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("1/1 answered"));
    await waitFor(() => expect(stand.logReads.length).toBeGreaterThanOrEqual(2));
    const reads = stand.logReads.length;
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(stand.logReads).toHaveLength(reads);
  });

  it("follows the pages of a log longer than one read", async () => {
    const stand = createJobStand({ total: 1, logBytes: 300 });
    server.use(...stand.handlers);
    const [first] = stand.start({
      set: { name: "demo-papers", version: "v1" },
      arms: [{ name: "bare" }],
      models: ["fake-model"],
      repeats: 1,
      notes: "",
    }) as [Job];
    for (let line = 1; line <= 80; line += 1) stand.write(first.id, `extra line ${line}`);
    stand.tick(first.id);
    // no second poll comes in time: the whole log has to arrive in the first query
    polling.jobMs = 60_000;
    await openJob(first.id);
    const log = screen.getByRole("log");
    await waitFor(() => expect(log).toHaveTextContent("1/1 answered"));
    expect(log.textContent?.match(/extra line \d+/g)).toHaveLength(80);
    expect(log).toHaveTextContent("extra line 1 ");
    expect(log).toHaveTextContent("extra line 80 ");
    // each page starts where the one before stopped, and there were several of them
    expect(stand.logReads[0]).toBe(0);
    expect(stand.logReads).toEqual([...stand.logReads].sort((a, b) => a - b));
    expect(new Set(stand.logReads).size).toBeGreaterThan(3);
  });

  it("starts the log again when the file got shorter", async () => {
    const { first } = twoJobs();
    let calls = 0;
    server.use(
      http.get("*/api/v1/jobs/:id/log", ({ request }) => {
        calls += 1;
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        const text = calls === 1 ? "old old old old old\n" : "new\n";
        return new HttpResponse(text.slice(offset), {
          headers: { "x-log-next-offset": String(text.length), "x-log-size": String(text.length) },
        });
      }),
    );
    await openJob(first.id);
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("new"));
    expect(screen.getByRole("log")).not.toHaveTextContent("old");
  });
});

describe("cancelling and resuming", () => {
  it("asks first, and keeps the job when the answer is no", async () => {
    const { first } = twoJobs();
    const user = await openJob(first.id);
    await user.click(screen.getByRole("button", { name: "Cancel job" }));
    const dialog = await screen.findByRole("dialog", { name: "Cancel this job?" });
    expect(dialog).toHaveTextContent("It stops before the next question.");
    await user.click(within(dialog).getByRole("button", { name: "Keep it" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(status()).toHaveTextContent("Running");
    expect(screen.getByRole("button", { name: "Cancel job" })).toHaveFocus();
  });

  it("goes to cancelling at once, to cancelled after the question in flight, and keeps the partial run", async () => {
    const { stand, first } = twoJobs(4);
    const user = await openJob(first.id);
    stand.tick(first.id);
    await waitFor(() => expect(status()).toHaveTextContent("Running. 1 of 4 answers"));
    await user.click(screen.getByRole("button", { name: "Cancel job" }));
    await user.click(await screen.findByRole("button", { name: "Yes, cancel" }));
    await waitFor(() => expect(status()).toHaveTextContent("Cancelling. 1 of 4 answers"));
    expect(status()).toHaveTextContent("It stops before the next question.");
    expect(screen.queryByRole("button", { name: "Cancel job" })).not.toBeInTheDocument();
    stand.tick(first.id);
    await waitFor(() => expect(status()).toHaveTextContent("Cancelled. 2 of 4 answers"));
    // the partial run is still there to read
    expect(screen.getByRole("link", { name: "See results" })).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent(first.run?.run_id ?? "")),
    );
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("resumes a cancelled job, behind the job that took its place, and runs it to its end", async () => {
    const { stand, first, second } = twoJobs(3);
    const user = await openJob(first.id);
    await user.click(screen.getByRole("button", { name: "Cancel job" }));
    await user.click(await screen.findByRole("button", { name: "Yes, cancel" }));
    stand.tick(first.id);
    await waitFor(() => expect(status()).toHaveTextContent("Cancelled. 1 of 3 answers"));
    await user.click(screen.getByRole("button", { name: "Resume" }));
    // the second job took the lane: this one waits its turn, and the page reads it again by itself
    await waitFor(() => expect(status()).toHaveTextContent("Queued. 1 job ahead"));
    stand.tick(second.id);
    stand.tick(second.id);
    stand.tick(second.id);
    await waitFor(() => expect(status()).toHaveTextContent("Running. 1 of 3 answers"));
    stand.tick(first.id);
    stand.tick(first.id);
    await waitFor(() => expect(status()).toHaveTextContent("Done. 3 of 3 answers"));
  });

  it("sends one resume when the button is clicked twice before the page can disable it", async () => {
    const { stand, first } = twoJobs(3);
    stand.jobs[0]!.state = "cancelled";
    stand.jobs[0]!.settled = true;
    stand.jobs[0]!.progress.done = 1;
    await openJob(first.id);
    const button = screen.getByRole("button", { name: "Resume" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(status()).toHaveTextContent(/Running|Queued/));
    expect(stand.actions).toEqual([`resume:${first.id}`]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("cancels a queued job at once", async () => {
    const { second } = twoJobs();
    const user = await openJob(second.id);
    await user.click(screen.getByRole("button", { name: "Cancel job" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("It leaves the line and nothing is run.");
    await user.click(within(dialog).getByRole("button", { name: "Yes, cancel" }));
    await waitFor(() => expect(status()).toHaveTextContent("Cancelled. 0 of 3 answers"));
  });

  it("resumes an interrupted job", async () => {
    const { stand, first } = twoJobs();
    stand.jobs[0]!.state = "interrupted";
    stand.jobs[0]!.settled = true;
    const user = await openJob(first.id);
    expect(status()).toHaveTextContent("Interrupted");
    await user.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(status()).toHaveTextContent("Running"));
  });

  it("shows the words of the server when the job cannot be cancelled", async () => {
    const { first } = twoJobs();
    server.use(
      http.post("*/api/v1/jobs/:id/cancel", () => problem(409, "Too late: the run just ended.")),
    );
    const user = await openJob(first.id);
    await user.click(screen.getByRole("button", { name: "Cancel job" }));
    await user.click(await screen.findByRole("button", { name: "Yes, cancel" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not possible now");
    expect(alert).toHaveTextContent("Too late: the run just ended.");
  });

  it("offers no buttons to a member who does not own the job", async () => {
    const { first } = twoJobs();
    server.use(
      http.get("*/api/v1/auth/me", () =>
        HttpResponse.json({
          username: "dani",
          display_name: "Dani Prueba",
          role: "member",
          must_change_password: false,
          language: "en",
        }),
      ),
    );
    await openJob(first.id);
    expect(
      await screen.findByText("Only ana or an administrator can cancel or resume this job."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel job" })).not.toBeInTheDocument();
  });

  it("lets an administrator cancel a job of someone else", async () => {
    const { first } = twoJobs();
    server.use(
      http.get("*/api/v1/auth/me", () =>
        HttpResponse.json({
          username: "root",
          display_name: "Admin",
          role: "admin",
          must_change_password: false,
          language: "en",
        }),
      ),
    );
    await openJob(first.id);
    expect(await screen.findByRole("button", { name: "Cancel job" })).toBeInTheDocument();
  });
});

describe("a job that failed or is not there", () => {
  it("shows the error of a failed job", async () => {
    const { stand, first } = twoJobs();
    stand.jobs[0]!.state = "failed";
    stand.jobs[0]!.settled = true;
    stand.jobs[0]!.error = { kind: "disk", message: "The run file could not be written." };
    await openJob(first.id);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The job failed");
    expect(alert).toHaveTextContent("The run file could not be written.");
    expect(status()).toHaveTextContent("Failed");
  });

  it("says so for a job that does not exist", async () => {
    await openJob("01m5yz00000000000000000000", false);
    expect(await screen.findByRole("alert")).toHaveTextContent("No such job");
  });

  it("says so for an address that is not a job id, without asking the server", async () => {
    let asked = false;
    server.use(
      http.get("*/api/v1/jobs/:id", () => {
        asked = true;
        return problem(404, "No such job.");
      }),
    );
    await openJob("nope", false);
    expect(await screen.findByRole("alert")).toHaveTextContent("This job does not exist");
    expect(asked).toBe(false);
  });

  it("shows a panel with Try again when the job cannot be read", async () => {
    let fail = true;
    server.use(
      http.get("*/api/v1/jobs/:id", () =>
        fail ? problem(403, "Not yours to see.") : HttpResponse.json(DONE),
      ),
    );
    const user = await openJob(DONE.id, false);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not yours to see.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(status()).toHaveTextContent("Done"));
  });
});

describe("a scoring", () => {
  it("shows the runs it scored and opens the results", async () => {
    await openJob(CAPTURE.scoreId);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/^Scoring of 4 runs/);
    const link = await screen.findByRole("link", { name: "Open the results" });
    const url = new URL(link.getAttribute("href") ?? "", "http://x");
    expect(url.pathname).toBe("/benchmarks/compare");
    expect(url.searchParams.get("score")).toBe(CAPTURE.scoreId);
    expect(url.searchParams.get("runs")).toBe(
      CAPTURE.score.summary.pairs
        .slice(0, 1)
        .flatMap((pair) => [pair.base_run, pair.var_run])
        .join(","),
    );
  });
});

type Stand = JobStand;
export type { Stand };
