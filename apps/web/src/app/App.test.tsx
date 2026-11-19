import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA as ana, TEMP as temp, MAX_FAILURES } from "@/test/auth-mock";
import { renderApp } from "@/test/render";
import { setSystemDark } from "@/test/media";
import { authMock, server } from "@/test/server";

async function signIn(
  user: ReturnType<typeof userEvent.setup>,
  username: string,
  password: string,
) {
  await user.type(await screen.findByLabelText("Username"), username);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("the session guard", () => {
  it("sends a visitor without a session to the login screen", async () => {
    const app = renderApp("/benchmarks");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/login");
    expect(app.router.state.location.search).toBe("?next=%2Fbenchmarks");
  });

  it("lets a signed-in user in and sends the root to Ask", async () => {
    authMock.handle({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { username: ana.username, password: ana.password },
    });
    const app = renderApp("/");
    expect(await screen.findByRole("heading", { name: "Ask" })).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/ask");
  });

  it("moves a signed-in user off the login screen", async () => {
    authMock.handle({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { username: ana.username, password: ana.password },
    });
    const app = renderApp("/login?next=%2Fcircuits");
    expect(await screen.findByRole("heading", { name: "Circuits" })).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/circuits");
  });

  it("does not follow a return path that leaves the app", async () => {
    authMock.handle({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { username: ana.username, password: ana.password },
    });
    const app = renderApp("/login?next=%2F%2Fevil.example");
    expect(await screen.findByRole("heading", { name: "Ask" })).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/ask");
  });

  it("does not follow a return path that hides a host behind a control character", async () => {
    authMock.handle({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { username: ana.username, password: ana.password },
    });
    const app = renderApp("/login?next=%2F%09%2Fexample.org");
    expect(await screen.findByRole("heading", { name: "Ask" })).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/ask");
  });

  it("sends a session that must change its password to the change screen", async () => {
    authMock.handle({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { username: temp.username, password: temp.password },
    });
    const app = renderApp("/sets");
    expect(
      await screen.findByRole("heading", { name: "Change your password" }),
    ).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/login");
  });

  it("shows a panel with Try again when the session check fails", async () => {
    server.use(
      http.get("*/api/v1/auth/me", () => HttpResponse.json({ detail: "Down." }, { status: 503 })),
    );
    renderApp("/ask");
    expect(
      await screen.findByRole("heading", { name: "Something went wrong" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Down.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("login", () => {
  it("signs in and lands on the screen that was asked for", async () => {
    const user = userEvent.setup();
    const app = renderApp("/benchmarks");
    await signIn(user, ana.username, ana.password);
    expect(await screen.findByRole("heading", { name: "Benchmarks" })).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/benchmarks");
    expect(authMock.signedInAs()).toBe(ana.username);
  });

  it("asks for both fields before it calls the server", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findAllByText("This field is required.")).toHaveLength(2);
    expect(screen.getByLabelText("Username")).toBeInvalid();
    expect(authMock.signedInAs()).toBeNull();
  });

  it("shows one message for a wrong user and a wrong password", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user, "nobody", "whatever-it-is");
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");
    await user.clear(screen.getByLabelText("Username"));
    await user.type(screen.getByLabelText("Username"), ana.username);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Wrong username or password."),
    );
  });

  it("tells the user how long to wait after too many failures", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await user.type(await screen.findByLabelText("Username"), ana.username);
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    for (let attempt = 0; attempt <= MAX_FAILURES; attempt += 1) {
      await user.click(screen.getByRole("button", { name: "Sign in" }));
      await screen.findByRole("alert");
    }
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Wait 30 s and try again."),
    );
  });

  it("says the server is not answering when the call fails", async () => {
    server.use(http.post("*/api/v1/auth/login", () => HttpResponse.error()));
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user, ana.username, ana.password);
    expect(await screen.findByRole("alert")).toHaveTextContent("The server is not answering.");
  });
});

describe("the forced password change", () => {
  async function reachChangeScreen(user: ReturnType<typeof userEvent.setup>) {
    renderApp("/login");
    await signIn(user, temp.username, temp.password);
    await screen.findByRole("heading", { name: "Change your password" });
  }

  it("moves focus to the heading when the change form replaces the sign-in form", async () => {
    const user = userEvent.setup();
    await reachChangeScreen(user);
    expect(screen.getByRole("heading", { name: "Change your password" })).toHaveFocus();
  });

  it("follows a first login with the change screen, then opens the app", async () => {
    const user = userEvent.setup();
    await reachChangeScreen(user);
    await user.type(screen.getByLabelText("Current password"), temp.password);
    await user.type(screen.getByLabelText("New password"), "a-brand-new-password");
    await user.type(screen.getByLabelText("Repeat the new password"), "a-brand-new-password");
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("heading", { name: "Ask" })).toBeInTheDocument();
  });

  it("checks the new password before it calls the server", async () => {
    const user = userEvent.setup();
    await reachChangeScreen(user);
    await user.type(screen.getByLabelText("Current password"), temp.password);
    await user.type(screen.getByLabelText("New password"), "short");
    await user.type(screen.getByLabelText("Repeat the new password"), "different");
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByText("Use at least 12 characters.")).toBeInTheDocument();
    expect(screen.getByText("The passwords do not match.")).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toBeInvalid();
  });

  it("says when the current password is wrong", async () => {
    const user = userEvent.setup();
    await reachChangeScreen(user);
    await user.type(screen.getByLabelText("Current password"), "not-the-temporary-one");
    await user.type(screen.getByLabelText("New password"), "a-brand-new-password");
    await user.type(screen.getByLabelText("Repeat the new password"), "a-brand-new-password");
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The current password is not correct.",
    );
  });

  async function fillChange(user: ReturnType<typeof userEvent.setup>, next: string) {
    await user.type(screen.getByLabelText("Current password"), temp.password);
    await user.type(screen.getByLabelText("New password"), next);
    await user.type(screen.getByLabelText("Repeat the new password"), next);
    await user.click(screen.getByRole("button", { name: "Save password" }));
  }

  it("shows a refused new password on its field", async () => {
    const user = userEvent.setup();
    await reachChangeScreen(user);
    // 12 characters long, so only the server refuses it
    server.use(
      http.post("*/api/v1/auth/password", () =>
        HttpResponse.json(
          {
            type: "urn:research-api:problem:invalid-input",
            title: "Unprocessable Content",
            status: 422,
            errors: [{ path: "/new", message: "That password is too common." }],
          },
          { status: 422, headers: { "content-type": "application/problem+json" } },
        ),
      ),
    );
    await fillChange(user, "password1234");
    expect(await screen.findByText("That password is too common.")).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toBeInvalid();
    expect(screen.getByLabelText("New password")).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says so when the server refuses for another reason", async () => {
    server.use(
      http.post("*/api/v1/auth/password", () =>
        HttpResponse.json({ detail: "x" }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    await reachChangeScreen(user);
    await fillChange(user, "a-brand-new-password");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The password could not be changed.",
    );
  });
});

describe("the layout", () => {
  beforeEach(() => {
    authMock.handle({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { username: ana.username, password: ana.password },
    });
  });

  it("links the four screens and marks the current one", async () => {
    const user = userEvent.setup();
    renderApp("/ask");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Ask",
      "Benchmarks",
      "Question sets",
      "Circuits",
    ]);
    expect(within(nav).getByRole("link", { name: "Ask" })).toHaveAttribute("aria-current", "page");
    await user.click(within(nav).getByRole("link", { name: "Question sets" }));
    expect(await screen.findByRole("heading", { name: "Question sets" })).toHaveFocus();
    expect(within(nav).getByRole("link", { name: "Question sets" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(document.title).toBe("Question sets | Research engine");
  });

  it("offers a skip link", async () => {
    renderApp("/benchmarks");
    expect(await screen.findByRole("link", { name: "Skip to content" })).toHaveAttribute(
      "href",
      "#main",
    );
  });

  it("shows a not-found screen inside the layout", async () => {
    renderApp("/nowhere");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("shows the service status from the API", async () => {
    renderApp("/ask");
    expect(
      await screen.findByRole("status", { name: "Service status: Ready (test data)" }),
    ).toBeInTheDocument();
  });

  it("shows when the service is starting or unreachable", async () => {
    server.use(
      http.get("*/api/v1/status", () =>
        HttpResponse.json({
          mode: "live",
          api_version: "0.1.0",
          started_at: "2026-01-01T00:00:00Z",
          uptime_s: 1,
          ready: false,
          ask: {
            state: "warming_up",
            reason: null,
            index_generation: null,
            queued: 0,
            busy: false,
          },
          bench: { running_job: null, queued_runs: 0, scoring: false, queued_scores: 0 },
          speech: { stt: "ready" },
        }),
      ),
    );
    const first = renderApp("/ask");
    expect(
      await screen.findByRole("status", { name: "Service status: Starting" }),
    ).toBeInTheDocument();
    first.router.dispose();
  });

  it("shows when the service is unreachable", async () => {
    server.use(
      http.get("*/api/v1/status", () => HttpResponse.json({ detail: "x" }, { status: 502 })),
    );
    renderApp("/ask");
    expect(
      await screen.findByRole("status", { name: "Service status: Unreachable" }),
    ).toBeInTheDocument();
  });

  it("signs out and returns to the login screen", async () => {
    const user = userEvent.setup();
    const app = renderApp("/ask");
    await user.click(await screen.findByRole("button", { name: "Ana Prueba's account" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/login");
    expect(authMock.signedInAs()).toBeNull();
  });

  it("moves focus to the heading of the login screen after signing out", async () => {
    const user = userEvent.setup();
    renderApp("/ask");
    await user.click(await screen.findByRole("button", { name: "Ana Prueba's account" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toHaveFocus();
  });

  it("stays on the page and says so when signing out fails", async () => {
    server.use(
      http.post("*/api/v1/auth/logout", () => HttpResponse.json({ detail: "x" }, { status: 500 })),
    );
    const user = userEvent.setup();
    const app = renderApp("/circuits");
    await user.click(await screen.findByRole("button", { name: "Ana Prueba's account" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not sign out. Try again.");
    expect(app.router.state.location.pathname).toBe("/circuits");
    expect(authMock.signedInAs()).toBe(ana.username);
    expect(screen.getByRole("heading", { name: "Circuits" })).toBeInTheDocument();
  });

  it("drops the sign-out failure message when the user moves to another screen", async () => {
    server.use(
      http.post("*/api/v1/auth/logout", () => HttpResponse.json({ detail: "x" }, { status: 500 })),
    );
    const user = userEvent.setup();
    renderApp("/circuits");
    await user.click(await screen.findByRole("button", { name: "Ana Prueba's account" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "Ask" }));
    expect(await screen.findByRole("heading", { name: "Ask" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("returns to the login screen when the session ends under an open page", async () => {
    const app = renderApp("/circuits");
    await screen.findByRole("status", { name: "Service status: Ready (test data)" });
    // the server ends the session, and the next refresh of the status badge is refused
    authMock.handle({ method: "POST", path: "/api/v1/auth/logout", csrfHeader: "t" });
    server.use(
      http.get("*/api/v1/status", () =>
        HttpResponse.json({ detail: "No session." }, { status: 401 }),
      ),
    );
    await app.queryClient.invalidateQueries();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/login");
    expect(app.router.state.location.search).toBe("?next=%2Fcircuits");
  });
});

describe("theme and language", () => {
  beforeEach(() => {
    authMock.handle({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { username: ana.username, password: ana.password },
    });
  });

  it("switches the theme and remembers it", async () => {
    const user = userEvent.setup();
    renderApp("/ask");
    await user.click(await screen.findByRole("button", { name: "Theme: System" }));
    await user.click(await screen.findByRole("menuitemradio", { name: "Dark" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(window.localStorage.getItem("re.theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Theme: Dark" })).toBeInTheDocument();
  });

  it("follows the system while the theme is system", async () => {
    renderApp("/ask");
    await screen.findByRole("heading", { name: "Ask" });
    setSystemDark(true);
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    setSystemDark(false);
    await waitFor(() => expect(document.documentElement).not.toHaveClass("dark"));
  });

  it("switches the language and remembers it", async () => {
    const user = userEvent.setup();
    renderApp("/ask");
    await user.click(await screen.findByRole("button", { name: "Language: English" }));
    await user.click(await screen.findByRole("menuitemradio", { name: "Español" }));
    expect(await screen.findByRole("heading", { name: "Preguntar" })).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("lang", "es");
    expect(window.localStorage.getItem("re.lang")).toBe("es");
    expect(screen.getByRole("navigation", { name: "Principal" })).toBeInTheDocument();
  });
});
