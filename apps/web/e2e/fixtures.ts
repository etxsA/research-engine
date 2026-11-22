import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type APIRequestContext, type Page } from "@playwright/test";
import { ANA, createAuthMock, TEMP, type MockUser } from "../src/test/auth-mock";

export { ANA, TEMP };

/** A seeded member of the API in fake mode, used by the circuit and Ask screens' tests. */
export const DANI: MockUser = {
  username: "dani",
  password: "test-password-dani-03",
  display_name: "Dani Prueba",
  role: "member",
  must_change_password: false,
};

interface Fixtures {
  /**
   * Answers the sign-in endpoints with the shared mock, and /status with a fake-mode body (the
   * real one needs a real session, which the mock does not make); /health reaches the real API.
   * real-api.spec.ts reads the real /status after a real sign-in.
   */
  authMock: ReturnType<typeof createAuthMock>;
  /** A request context signed in as `dani` against the real API: the session cookie, the CSRF header and the Origin the API checks. */
  api: APIRequestContext;
}

const STATUS_BODY = {
  mode: "fake",
  api_version: "0.1.0",
  started_at: "2026-01-01T00:00:00Z",
  uptime_s: 12.5,
  ready: true,
  ask: { state: "ready", reason: null, index_generation: null, queued: 0, busy: false },
  bench: { running_job: null, queued_runs: 0, scoring: false, queued_scores: 0 },
  speech: { stt: "ready", tts: "ready" },
};

export const test = base.extend<Fixtures>({
  authMock: async ({ page }, provide) => {
    const mock = createAuthMock();
    await page.route("**/api/v1/status", (route) =>
      route.fulfill({ status: 200, json: STATUS_BODY }),
    );
    await page.route("**/api/v1/auth/**", async (route) => {
      const request = route.request();
      const body: unknown = request.postData()
        ? JSON.parse(request.postData() ?? "null")
        : undefined;
      const answer = mock.handle({
        method: request.method(),
        path: new URL(request.url()).pathname,
        body,
        csrfHeader: request.headers()["x-csrf-token"],
      });
      if (answer === null) return route.fallback();
      return route.fulfill({
        status: answer.status,
        headers: answer.headers,
        ...(answer.body === undefined ? {} : { body: JSON.stringify(answer.body) }),
      });
    });
    await provide(mock);
  },
  api: async ({ playwright, baseURL }, provide) => {
    const origin = baseURL ?? "";
    const signing = await playwright.request.newContext({
      baseURL: origin,
      extraHTTPHeaders: { Origin: origin },
    });
    const login = await signing.post("/api/v1/auth/login", {
      data: { username: DANI.username, password: DANI.password },
    });
    expect(login.status()).toBe(204);
    const state = await signing.storageState();
    await signing.dispose();
    const csrf = state.cookies.find((cookie) => cookie.name === "re_csrf")?.value ?? "";
    const signedIn = await playwright.request.newContext({
      baseURL: origin,
      storageState: state,
      extraHTTPHeaders: { Origin: origin, "X-CSRF-Token": csrf },
    });
    await provide(signedIn);
    await signedIn.dispose();
  },
});

export { expect };

export async function signIn(page: Page, user: MockUser, password = user.password) {
  await page.getByLabel("Username").fill(user.username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

/** Fails on any WCAG 2.2 A/AA violation of the page as it is now. */
export async function expectNoAxeViolations(page: Page) {
  // Let every color change finish: a color in the middle of a fade is not one of ours. Looping
  // animations (a spinner) never finish and do not change colors, so they are not waited for.
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every(
        (animation) =>
          animation.playState === "finished" ||
          animation.playState === "idle" ||
          animation.effect?.getComputedTiming().iterations === Infinity,
      ),
  );
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const summary = results.violations.flatMap((violation) =>
    violation.nodes.map(
      (node) =>
        `${violation.id}: ${node.target.join(" ")} (${node.any[0]?.message ?? node.failureSummary})`,
    ),
  );
  expect(summary).toEqual([]);
}
