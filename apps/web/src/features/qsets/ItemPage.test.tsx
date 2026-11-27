import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedAnswer, capturedBody, meAs, problem, problemBody } from "@/test/sets-api";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";

preloadScreens(() => import("./ItemPage"));

// These tests type into a whole editor: a busy machine must not turn the default 5 s into a failure.
vi.setConfig({ testTimeout: 20_000 });

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

const V2 = "/sets/demo-papers/v2";

async function openItem(id: string, base = V2) {
  const user = userEvent.setup();
  const app = renderApp(`${base}/items/${id}`);
  await screen.findByRole("heading", { level: 1, name: id });
  await screen.findByRole("button", { name: "Check against qset/1" });
  return { user, app };
}

/** Records the PUT of an item: its If-Match header and its body. */
function recordPut(id: string, answer: () => Response | Promise<Response>) {
  const seen: { ifMatch: string | null; body: Record<string, any> }[] = [];
  server.use(
    http.put(`*/api/v1/sets/demo-papers/versions/v2/items/${id}`, async ({ request }) => {
      seen.push({
        ifMatch: request.headers.get("if-match"),
        body: (await request.json()) as Record<string, any>,
      });
      return answer();
    }),
  );
  return seen;
}

/** The text of everything that describes a control (its hint and its problems). */
function described(element: HTMLElement): string {
  return (element.getAttribute("aria-describedby") ?? "")
    .split(" ")
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .join(" ");
}

const etagOf = (name: string) =>
  (capturedBody<Record<string, any>>(name).version as { etag: string }).etag;

describe("the fields of an item (S2)", () => {
  it("shows the fields of a multi-paper item, with synthesis and necessity", async () => {
    await openItem("DP-002");
    expect(screen.getByLabelText("Id")).toHaveValue("DP-002");
    expect(screen.getByLabelText("Id")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Form")).toHaveValue("multi_chain");
    expect(screen.getByLabelText("Language")).toHaveValue("en");
    expect(screen.getByLabelText("Corpus")).toHaveValue("demo-corpus");
    expect(screen.getByLabelText("Split")).toHaveValue("test");
    expect(screen.getByLabelText("Question")).toHaveValue(
      "What does the synthetic paper Beta change in the coupling that Alpha introduces?",
    );
    expect(screen.getByLabelText("Kind")).toHaveValue("chain");
    expect(screen.getByLabelText("Statement")).toHaveValue(
      "Beta generalizes the coupling defined in Alpha.",
    );
    expect(screen.getByLabelText("Necessity (JSON)")).toBeInTheDocument();
    expect(screen.getByLabelText<HTMLTextAreaElement>("Gold papers (JSON)").value).toContain(
      '"doc_id"',
    );
    expect(screen.getByLabelText("Tag 1 name")).toHaveValue("category");
    expect(screen.getByLabelText("Value of key fact 2")).toHaveValue("three couplings");
    expect(screen.getByLabelText<HTMLTextAreaElement>("Other fields (JSON)").value).toContain(
      '"writer": "ana"',
    );
  });

  it("does not show what a single-paper item does not have", async () => {
    await openItem("DP-004");
    expect(screen.queryByLabelText("Necessity (JSON)")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Synthesis" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Circuit (JSON)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Search record (JSON)")).not.toBeInTheDocument();
  });

  it("shows the search record of a question with no answer in the corpus", async () => {
    await openItem("DP-003");
    expect(screen.getByLabelText("Search record (JSON)")).toBeInTheDocument();
  });

  it("shows the circuit and a yes or no answer for a circuit item", async () => {
    await openItem("DC-001", "/sets/demo-circuits/v1");
    expect(screen.getByLabelText("Circuit (JSON)")).toBeInTheDocument();
    expect(screen.getByLabelText("Answer")).toHaveValue("no");
    expect(screen.getByLabelText("Answer").tagName).toBe("SELECT");
  });

  it("shows the sections the form asks for after the form changes", async () => {
    const { user } = await openItem("DP-004");
    await user.selectOptions(screen.getByLabelText("Form"), "multi_compare");
    expect(screen.getByLabelText("Necessity (JSON)")).toBeInTheDocument();
    expect(screen.getByLabelText("Kind")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Form"), "circuit_create");
    expect(screen.getByLabelText("Circuit (JSON)")).toBeInTheDocument();
  });
});

describe("every control writes its own field", () => {
  it("sends what each control of a multi-paper item was set to", async () => {
    const { user } = await openItem("DP-002");
    const seen = recordPut("DP-002", () => HttpResponse.json({}));
    await user.selectOptions(screen.getByLabelText("Language"), "es");
    await user.selectOptions(screen.getByLabelText("Content"), "other");
    await user.selectOptions(screen.getByLabelText("Split"), "dev");
    await user.selectOptions(screen.getByLabelText("Language origin"), "mirror");
    await user.type(screen.getByLabelText("Mirror of"), "DP-001");
    const corpus = screen.getByLabelText("Corpus");
    await user.clear(corpus);
    await user.type(corpus, "other-corpus");
    await user.type(screen.getByLabelText("Rationale"), "Because.");
    await user.type(screen.getByLabelText("Notes", { exact: true }), "A note.");
    await user.selectOptions(screen.getByLabelText("Kind"), "order");
    const statement = screen.getByLabelText("Statement");
    await user.clear(statement);
    await user.type(statement, "A before B.");
    const rubric = screen.getByLabelText("Rubric");
    await user.clear(rubric);
    await user.type(rubric, "Orders them.");
    const necessity = screen.getByLabelText("Necessity (JSON)");
    await user.click(necessity);
    await user.clear(necessity);
    await user.paste('{"a1a1a1a1a1a1a1a1": [0]}');
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]!.body).toMatchObject({
      lang: "es",
      content: "other",
      split: "dev",
      lang_origin: "mirror",
      mirror_of: "DP-001",
      corpus: "other-corpus",
      rationale: "Because.",
      notes: "A note.",
      synthesis: { kind: "order", statement: "A before B.", rubric: "Orders them." },
      necessity: { a1a1a1a1a1a1a1a1: [0] },
    });
  });

  it("takes the synthesis out of the item when all three of its boxes are emptied", async () => {
    const { user } = await openItem("DP-002");
    const seen = recordPut("DP-002", () => HttpResponse.json({}));
    await user.selectOptions(screen.getByLabelText("Kind"), "Choose");
    await user.clear(screen.getByLabelText("Statement"));
    await user.clear(screen.getByLabelText("Rubric"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect("synthesis" in seen[0]!.body).toBe(false);
  });

  it("writes the search record and the circuit from their JSON boxes", async () => {
    const { user } = await openItem("DP-003");
    const seen = recordPut("DP-003", () => HttpResponse.json({}));
    const record = screen.getByLabelText("Search record (JSON)");
    await user.click(record);
    await user.clear(record);
    await user.paste('{"corpus": "demo-corpus", "terms": ["relaxation"]}');
    await user.selectOptions(screen.getByLabelText("Form"), "circuit_create");
    const circuit = screen.getByLabelText("Circuit (JSON)");
    await user.click(circuit);
    await user.paste('{"wires": 2}');
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]!.body.search_record).toEqual({ corpus: "demo-corpus", terms: ["relaxation"] });
    expect(seen[0]!.body.circuit).toEqual({ wires: 2 });
    expect(seen[0]!.body.form).toBe("circuit_create");
  });

  it("removes a tag row", async () => {
    const { user } = await openItem("DP-001");
    const seen = recordPut("DP-001", () => HttpResponse.json({}));
    await user.click(screen.getByRole("button", { name: "Remove tag 1" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect("tags" in seen[0]!.body).toBe(false);
  });
});

describe("saving an item (S2)", () => {
  it("starts with nothing to save", async () => {
    await openItem("DP-003");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard changes" })).toBeDisabled();
  });

  it("sends the whole item with the etag that was read, then says it is saved", async () => {
    const { user } = await openItem("DP-003");
    const seen = recordPut("DP-003", () =>
      HttpResponse.json(capturedBody("item demo-papers/v2/DP-003")),
    );
    await user.type(screen.getByLabelText("Question"), " (edited)");
    expect(screen.getByText("1 field changed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(seen).toHaveLength(1);
    expect(seen[0]!.ifMatch).toBe(etagOf("item demo-papers/v2/DP-003"));
    const stored = capturedBody<Record<string, any>>("item demo-papers/v2/DP-003").item;
    expect(seen[0]!.body).toEqual({ ...stored, question: `${stored.question} (edited)` });
  });

  it("takes an optional text out of the item when it is emptied", async () => {
    const { user } = await openItem("DP-001");
    const seen = recordPut("DP-001", () => HttpResponse.json({}));
    await user.clear(screen.getByLabelText("Answer"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect("answer" in seen[0]!.body).toBe(false);
  });

  it("shows each problem at its field and never loses one", async () => {
    const { user } = await openItem("DP-003");
    recordPut("DP-003", () => problem("put 422"));
    await user.type(screen.getByLabelText("Question"), " x");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("The item is not a valid qset/1 item.")).toBeInTheDocument();
    const lang = screen.getByLabelText("Language");
    expect(lang).toHaveAttribute("aria-invalid", "true");
    expect(described(lang)).toContain("'fr' is not one of ['en', 'es']");
    const question = screen.getByLabelText("Question");
    expect(question).toHaveAttribute("aria-invalid", "true");
    expect(described(question)).toContain("'question' is a required property");
    const gold = screen.getByLabelText("Gold papers (JSON)");
    expect(described(gold)).toContain("/0/passages: 'passages' is a required property");
    expect(described(gold)).toContain("/0/doc_id");
    const other = screen.getByLabelText("Other fields (JSON)");
    expect(described(other)).toContain(
      "Additional properties are not allowed ('colour' was unexpected)",
    );
  });

  it("shows a problem at a part that this form does not show", async () => {
    const { user } = await openItem("DP-004");
    recordPut("DP-004", () =>
      problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
        errors: [
          { path: "/necessity", message: "'necessity' is a required property" },
          { path: "", message: "the item as a whole" },
        ],
      }),
    );
    await user.type(screen.getByLabelText("Question"), " x");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    const group = await screen.findByRole("group", {
      name: "Problems at parts this form does not show",
    });
    expect(within(group).getByText("/necessity")).toBeInTheDocument();
    expect(within(group).getByText("the item as a whole")).toBeInTheDocument();
  });

  it("checks the item against qset/1 without saving", async () => {
    const { user } = await openItem("DP-003");
    let body: unknown;
    server.use(
      http.post("*/api/v1/sets/validate", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(capturedAnswer("validate ok").response);
      }),
    );
    await user.click(screen.getByRole("button", { name: "Check against qset/1" }));
    expect(await screen.findByText(/valid qset\/1 item. Nothing was saved/)).toBeInTheDocument();
    expect(body).toEqual({
      item: capturedBody<Record<string, any>>("item demo-papers/v2/DP-003").item,
    });
  });

  it("shows the problems of a check at their fields", async () => {
    const { user } = await openItem("DP-003");
    server.use(
      http.post("*/api/v1/sets/validate", () =>
        HttpResponse.json(capturedAnswer("validate bad").response),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Check against qset/1" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Language")).toHaveAttribute("aria-invalid", "true"),
    );
  });

  it("builds a tag from a row and saves it", async () => {
    const { user } = await openItem("DP-003");
    const seen = recordPut("DP-003", () => HttpResponse.json({}));
    await user.click(screen.getByRole("button", { name: "Add a tag" }));
    await user.type(screen.getByLabelText("Tag 1 name"), "category");
    await user.type(screen.getByLabelText("Tag 1 value"), "limits");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]!.body.tags).toEqual({ category: "limits" });
  });

  it("discards the changes", async () => {
    const { user } = await openItem("DP-003");
    const question = screen.getByLabelText("Question");
    const before = (question as HTMLTextAreaElement).value;
    await user.type(question, " more");
    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() => expect(screen.getByLabelText("Question")).toHaveValue(before));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });
});

describe("the whole item as JSON", () => {
  it("shows the same item and takes a change into the fields", async () => {
    const { user } = await openItem("DP-003");
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    const box = screen.getByLabelText<HTMLTextAreaElement>("The whole item as JSON");
    expect(JSON.parse(box.value).id).toBe("DP-003");
    const next = { ...JSON.parse(box.value), question: "From the JSON box" };
    await user.click(box);
    await user.clear(box);
    await user.paste(JSON.stringify(next));
    await user.click(screen.getByRole("tab", { name: "Fields" }));
    expect(screen.getByLabelText("Question")).toHaveValue("From the JSON box");
  });

  it("stops everything while the text cannot be read", async () => {
    const { user } = await openItem("DP-003");
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    const box = screen.getByLabelText("The whole item as JSON");
    await user.click(box);
    await user.clear(box);
    await user.paste("{ not json");
    expect((await screen.findAllByText(/Not valid JSON/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("alert")).toHaveTextContent("cannot be used as written");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check against qset/1" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Propose edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("refuses text that is JSON but not an object", async () => {
    const { user } = await openItem("DP-003");
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    const box = screen.getByLabelText("The whole item as JSON");
    await user.click(box);
    await user.clear(box);
    await user.paste("[1, 2]");
    expect(await screen.findByText("This must be a JSON object.")).toBeInTheDocument();
  });

  it("stops saving while a JSON box of the fields cannot be read, and goes on when it can", async () => {
    const { user } = await openItem("DP-003");
    const box = screen.getByLabelText("Other fields (JSON)");
    await user.click(box);
    await user.clear(box);
    await user.paste("{ nope");
    expect((await screen.findAllByText(/Not valid JSON/)).length).toBeGreaterThan(0);
    await user.type(screen.getByLabelText("Question"), " x");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await user.clear(box);
    await user.paste('{"writer": "ana"}');
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
  });
});

describe("key facts", () => {
  it("changes a text fact into a number: the tolerance and the unit appear", async () => {
    const { user } = await openItem("DP-001");
    const seen = recordPut("DP-001", () => HttpResponse.json({}));
    await user.selectOptions(screen.getByLabelText("Type of key fact 1"), "num");
    expect(screen.getByLabelText("Relative tolerance of key fact 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Unit of key fact 1")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Other accepted spellings of key fact 1"),
    ).not.toBeInTheDocument();
    const value = screen.getByLabelText("Value of key fact 1");
    await user.clear(value);
    await user.type(value, "1.5");
    await user.clear(screen.getByLabelText("Relative tolerance of key fact 1"));
    await user.type(screen.getByLabelText("Relative tolerance of key fact 1"), "0.01");
    await user.type(screen.getByLabelText("Unit of key fact 1"), "us");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]!.body.key_facts[0]).toMatchObject({
      type: "num",
      value: 1.5,
      rel: 0.01,
      unit: "us",
    });
  });

  it("lists other spellings one per line, adds a fact and removes one", async () => {
    const { user } = await openItem("DP-001");
    const seen = recordPut("DP-001", () => HttpResponse.json({}));
    await user.click(screen.getByRole("button", { name: "Add a key fact" }));
    expect(screen.getByRole("group", { name: "Key fact 2" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Value of key fact 2"), "J");
    const alts = screen.getByLabelText("Other accepted spellings of key fact 2");
    await user.click(alts);
    await user.paste("J_1\n\nJ-1\n");
    await user.click(screen.getByRole("button", { name: "Remove key fact 1" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]!.body.key_facts).toEqual([{ type: "text", value: "J", alts: ["J_1", "J-1"] }]);
  });

  it("drops the whole field when the last fact goes", async () => {
    const { user } = await openItem("DP-001");
    const seen = recordPut("DP-001", () => HttpResponse.json({}));
    await user.click(screen.getByRole("button", { name: "Remove key fact 1" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect("key_facts" in seen[0]!.body).toBe(false);
  });

  it("shows a problem at the row and field that the API names", async () => {
    const { user } = await openItem("DP-001");
    recordPut("DP-001", () =>
      problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
        errors: [{ path: "/key_facts/0/value", message: "'J' is not valid" }],
      }),
    );
    await user.type(screen.getByLabelText("Question"), " x");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect((await screen.findAllByText("'J' is not valid")).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Value of key fact 1")).toHaveAttribute("aria-invalid", "true");
  });
});

describe("two people editing the same draft (S2, 409)", () => {
  it("says the draft changed and offers to load the latest, dropping the edits", async () => {
    const { user } = await openItem("DP-003");
    recordPut("DP-003", () => problem("put 409 stale"));
    const question = screen.getByLabelText("Question");
    const before = (question as HTMLTextAreaElement).value;
    await user.type(question, " mine");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Changed since you opened it")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load the latest" }));
    await waitFor(() => expect(screen.getByLabelText("Question")).toHaveValue(before));
    expect(screen.queryByText("Changed since you opened it")).not.toBeInTheDocument();
  });

  it("reads the draft again and keeps the edits when asked", async () => {
    const { user } = await openItem("DP-003");
    recordPut("DP-003", () => problem("put 409 stale"));
    let reads = 0;
    server.use(
      http.get("*/api/v1/sets/demo-papers/versions/v2/items/DP-003", () => {
        reads += 1;
        const body = capturedBody<Record<string, any>>("item demo-papers/v2/DP-003");
        body.version.etag = '"newer"';
        return HttpResponse.json(body);
      }),
    );
    await user.type(screen.getByLabelText("Question"), " mine");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    expect(await screen.findByText(/Your edits are kept; save again/)).toBeInTheDocument();
    expect(reads).toBe(1);
    expect(screen.getByLabelText<HTMLTextAreaElement>("Question").value).toMatch(/ mine$/);
    // The next save carries the etag that was just read.
    const seen = recordPut("DP-003", () => HttpResponse.json({}));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]!.ifMatch).toBe('"newer"');
  });
});

describe("an item with decisions (O-126 c)", () => {
  it("explains that its text is fixed and moves the person to an edit decision", async () => {
    const { user } = await openItem("DP-001");
    recordPut("DP-001", () => problem("put 409 decisions"));
    await user.type(screen.getByLabelText("Question"), " reworded");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("This item has review decisions")).toBeInTheDocument();
    expect(screen.queryByText("Changed since you opened it")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Propose these changes as an edit decision" }),
    );
    await waitFor(() => expect(screen.getByLabelText("Note")).toHaveFocus());

    const posted: { ifMatch: string | null; body: Record<string, any> }[] = [];
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/reviews", async ({ request }) => {
        posted.push({
          ifMatch: request.headers.get("if-match"),
          body: (await request.json()) as Record<string, any>,
        });
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    await user.type(screen.getByLabelText("Note"), "Clearer wording.");
    await user.click(screen.getByRole("button", { name: "Propose edit" }));
    expect(await screen.findByText("Recorded: edit.")).toBeInTheDocument();
    const stored = capturedBody<Record<string, any>>("item demo-papers/v2/DP-001").item;
    expect(posted[0]!.ifMatch).toBe(etagOf("item demo-papers/v2/DP-001"));
    expect(posted[0]!.body).toEqual({
      item_id: "DP-001",
      verdict: "edit",
      note: "Clearer wording.",
      edit: { question: `${stored.question} reworded` },
    });
  });
});

describe("deleting an item", () => {
  it("asks first, then deletes with the etag and goes back to the version", async () => {
    const { user, app } = await openItem("DP-003");
    const seen: (string | null)[] = [];
    server.use(
      http.delete("*/api/v1/sets/demo-papers/versions/v2/items/DP-003", ({ request }) => {
        seen.push(request.headers.get("if-match"));
        return HttpResponse.json({});
      }),
    );
    await user.click(screen.getByRole("button", { name: "Delete item" }));
    expect(screen.getByText("Delete DP-003?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep it" }));
    expect(screen.queryByText("Delete DP-003?")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete item" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(app.router.state.location.pathname).toBe(V2));
    expect(seen).toEqual([etagOf("item demo-papers/v2/DP-003")]);
  });

  it("shows why an item cannot be deleted", async () => {
    const { user } = await openItem("DP-003");
    server.use(
      http.delete("*/api/v1/sets/demo-papers/versions/v2/items/DP-003", () =>
        problemBody(409, "conflict", "DP-003 is the original of a mirror."),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Delete item" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByText("DP-003 is the original of a mirror.")).toBeInTheDocument();
    expect(screen.queryByText("Delete DP-003?")).not.toBeInTheDocument();
  });
});

describe("a member who does not own the draft", () => {
  it("cannot save or delete, and is told how to propose a change", async () => {
    server.use(meAs("dani"));
    await openItem("DP-003");
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete item" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Only the owner \(beto\) or an admin can save changes/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();
  });

  it("shows a refusal of the server that the page did not foresee", async () => {
    server.use(meAs("beto"));
    const { user } = await openItem("DP-003");
    recordPut("DP-003", () => problem("put 403 member"));
    await user.type(screen.getByLabelText("Question"), " x");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Not allowed")).toBeInTheDocument();
  });
});

describe("a frozen item", () => {
  it("is read only and shows its decisions", async () => {
    renderApp("/sets/demo-papers/v1/items/DP-001");
    await screen.findByRole("heading", { level: 1, name: "DP-001" });
    expect(
      await screen.findByText("This version is frozen, so the item cannot change."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.getByText("Gold")).toBeInTheDocument();
    const history = screen.getByRole("list", { name: "Decisions" });
    expect(within(history).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("The whole item as JSON")).toBeInTheDocument();
  });
});

describe("a missing item", () => {
  it("says so and offers the way back", async () => {
    renderApp(`${V2}/items/NOPE`);
    expect(await screen.findByText("No such item.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to demo-papers v2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
