import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { vi } from "vitest";
import { ANA } from "@/test/auth-mock";
import { answerOf, capturedAsk, CAPTURE, docIdOf, problem } from "@/test/ask-api";
import { FakeEventSource, replay } from "@/test/event-source";
import { renderApp } from "@/test/render";
import { authMock, server, STATUS_BODY } from "@/test/server";
import { installFakeAudio, type FakeAudio } from "@/test/audio";
import { installFakeMic, type FakeMic } from "@/test/mic";
import { installFakeSpeech, voice } from "@/test/speech";
import { serveSynthesis } from "@/test/synthesis";
import { startReading, stopReading } from "./reader";
import { useAskStore } from "./store";
import type { AskResponse } from "./types";

type User = ReturnType<typeof userEvent.setup>;

const TRANSMON = answerOf("transmon");
const TELEPORT = answerOf("teleportation");
const TRANSMON_DOC = docIdOf("transmon");
const TELEPORT_DOC = docIdOf("teleportation");

interface Seen {
  bodies: Record<string, unknown>[];
}

/** The API takes questions: `POST /asks` answers 202 and a test plays the stream by hand. */
function takeAsks(accepted = { ask_id: "a1", position: 0 }): Seen {
  const seen: Seen = { bodies: [] };
  server.use(
    http.post("*/api/v1/asks", async ({ request }) => {
      seen.bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(accepted, { status: 202 });
    }),
  );
  return seen;
}

function withStatus(ask: Record<string, unknown>) {
  server.use(
    http.get("*/api/v1/status", () =>
      HttpResponse.json({ ...STATUS_BODY, ask: { ...STATUS_BODY.ask, ...ask } }),
    ),
  );
}

async function openAsk(): Promise<User> {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
  renderApp("/ask");
  await screen.findByRole("heading", { name: "Ask", level: 1 });
  return userEvent.setup();
}

async function ask(user: User, question: string) {
  await user.type(screen.getByLabelText("Your question"), question);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
}

function serve(play: (source: FakeEventSource) => void) {
  act(() => play(FakeEventSource.last));
}

/** Asks a question and plays the recorded events of the answer. */
async function answered(user: User, name: string, question = "A question") {
  takeAsks();
  await ask(user, question);
  serve((source) => replay(source, capturedAsk(name).events));
}

beforeEach(() => {
  useAskStore.getState().reset();
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sending a question (A1)", () => {
  it("cannot send an empty question", async () => {
    const user = await openAsk();
    const seen = takeAsks();
    const send = screen.getByRole("button", { name: "Ask" });
    expect(send).toBeDisabled();
    expect(screen.getByText("Write a question to send it.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Your question"), "   \n ");
    expect(send).toBeDisabled();
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(seen.bodies).toEqual([]);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("sends with the button and shows the phases in order", async () => {
    const user = await openAsk();
    const seen = takeAsks({ ask_id: "a1", position: 1 });
    await user.type(screen.getByLabelText("Your question"), "  What limits T1 in transmons?  ");
    expect(screen.queryByText("Write a question to send it.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ask" }));

    expect(seen.bodies).toEqual([
      {
        question: "What limits T1 in transmons?",
        use_index: true,
        bm25: null,
        whole_blocks: null,
        return_contexts: true,
      },
    ]);
    const steps = await screen.findByRole("list", { name: "Progress of your question" });
    const current = () =>
      within(steps)
        .getByText(/in progress/)
        .closest("li");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.last.url).toBe("/api/v1/asks/a1/events");
    expect(current()).toHaveTextContent("Waiting for the model servers. 1 question ahead of yours");

    serve((source) => source.emit("queued", { ask_id: "a1", position: 0 }));
    expect(current()).toHaveTextContent("Yours is next");
    serve((source) => source.emit("retrieving", { ask_id: "a1" }));
    expect(current()).toHaveTextContent("Searching the papers");
    expect(within(steps).getAllByRole("listitem")[0]).toHaveTextContent("(done)");
    serve((source) => source.emit("answering", { ask_id: "a1" }));
    expect(current()).toHaveTextContent("Writing the answer");

    serve((source) => source.emit("answer", TRANSMON));
    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
    expect(
      screen.queryByRole("list", { name: "Progress of your question" }),
    ).not.toBeInTheDocument();
  });

  it("sends with Ctrl+Enter, and with Cmd+Enter", async () => {
    const user = await openAsk();
    const seen = takeAsks();
    await user.type(screen.getByLabelText("Your question"), "First{Control>}{Enter}{/Control}");
    await waitFor(() => expect(seen.bodies).toHaveLength(1));
    serve((source) => source.emit("answer", TRANSMON));
    await screen.findByRole("heading", { name: "Answer" });

    expect(screen.getByLabelText("Your question")).toHaveValue("");
    await user.type(screen.getByLabelText("Your question"), "Second{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(seen.bodies).toHaveLength(2));
    expect(seen.bodies[1]?.question).toBe("Second");
  });

  it("empties the question box once the question is sent, and Edit and ask again puts it back", async () => {
    const user = await openAsk();
    takeAsks();
    const box = screen.getByLabelText("Your question");
    await user.click(screen.getByRole("button", { name: "Options for this question" }));
    await user.click(screen.getByRole("radio", { name: "Never" }));
    await user.keyboard("{Escape}");
    await ask(user, "First question");
    expect(box).toHaveValue("");
    expect(screen.getByText("0 of 4,000 characters")).toBeInTheDocument();
    serve((source) => replay(source, capturedAsk("transmon").events));
    await screen.findByRole("heading", { name: "Answer" });

    // typing the next question starts from nothing
    await user.type(box, "Second question");
    expect(box).toHaveValue("Second question");
    await user.clear(box);

    // the options change after sending; Edit and ask again brings back the ones the question had
    await user.click(screen.getByRole("button", { name: /^Options for this question/ }));
    await user.click(screen.getByRole("radio", { name: "Always" }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Edit and ask again" }));
    expect(box).toHaveValue("First question");
    expect(box).toHaveFocus();
    await user.click(screen.getByRole("button", { name: /^Options for this question/ }));
    expect(screen.getByRole("radio", { name: "Never" })).toBeChecked();
  });

  it("lets a refused question be edited and asked again, with its options", async () => {
    const user = await openAsk();
    server.use(http.post("*/api/v1/asks", () => problem(422, "The question has no words.")));
    await user.click(screen.getByRole("button", { name: "Options for this question" }));
    await user.click(screen.getByRole("radio", { name: "Never" }));
    await user.keyboard("{Escape}");
    await user.type(screen.getByLabelText("Your question"), "A question");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The question was refused");
    const box = screen.getByLabelText("Your question");
    expect(box).toHaveValue("");

    await user.click(within(alert).getByRole("button", { name: "Edit and ask again" }));
    expect(box).toHaveValue("A question");
    expect(box).toHaveFocus();
    await user.click(screen.getByRole("button", { name: /^Options for this question/ }));
    expect(screen.getByRole("radio", { name: "Never" })).toBeChecked();
  });

  it("empties the question box when Ask anyway sends the question", async () => {
    withStatus({ state: "unavailable", reason: "The index could not be opened." });
    takeAsks();
    const user = await openAsk();
    await screen.findByText("The engine reports a problem");
    await user.type(screen.getByLabelText("Your question"), "A question");
    await user.click(screen.getByRole("button", { name: "Ask anyway" }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    expect(screen.getByLabelText("Your question")).toHaveValue("");
  });

  it("leaves the box alone when a failed question is tried again", async () => {
    const user = await openAsk();
    const seen = takeAsks();
    await ask(user, "simulate-unavailable please");
    serve((source) => replay(source, capturedAsk("unavailable").events));
    const alert = await screen.findByRole("alert");
    await user.type(screen.getByLabelText("Your question"), "The next one");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(seen.bodies).toHaveLength(2));
    expect(seen.bodies[1]?.question).toBe("simulate-unavailable please");
    expect(screen.getByLabelText("Your question")).toHaveValue("The next one");
  });

  it("sends one question at a time", async () => {
    const user = await openAsk();
    const seen = takeAsks();
    await ask(user, "Long question");
    expect(screen.getByRole("button", { name: "Asking" })).toBeDisabled();
    expect(
      screen.getByText("A question is being answered. Wait for it or cancel it to send another."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toHaveAccessibleDescription(
      /A question is being answered\. Wait for it/,
    );
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(seen.bodies).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Options for this question" })).toBeDisabled();
  });

  it("shows the answer with the text, the warnings and one chip per citation", async () => {
    const user = await openAsk();
    await answered(user, "surface code", "surface code threshold");
    const answer = answerOf("surface code");
    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
    expect(screen.getByText(/rotated surface code of distance/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(answer.warnings[0]!);

    const sources = screen.getByRole("region", { name: "Sources" });
    const chips = within(sources).getAllByRole("button");
    expect(chips).toHaveLength(answer.citations.length);
    answer.citations.forEach((citation, index) => {
      const chip = chips[index]!;
      expect(chip).toHaveTextContent(citation.doc_id.slice(0, 16));
      expect(chip).toHaveTextContent(
        citation.last_page > citation.first_page
          ? `pages ${citation.first_page}-${citation.last_page}`
          : `page ${citation.first_page}`,
      );
    });
    expect(screen.getByText(/in all\)\.$/)).toBeInTheDocument();
  });

  it("names each chip with the title of its paper once it is known", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    const sources = await screen.findByRole("region", { name: "Sources" });
    expect(
      await within(sources).findAllByRole("button", {
        name: /Coherence limits of fixed-frequency transmon qubits/,
      }),
    ).not.toHaveLength(0);
  });

  it("renders the math of the answer", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    await screen.findByRole("heading", { name: "Answer" });
    expect(document.querySelector(".markdown .katex")).not.toBeNull();
    expect(document.querySelector(".markdown .katex-mathml")).not.toBeNull();
  });

  it("says when an answer cites nothing", async () => {
    const user = await openAsk();
    await answered(user, "no evidence", "How do octopuses taste?");
    expect(await screen.findByText("This answer cites no paper.")).toBeInTheDocument();
    expect(screen.getByText("No passages came with this answer.")).toBeInTheDocument();
  });

  it("keeps the answer in the text away from raw HTML, links and images that are not web links", async () => {
    const user = await openAsk();
    takeAsks();
    await ask(user, "Q");
    const hostile: AskResponse = {
      ...TRANSMON,
      citations: [],
      contexts: [],
      answer:
        "<script>window.__pwned = true</script>\n\n[bad](javascript:alert(1)) [good](https://example.org/x) ![pic](https://example.org/p.png)",
    };
    serve((source) => source.emit("answer", hostile));
    await screen.findByRole("heading", { name: "Answer" });
    expect(document.querySelector(".markdown script")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
    expect(document.querySelector(".markdown img")).toBeNull();
    expect(screen.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
    const good = screen.getByRole("link", { name: "good" });
    expect(good).toHaveAttribute("href", "https://example.org/x");
    expect(good).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.getByText("[pic]")).toBeInTheDocument();
  });

  it("makes a button of each citation inside a parenthesis that joins several", async () => {
    const user = await openAsk();
    takeAsks();
    await ask(user, "Q");
    const [first, second] = [
      { ...TRANSMON.citations[0]!, key: "aaaaaaaaaaaaaaaa pages 3-4", first_page: 3, last_page: 4 },
      { ...TRANSMON.citations[0]!, key: "bbbbbbbbbbbbbbbb pages 1-1", first_page: 1, last_page: 1 },
    ];
    const joined: AskResponse = {
      ...TRANSMON,
      contexts: [],
      citations: [first, second],
      answer: `Both agree (${first.key}, ${second.key}).`,
    };
    serve((source) => source.emit("answer", joined));
    await screen.findByRole("heading", { name: "Answer" });
    const inline = [...document.querySelectorAll<HTMLElement>(".markdown button")];
    expect(inline.map((button) => button.textContent?.slice(0, 26))).toEqual([
      first.key,
      second.key,
    ]);
    expect(document.querySelector(".markdown")).toHaveTextContent(
      `Both agree (${first.key}, ${second.key}).`,
    );
  });

  it("announces each step to a screen reader", async () => {
    const user = await openAsk();
    // Qupi has its own live line; this one is the screen's.
    const live = screen
      .getAllByRole("status")
      .find((node) => node.className.includes("sr-only") && !node.textContent.startsWith("Qupi"))!;
    takeAsks();
    await ask(user, "Q");
    expect(live).toHaveTextContent("Waiting for the model servers");
    serve((source) => source.emit("retrieving", {}));
    expect(live).toHaveTextContent("Searching the papers");
    serve((source) => source.emit("answering", {}));
    expect(live).toHaveTextContent("Writing the answer");
    serve((source) => source.emit("answer", TRANSMON));
    expect(live).toHaveTextContent("Answer ready");
  });
});

describe("reading a cited paper (A2)", () => {
  it("opens the paper at the cited page from a chip", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    const sources = await screen.findByRole("region", { name: "Sources" });
    const citation = TRANSMON.citations[0]!;
    const chip = within(sources).getAllByRole("button")[0]!;
    await user.click(chip);

    const dialog = await screen.findByRole("dialog", {
      name: "Coherence limits of fixed-frequency transmon qubits (synthetic)",
    });
    const page = CAPTURE.pages[`${TRANSMON_DOC}:${citation.first_page}`] as {
      pages: number;
      markdown: string;
    };
    expect(
      await within(dialog).findByText(`Page ${citation.first_page} of ${page.pages}`),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        `Cited: ${citation.last_page > citation.first_page ? `pages ${citation.first_page}-${citation.last_page}` : `page ${citation.first_page}`}`,
      ),
    ).toBeInTheDocument();
    const text = within(dialog).getByRole("region", { name: "Text of the page" });
    const heading = /^#+\s*(.+)$/m.exec(page.markdown)?.[1];
    expect(heading).toBeTruthy();
    expect(await within(text).findByRole("heading", { name: heading! })).toBeInTheDocument();
    await waitFor(() => expect(text.querySelector(".katex")).not.toBeNull());
  });

  it("opens the paper from a citation inside the answer", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    await screen.findByRole("heading", { name: "Answer" });
    const inline = document.querySelector<HTMLElement>(".markdown button[data-opener]");
    expect(inline).not.toBeNull();
    await user.click(inline!);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("names the paper in the accessible name of a citation inside the answer", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    await screen.findByRole("heading", { name: "Answer" });
    const title = "Coherence limits of fixed-frequency transmon qubits (synthetic)";
    await waitFor(() => {
      const inline = document.querySelector<HTMLElement>(".markdown button[data-opener]");
      expect(inline).toHaveAccessibleName(new RegExp(title.replace(/[()]/g, "\\$&")));
    });
  });

  describe("when the citation names the paper by its 16 printed characters", () => {
    /** The API completes the id: its page and paper carry the manifest's full one. */
    async function openByShortId(user: User, page = TRANSMON.citations[0]!.first_page) {
      takeAsks();
      await ask(user, "Q");
      const full = TRANSMON.citations[0]!;
      const short = full.doc_id.slice(0, 16);
      const seenIds: string[] = [];
      server.use(
        http.get("*/api/v1/documents/:docId/pages/:page", ({ params }) => {
          seenIds.push(String(params.docId));
          const found = CAPTURE.pages[`${full.doc_id}:${String(params.page)}`];
          return found ? HttpResponse.json(found) : problem(404, "No such page.");
        }),
        http.get("*/api/v1/documents/:docId", () =>
          HttpResponse.json(CAPTURE.documents[full.doc_id] as object),
        ),
      );
      const response: AskResponse = {
        ...TRANSMON,
        citations: [{ ...full, doc_id: short, first_page: page, last_page: page }],
      };
      serve((source) => source.emit("answer", response));
      const sources = await screen.findByRole("region", { name: "Sources" });
      await user.click(within(sources).getAllByRole("button")[0]!);
      const dialog = await screen.findByRole("dialog");
      return { dialog, short, seenIds };
    }

    it("shows the text of the page, not a skeleton for ever", async () => {
      const user = await openAsk();
      const { dialog, short, seenIds } = await openByShortId(user);
      const text = within(dialog).getByRole("region", { name: "Text of the page" });
      await waitFor(() => expect(text.querySelector(".markdown")).not.toBeNull());
      expect(text.querySelector("[data-slot=skeleton]")).toBeNull();
      expect(seenIds).toContain(short);
      expect(within(dialog).getByRole("status")).toHaveTextContent(/Page \d+ of \d+/);
    });

    it("moves through the pages of the paper", async () => {
      const user = await openAsk();
      const { dialog } = await openByShortId(user);
      const first = TRANSMON.citations[0]!.first_page;
      const text = within(dialog).getByRole("region", { name: "Text of the page" });
      await waitFor(() => expect(text.querySelector(".markdown")).not.toBeNull());
      await user.click(within(dialog).getByRole("button", { name: "Next page" }));
      await within(dialog).findByText(new RegExp(`Page ${first + 1} of`));
      await waitFor(() => expect(text.querySelector(".markdown")).not.toBeNull());
      expect(text.querySelector("[data-slot=skeleton]")).toBeNull();
    });
  });

  it("keeps the page number of the text on screen while the next page loads", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    const first = TRANSMON.citations[0]!.first_page;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    server.use(
      http.get("*/api/v1/documents/:docId/pages/:page", async ({ params }) => {
        if (Number(params.page) === first + 1) await gate;
        return HttpResponse.json(CAPTURE.pages[`${String(params.docId)}:${String(params.page)}`]);
      }),
    );
    const sources = await screen.findByRole("region", { name: "Sources" });
    await user.click(within(sources).getAllByRole("button")[0]!);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(new RegExp(`Page ${first} of`));
    await user.click(within(dialog).getByRole("button", { name: "Next page" }));
    const text = within(dialog).getByRole("region", { name: "Text of the page" });
    await waitFor(() => expect(text).toHaveAttribute("aria-busy", "true"));
    expect(within(dialog).getByRole("status")).toHaveTextContent(new RegExp(`Page ${first} of`));
    release();
    await within(dialog).findByText(new RegExp(`Page ${first + 1} of`));
  });

  it("moves to the next and the previous page", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    const sources = await screen.findByRole("region", { name: "Sources" });
    await user.click(within(sources).getAllByRole("button")[0]!);
    const dialog = await screen.findByRole("dialog");
    const first = TRANSMON.citations[0]!.first_page;
    const total = (CAPTURE.pages[`${TRANSMON_DOC}:${first}`] as { pages: number }).pages;
    await within(dialog).findByText(`Page ${first} of ${total}`);

    await user.click(within(dialog).getByRole("button", { name: "Next page" }));
    expect(await within(dialog).findByText(`Page ${first + 1} of ${total}`)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Previous page" }));
    expect(await within(dialog).findByText(`Page ${first} of ${total}`)).toBeInTheDocument();

    const last = total;
    while ((useAskStore.getState().source?.page ?? 0) < last) {
      await user.click(within(dialog).getByRole("button", { name: "Next page" }));
    }
    await within(dialog).findByText(`Page ${last} of ${total}`);
    expect(within(dialog).getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("does not offer a previous page before the first", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    useAskStore
      .getState()
      .openSource({ docId: TRANSMON_DOC, page: 1, first: 1, last: 1, opener: "none" });
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/Page 1 of/);
    expect(within(dialog).getByRole("button", { name: "Previous page" })).toBeDisabled();
  });

  it("says section instead of page for a LaTeX source", async () => {
    const user = await openAsk();
    await answered(user, "teleportation", "teleportation circuit");
    const sources = await screen.findByRole("region", { name: "Sources" });
    const chip = within(sources).getAllByRole("button")[0]!;
    await waitFor(() => expect(chip).toHaveTextContent(/section/));
    await user.click(chip);
    const dialog = await screen.findByRole("dialog");
    const first = TELEPORT.citations[0]!.first_page;
    expect(
      await within(dialog).findByText(new RegExp(`Section ${first} of 3`)),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Next section" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Previous section" })).toBeInTheDocument();
    expect(TELEPORT_DOC).toBe(TELEPORT.citations[0]!.doc_id);
  });

  it("closes with Escape and gives focus back to the citation", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    const sources = await screen.findByRole("region", { name: "Sources" });
    const chip =
      within(sources).getAllByRole("button")[1] ?? within(sources).getAllByRole("button")[0]!;
    await user.click(chip);
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(chip).toHaveFocus();
  });

  it("closes with its button and gives focus back to an inline citation", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    await screen.findByRole("heading", { name: "Answer" });
    const inline = document.querySelector<HTMLElement>(".markdown button[data-opener]")!;
    await user.click(inline);
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(inline).toHaveFocus();
  });

  it("sends focus to the question box when the citation is gone", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    const sources = await screen.findByRole("region", { name: "Sources" });
    await user.click(within(sources).getAllByRole("button")[0]!);
    await screen.findByRole("dialog");
    act(() => useAskStore.getState().reset());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Your question")).toHaveFocus();
  });

  it("says when the page is not in the paper, and offers no retry", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    useAskStore
      .getState()
      .openSource({ docId: TRANSMON_DOC, page: 99, first: 99, last: 99, opener: "none" });
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("This page is not in the paper.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("offers to try again when the page cannot be loaded", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    let calls = 0;
    server.use(
      http.get("*/api/v1/documents/:docId/pages/:page", ({ params }) => {
        calls += 1;
        if (calls === 1) return problem(503, "Busy.");
        return HttpResponse.json(CAPTURE.pages[`${String(params.docId)}:${String(params.page)}`]);
      }),
    );
    useAskStore
      .getState()
      .openSource({ docId: TRANSMON_DOC, page: 1, first: 1, last: 1, opener: "none" });
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("The page could not be loaded.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByText(/Page 1 of/)).toBeInTheDocument();
  });

  it("says when a short id names more than one paper", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    server.use(
      http.get("*/api/v1/documents/:docId/pages/:page", () => problem(409, "Two match.")),
      http.get("*/api/v1/documents/:docId", () => problem(409, "Two match.")),
    );
    useAskStore
      .getState()
      .openSource({ docId: "0123456789abcdef", page: 1, first: 1, last: 1, opener: "none" });
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText("That short id names more than one paper."),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "Paper" })).toBeInTheDocument();
  });
});

describe("what the model read (A3)", () => {
  it("is collapsed until asked for", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    const toggle = await screen.findByRole("button", { name: "Show the passages" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Passage 1")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: `What the model read (${TRANSMON.contexts.length} passages)`,
      }),
    ).toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getByRole("button", { name: "Hide the passages" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    const items = within(screen.getByRole("list", { name: /What the model read/ })).getAllByRole(
      "listitem",
    );
    expect(items).toHaveLength(TRANSMON.contexts.length);
    TRANSMON.contexts.forEach((context, index) => {
      expect(items[index]).toHaveTextContent(`Passage ${index + 1}`);
      expect(items[index]).toHaveTextContent(context.doc_id.slice(0, 16));
    });

    await user.click(screen.getByRole("button", { name: "Hide the passages" }));
    expect(screen.queryByText("Passage 1")).not.toBeInTheDocument();
  });

  it("marks a passage from the reference list and shows its score", async () => {
    const user = await openAsk();
    takeAsks();
    await ask(user, "Q");
    const withMarks: AskResponse = {
      ...TRANSMON,
      contexts: TRANSMON.contexts.map((context, index) => ({
        ...context,
        is_reference: index === 0,
        score: index === 0 ? 0.8123 : null,
      })),
    };
    serve((source) => source.emit("answer", withMarks));
    await user.click(await screen.findByRole("button", { name: "Show the passages" }));
    const items = screen
      .getAllByRole("listitem")
      .filter((item) => /Passage \d/.test(item.textContent));
    expect(within(items[0]!).getByText("Reference list")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Score 0.81")).toBeInTheDocument();
    expect(within(items[1]!).queryByText("Reference list")).not.toBeInTheDocument();
    expect(within(items[1]!).queryByText(/Score/)).not.toBeInTheDocument();
  });

  it("opens the paper at the page of a passage", async () => {
    const user = await openAsk();
    await answered(user, "transmon");
    await user.click(await screen.findByRole("button", { name: "Show the passages" }));
    const context = TRANSMON.contexts[1] ?? TRANSMON.contexts[0]!;
    const index = TRANSMON.contexts.indexOf(context);
    const item = screen.getByText(`Passage ${index + 1}`).closest("li")!;
    const open = within(item).getByRole("button", { name: /^Open the paper at/ });
    await user.click(open);
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(new RegExp(`Page ${context.first_page} of`)),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(open).toHaveFocus());
  });
});

describe("the options of one question (A4)", () => {
  it("labels what the server decides as the default and sends nothing for it", async () => {
    const user = await openAsk();
    const seen = takeAsks();
    await user.click(screen.getByRole("button", { name: "Options for this question" }));
    expect(screen.getByRole("dialog", { name: "Options" })).toBeInTheDocument();
    const group = screen.getByRole("radiogroup", { name: "Keyword matching" });
    expect(within(group).getByRole("radio", { name: "Server setting (default)" })).toBeChecked();
    const blocks = screen.getByRole("radiogroup", { name: "Complete equations and tables" });
    expect(within(blocks).getByRole("radio", { name: "Server setting (default)" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Search with the index" })).toBeChecked();
    await user.keyboard("{Escape}");
    await user.type(screen.getByLabelText("Your question"), "Q{Control>}{Enter}{/Control}");
    await waitFor(() => expect(seen.bodies).toHaveLength(1));
    expect(seen.bodies[0]).toMatchObject({ use_index: true, bm25: null, whole_blocks: null });
  });

  it("sends the options that were chosen", async () => {
    const user = await openAsk();
    const seen = takeAsks();
    const trigger = screen.getByRole("button", { name: "Options for this question" });
    await user.click(trigger);
    await user.click(screen.getByRole("switch", { name: "Search with the index" }));
    await user.click(screen.getByRole("radio", { name: "Always" }));
    await user.click(
      within(screen.getByRole("radiogroup", { name: "Complete equations and tables" })).getByRole(
        "radio",
        { name: "Yes" },
      ),
    );
    await user.keyboard("{Escape}");
    expect(trigger).toHaveTextContent("3 changed");

    await user.type(screen.getByLabelText("Your question"), "Q{Control>}{Enter}{/Control}");
    await waitFor(() => expect(seen.bodies).toHaveLength(1));
    expect(seen.bodies[0]).toMatchObject({ use_index: false, bm25: "always", whole_blocks: true });
    expect(useAskStore.getState().entries[0]?.settings).toEqual({
      useIndex: false,
      bm25: "always",
      wholeBlocks: true,
    });
  });

  it("offers the four ways of keyword matching and the two of completing blocks", async () => {
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: "Options for this question" }));
    const keyword = within(screen.getByRole("radiogroup", { name: "Keyword matching" }));
    expect(keyword.getAllByRole("radio").map((radio) => radio.getAttribute("value"))).toEqual([
      "default",
      "english",
      "always",
      "never",
    ]);
    await user.click(keyword.getByRole("radio", { name: "English questions and LaTeX" }));
    expect(useAskStore.getState().settings.bm25).toBe("english");
    await user.click(keyword.getByRole("radio", { name: "Never" }));
    expect(useAskStore.getState().settings.bm25).toBe("never");
    const blocks = within(
      screen.getByRole("radiogroup", { name: "Complete equations and tables" }),
    );
    await user.click(blocks.getByRole("radio", { name: "No" }));
    expect(useAskStore.getState().settings.wholeBlocks).toBe(false);
  });

  it("goes back to the server settings", async () => {
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: "Options for this question" }));
    const reset = screen.getByRole("button", { name: "Use the server settings" });
    expect(reset).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: "Never" }));
    expect(reset).toBeEnabled();
    await user.click(reset);
    expect(useAskStore.getState().settings).toEqual({
      useIndex: true,
      bm25: null,
      wholeBlocks: null,
    });
    expect(screen.getByRole("button", { name: "Options for this question" })).not.toHaveTextContent(
      "changed",
    );
  });

  it("shows no search step when the index is off", async () => {
    const user = await openAsk();
    takeAsks();
    await user.click(screen.getByRole("button", { name: "Options for this question" }));
    await user.click(screen.getByRole("switch", { name: "Search with the index" }));
    await user.keyboard("{Escape}");
    await ask(user, "Q");
    const steps = await screen.findByRole("list", { name: "Progress of your question" });
    expect(within(steps).queryByText("Searching the papers")).not.toBeInTheDocument();
    expect(within(steps).getAllByRole("listitem")).toHaveLength(2);
    serve((source) => replay(source, capturedAsk("without index").events));
    expect(await screen.findByText(/Answered in .* s, without the index\./)).toBeInTheDocument();
  });
});

describe("when the service cannot answer (A5)", () => {
  it("disables the form and says so while the engine warms up", async () => {
    withStatus({ state: "warming_up" });
    const user = await openAsk();
    expect(await screen.findByText("The engine is starting")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Your question"), "A question");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    // Ctrl+Enter is refused too, and the box says why to a screen reader.
    expect(screen.getByLabelText("Your question")).toHaveAccessibleDescription(
      /The engine is starting, so Ask is off/,
    );
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("opens the form again once the engine is ready", async () => {
    let state = "warming_up";
    server.use(
      http.get("*/api/v1/status", () =>
        HttpResponse.json({ ...STATUS_BODY, ask: { ...STATUS_BODY.ask, state } }),
      ),
    );
    const user = await openAsk();
    await screen.findByText("The engine is starting");
    state = "ready";
    await user.type(screen.getByLabelText("Your question"), "A question");
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled(), {
      timeout: 6000,
    });
    expect(screen.queryByText("The engine is starting")).not.toBeInTheDocument();
  }, 10_000);

  it("disables the button when the engine reports a problem, and says why", async () => {
    withStatus({ state: "unavailable", reason: "The index could not be opened." });
    const user = await openAsk();
    expect(
      await screen.findByText(/The index could not be opened\. Ask is off while this lasts/),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("Your question"), "A question");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ask anyway" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Ask" })).toHaveAccessibleDescription(
      /The engine reports a problem, so Ask is off/,
    );
    expect(screen.getByLabelText("Your question")).toHaveAccessibleDescription(
      /The engine reports a problem, so Ask is off/,
    );
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("sends the question with Ask anyway, so a fixed engine is never locked out", async () => {
    withStatus({ state: "unavailable", reason: "The index could not be opened." });
    const seen = takeAsks();
    const user = await openAsk();
    await screen.findByText("The engine reports a problem");
    expect(screen.getByRole("button", { name: "Ask anyway" })).toBeDisabled();
    await user.type(screen.getByLabelText("Your question"), "A question");
    await user.click(screen.getByRole("button", { name: "Ask anyway" }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    expect(seen.bodies).toHaveLength(1);
    expect(seen.bodies[0]).toMatchObject({ question: "A question" });
    serve((source) => replay(source, capturedAsk("transmon").events));
    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
  });

  it("looks at the status again when an answer arrives, so Ask opens at once", async () => {
    let state = "unavailable";
    server.use(
      http.get("*/api/v1/status", () =>
        HttpResponse.json({
          ...STATUS_BODY,
          ask: { ...STATUS_BODY.ask, state, reason: state === "ready" ? null : "Models down." },
        }),
      ),
    );
    takeAsks();
    const user = await openAsk();
    await screen.findByText("The engine reports a problem");
    await user.type(screen.getByLabelText("Your question"), "A question");
    await user.click(screen.getByRole("button", { name: "Ask anyway" }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    // The engine is fine by the time the answer comes; the next poll is 30 s away.
    state = "ready";
    serve((source) => replay(source, capturedAsk("transmon").events));
    await screen.findByRole("heading", { name: "Answer" });
    await waitFor(() =>
      expect(screen.queryByText("The engine reports a problem")).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Ask anyway" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Your question"), " again");
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
  });

  it("offers Ask anyway only to an engine that reports a problem", async () => {
    await openAsk();
    await screen.findByRole("status", { name: /Service status: Ready/ });
    expect(screen.queryByRole("button", { name: "Ask anyway" })).not.toBeInTheDocument();
  });

  it("copes with an engine that reports a problem without a reason", async () => {
    withStatus({ state: "unavailable", reason: null });
    await openAsk();
    expect(
      await screen.findByText(
        "Ask is off while this lasts. If the cause may be fixed, use Ask anyway.",
      ),
    ).toBeInTheDocument();
  });

  it("says when the service cannot be reached", async () => {
    server.use(http.get("*/api/v1/status", () => problem(500, "Down.")));
    await openAsk();
    expect(await screen.findByText("The service cannot be reached")).toBeInTheDocument();
  });

  it("shows no notice when the engine is ready", async () => {
    await openAsk();
    await screen.findByRole("status", { name: /Service status: Ready/ });
    expect(screen.queryByText("The engine is starting")).not.toBeInTheDocument();
    expect(screen.queryByText("The engine reports a problem")).not.toBeInTheDocument();
    expect(screen.queryByText("The service cannot be reached")).not.toBeInTheDocument();
  });

  it("shows a 503 on sending, with the server's message and a way to try again", async () => {
    const user = await openAsk();
    let calls = 0;
    server.use(
      http.post("*/api/v1/asks", () => {
        calls += 1;
        return calls === 1
          ? problem(503, "The engine is still starting.", { "retry-after": "4" })
          : HttpResponse.json({ ask_id: "a2", position: 0 }, { status: 202 });
      }),
    );
    await user.type(screen.getByLabelText("Your question"), "A question");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The service cannot answer right now");
    expect(alert).toHaveTextContent("The engine is still starting.");
    expect(alert).toHaveTextContent("The service suggests waiting 4 s.");
    expect(alert).toHaveTextContent("A question");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(calls).toBe(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a 503 that arrives during the ask, and tries again with the same question and options", async () => {
    const user = await openAsk();
    const seen = takeAsks();
    await user.click(screen.getByRole("button", { name: "Options for this question" }));
    await user.click(screen.getByRole("radio", { name: "Never" }));
    await user.keyboard("{Escape}");
    await ask(user, "simulate-unavailable please");
    serve((source) => replay(source, capturedAsk("unavailable").events));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The service cannot answer right now");
    expect(alert).toHaveTextContent("The model server did not answer");
    expect(alert).toHaveTextContent("The service suggests waiting 5 s.");

    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(seen.bodies).toHaveLength(2));
    expect(seen.bodies[1]).toEqual(seen.bodies[0]);
    expect(seen.bodies[1]).toMatchObject({ bm25: "never" });
  });

  it("names a refusal and an upstream error by what they are", async () => {
    const user = await openAsk();
    takeAsks();
    await ask(user, "simulate-upstream please");
    serve((source) => replay(source, capturedAsk("upstream").events));
    expect(await screen.findByRole("alert")).toHaveTextContent("A model server returned an error");
  });

  it.each([
    [429, "Too many questions are waiting"],
    [422, "The question was refused"],
    [418, "The question failed"],
  ])("titles a %i on sending", async (status, title) => {
    const user = await openAsk();
    server.use(http.post("*/api/v1/asks", () => problem(status, "No.")));
    await user.type(screen.getByLabelText("Your question"), "A question");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(title);
  });

  it("says the connection was lost when the request never arrived", async () => {
    const user = await openAsk();
    server.use(http.post("*/api/v1/asks", () => HttpResponse.error()));
    await user.type(screen.getByLabelText("Your question"), "A question");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The connection to the service was lost while waiting for the answer.",
    );
  });

  it("reads the answer from the API when the stream breaks", async () => {
    const user = await openAsk();
    takeAsks();
    server.use(
      http.get("*/api/v1/asks/:id", () =>
        HttpResponse.json({
          ask_id: "a1",
          state: "done",
          position: null,
          phase: null,
          response: TRANSMON,
          error: null,
        }),
      ),
    );
    await ask(user, "Q");
    serve((source) => source.drop(true));
    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
  });

  it("says the service forgot the question when it did", async () => {
    const user = await openAsk();
    takeAsks();
    server.use(http.get("*/api/v1/asks/:id", () => problem(404, "No such question.")));
    await ask(user, "Q");
    serve((source) => source.drop(true));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The service no longer has this question. It keeps them for 30 minutes.",
    );
  });

  it("says when the service sent something it does not understand", async () => {
    const user = await openAsk();
    takeAsks();
    await ask(user, "Q");
    serve((source) => source.emit("answer", { surprise: true }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The service sent something this page does not understand.",
    );
  });
});

describe("cancelling and the history", () => {
  it("cancels the question that is being answered", async () => {
    const user = await openAsk();
    takeAsks();
    let deleted: string | null = null;
    server.use(
      http.delete("*/api/v1/asks/:id", ({ params }) => {
        deleted = String(params.id);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await ask(user, "Q");
    await user.click(screen.getByRole("button", { name: "Cancel question" }));
    await waitFor(() => expect(deleted).toBe("a1"));
    expect(await screen.findByText("You cancelled this question.")).toBeInTheDocument();
    // the box was emptied when the question went out, so there is nothing to send until it is typed
    expect(screen.getByLabelText("Your question")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Edit and ask again" }));
    expect(screen.getByLabelText("Your question")).toHaveValue("Q");
    expect(screen.getByLabelText("Your question")).toHaveFocus();
  });

  it("keeps the questions of this tab, newest first, and shows any of them again", async () => {
    const user = await openAsk();
    takeAsks();
    await ask(user, "First question");
    serve((source) => replay(source, capturedAsk("transmon").events));
    await screen.findByRole("heading", { name: "Answer" });
    await user.clear(screen.getByLabelText("Your question"));
    await ask(user, "Second question");
    serve((source) => replay(source, capturedAsk("teleportation").events));
    await waitFor(() => expect(screen.getAllByRole("heading", { name: "Answer" })).toHaveLength(1));

    const history = screen.getByRole("region", { name: "This session" });
    const items = within(history).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Second question");
    expect(items[0]).toHaveTextContent("Answered");
    expect(items[1]).toHaveTextContent("First question");
    expect(
      within(history).getByText(/Closing the tab or signing out erases them/),
    ).toBeInTheDocument();

    await user.click(within(items[1]!).getByRole("button"));
    expect(within(items[1]!).getByRole("button")).toHaveAttribute("aria-current", "true");
    expect(screen.getByText(/limited mostly by dielectric loss/)).toBeInTheDocument();
    expect(screen.getByText("First question", { selector: "p *, p" })).toBeInTheDocument();
  });

  it("says so when there are no questions yet", async () => {
    await openAsk();
    expect(screen.getByText("Your questions will appear here.")).toBeInTheDocument();
  });

  it("keeps a question going and its answer when the screen is left and opened again", async () => {
    const user = await openAsk();
    takeAsks();
    await ask(user, "Q");
    await user.click(screen.getByRole("link", { name: "Circuits" }));
    await screen.findByRole("heading", { name: "Circuits", level: 1 });
    serve((source) => source.emit("answer", TRANSMON));
    await user.click(screen.getByRole("link", { name: "Ask" }));
    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
  });

  it("forgets everything when the person signs out", async () => {
    const user = await openAsk();
    await answered(user, "transmon", "Secret question");
    await screen.findByRole("heading", { name: "Answer" });
    await user.click(screen.getByRole("button", { name: "Ana Prueba's account" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(useAskStore.getState().entries).toEqual([]);
    expect(useAskStore.getState().question).toBe("");
  });

  it("explains itself and keeps the intro", async () => {
    await openAsk();
    expect(screen.getByText(/Questions and answers stay in this tab/)).toBeInTheDocument();
  });
});

describe("in Spanish", () => {
  it("shows the screen, the phases and the pages in Spanish", async () => {
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: /Language/ }));
    await user.click(await screen.findByRole("menuitemradio", { name: "Español" }));
    expect(await screen.findByLabelText("Tu pregunta")).toBeInTheDocument();
    takeAsks();
    await user.type(screen.getByLabelText("Tu pregunta"), "¿Qué limita T1?");
    await user.click(screen.getByRole("button", { name: "Preguntar" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(screen.getByRole("list", { name: "Avance de tu pregunta" })).toBeInTheDocument();
    serve((source) => replay(source, capturedAsk("spanish").events));
    expect(await screen.findByRole("heading", { name: "Respuesta" })).toBeInTheDocument();
    const sources = screen.getByRole("region", { name: "Fuentes" });
    expect(within(sources).getAllByRole("button")[0]).toHaveTextContent(/página/);
  });

  it("says one option changed in the singular", async () => {
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: /Language/ }));
    await user.click(await screen.findByRole("menuitemradio", { name: "Español" }));
    await user.click(await screen.findByRole("button", { name: "Opciones de esta pregunta" }));
    await user.click(screen.getByRole("radio", { name: "Nunca" }));
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: /Opciones de esta pregunta/ })).toHaveTextContent(
      "1 cambiada",
    );
  });
});

describe("read aloud", () => {
  it("shows the button on a finished answer only and a new question stops the voice", async () => {
    const audio = installFakeAudio();
    serveSynthesis();
    try {
      const user = await openAsk();
      takeAsks();
      await ask(user, "What limits T1?");
      expect(screen.queryByRole("button", { name: "Read aloud" })).not.toBeInTheDocument();
      serve((source) => replay(source, capturedAsk("transmon").events));
      await user.click(await screen.findByRole("button", { name: "Read aloud" }));
      await screen.findByRole("button", { name: "Stop reading" });
      await waitFor(() => expect(audio.sources).toHaveLength(1));
      await new Promise((resolve) => setTimeout(resolve, 20));

      FakeEventSource.reset();
      await user.type(screen.getByLabelText("Your question"), "Another one");
      await user.click(screen.getByRole("button", { name: "Ask" }));
      await waitFor(() => expect(audio.sources[0]?.stopped).toBe(true));
      expect(screen.queryByRole("button", { name: "Stop reading" })).not.toBeInTheDocument();
    } finally {
      stopReading();
      audio.restore();
    }
  });

  it("sends the person to sign in when the session ended during a reading", async () => {
    const audio = installFakeAudio();
    serveSynthesis({ failAt: { index: 0, status: 401 } });
    try {
      const user = await openAsk();
      takeAsks();
      await ask(user, "What limits T1?");
      serve((source) => replay(source, capturedAsk("transmon").events));
      const read = await screen.findByRole("button", { name: "Read aloud" });
      // The session ends on the server; the next request, the reading, finds out.
      authMock.handle({ method: "POST", path: "/api/v1/auth/logout", csrfHeader: "x" });
      await user.click(read);
      await screen.findByRole("heading", { name: "Sign in" });
      expect(audio.sources).toHaveLength(0);
    } finally {
      stopReading();
      audio.restore();
    }
  });

  it("stops by itself when a question is sent, even when no button owns the voice", async () => {
    const audio = installFakeAudio();
    serveSynthesis();
    try {
      const user = await openAsk();
      startReading("elsewhere", "Something that is being read.", "server");
      await waitFor(() => expect(audio.sources).toHaveLength(1));
      await new Promise((resolve) => setTimeout(resolve, 20));
      takeAsks();
      await ask(user, "Another one");
      expect(audio.sources[0]?.stopped).toBe(true);
    } finally {
      stopReading();
      audio.restore();
    }
  });

  it("stops the voice when the screen is left, even when no button owns it", async () => {
    const audio = installFakeAudio();
    serveSynthesis();
    try {
      const user = await openAsk();
      startReading("elsewhere", "Something that is being read.", "server");
      await waitFor(() => expect(audio.sources).toHaveLength(1));
      await new Promise((resolve) => setTimeout(resolve, 20));
      await user.click(screen.getByRole("link", { name: /Benchmarks/ }));
      await waitFor(() => expect(audio.sources[0]?.stopped).toBe(true));
    } finally {
      stopReading();
      audio.restore();
    }
  });
});

describe("reading the answer to a spoken question", () => {
  let mic: FakeMic;
  let audio: FakeAudio | undefined;
  beforeEach(() => {
    mic = installFakeMic(); // before the page renders, which asks what the browser can record
    audio = undefined;
  });
  afterEach(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
    stopReading();
    mic.restore();
    audio?.restore();
  });

  /** Presses Speak, talks, presses Stop: the transcript lands in the box. */
  async function speakQuestion(user: User, text = "what limits T1") {
    server.use(
      http.post("*/api/v1/speech/transcriptions", () =>
        HttpResponse.json({ text, language: "en", duration_s: 1 }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Speak" }));
    await screen.findByRole("button", { name: "Stop" });
    act(() => mic.emit(new Float32Array(48_000).fill(0.2)));
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByLabelText("Your question")).toHaveValue(text));
    audio = installFakeAudio(); // the recording is over: from here the page plays audio
  }

  async function sendAndAnswer(user: User) {
    takeAsks();
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    serve((source) => replay(source, capturedAsk("transmon").events));
    await screen.findByRole("heading", { name: "Answer", level: 2 });
  }

  it("reads the answer by itself, with the toggle on by default", async () => {
    const synthesis = serveSynthesis();
    const user = await openAsk();
    expect(
      screen.getByRole("checkbox", { name: "Read answers after a spoken question" }),
    ).toBeChecked();
    await speakQuestion(user);
    await sendAndAnswer(user);
    await waitFor(() => expect(audio?.sources).toHaveLength(1));
    expect(synthesis.calls[0]?.text).not.toMatch(/[0-9a-f]{16}/);
    expect(screen.getByRole("button", { name: "Stop reading" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("does not read when the toggle is off, and remembers the choice", async () => {
    const synthesis = serveSynthesis();
    const user = await openAsk();
    await user.click(
      screen.getByRole("checkbox", { name: "Read answers after a spoken question" }),
    );
    expect(window.localStorage.getItem("re.voice.autoRead")).toBe("false");
    await speakQuestion(user);
    await sendAndAnswer(user);
    expect(screen.getByRole("button", { name: "Read aloud" })).toBeInTheDocument();
    expect(synthesis.calls).toHaveLength(0);
    expect(audio?.sources).toHaveLength(0);
  });

  it("does not read the answer to a typed question", async () => {
    const synthesis = serveSynthesis();
    const user = await openAsk();
    audio = installFakeAudio();
    await answered(user, "transmon", "What limits T1?");
    await screen.findByRole("button", { name: "Read aloud" });
    expect(synthesis.calls).toHaveLength(0);
  });

  it("does not read when the box was emptied and a question typed instead", async () => {
    const synthesis = serveSynthesis();
    const user = await openAsk();
    await speakQuestion(user);
    await user.clear(screen.getByLabelText("Your question"));
    await user.type(screen.getByLabelText("Your question"), "typed after all");
    await sendAndAnswer(user);
    await screen.findByRole("button", { name: "Read aloud" });
    expect(synthesis.calls).toHaveLength(0);
  });

  it("does not read an answer that arrives while another one is on screen", async () => {
    const synthesis = serveSynthesis();
    const user = await openAsk();
    await answered(user, "teleportation", "The typed one");
    await screen.findByRole("heading", { name: "Answer", level: 2 });
    await speakQuestion(user);
    takeAsks({ ask_id: "a2", position: 0 });
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: /The typed one/ }));
    serve((source) => replay(source, capturedAsk("transmon").events));
    await waitFor(() => expect(useAskStore.getState().entries[0]?.status).toBe("done"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(synthesis.calls).toHaveLength(0);
    expect(audio?.sources).toHaveLength(0);
  });

  it("does not read a typed question put back in the box while dictated words were there", async () => {
    const synthesis = serveSynthesis();
    const user = await openAsk();
    await answered(user, "teleportation", "The typed one");
    await screen.findByRole("heading", { name: "Answer", level: 2 });
    await speakQuestion(user);
    await user.click(screen.getByRole("button", { name: "Edit and ask again" }));
    expect(screen.getByLabelText("Your question")).toHaveValue("The typed one");
    expect(useAskStore.getState().spoken).toBe(false);
    await sendAndAnswer(user);
    await waitFor(() => expect(useAskStore.getState().entries[0]?.status).toBe("done"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(synthesis.calls).toHaveLength(0);
  });

  it("hides the switch when voice input is not set up", async () => {
    server.use(
      http.get("*/api/v1/status", () =>
        HttpResponse.json({ ...STATUS_BODY, speech: { stt: "unavailable", tts: "ready" } }),
      ),
    );
    await openAsk();
    await screen.findByText("Voice input is not set up on this server.");
    expect(
      screen.queryByRole("checkbox", { name: "Read answers after a spoken question" }),
    ).not.toBeInTheDocument();
  });

  it("reads with the voice of the browser when the speech server is not ready", async () => {
    const speech = installFakeSpeech(vi, [voice("Samantha", "en-US", true)]);
    server.use(
      http.get("*/api/v1/status", () =>
        HttpResponse.json({ ...STATUS_BODY, speech: { stt: "ready", tts: "unavailable" } }),
      ),
    );
    const synthesis = serveSynthesis();
    try {
      const user = await openAsk();
      await speakQuestion(user);
      await sendAndAnswer(user);
      await waitFor(() => expect(speech.spoken.length).toBeGreaterThan(0));
      expect(synthesis.calls).toHaveLength(0);
    } finally {
      stopReading();
      speech.restore();
    }
  });
});
