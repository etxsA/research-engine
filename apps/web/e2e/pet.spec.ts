import { DANI, expect as baseExpect, expectNoAxeViolations, signIn, test } from "./fixtures";
import type { Page } from "@playwright/test";

// Qubi, the pet of the Ask screen, in a browser against the real API in fake mode. Like the
// other Ask tests, these sign in as `dani` and run one after the other.
const expect = baseExpect.configure({ timeout: 15_000 });

test.describe.configure({ mode: "default" });

// Chromium answers the microphone with a test tone.
test.use({
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
});

async function openAsk(page: Page) {
  await page.goto("/login");
  await signIn(page, DANI);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Ask" }).click();
  await expect(page.getByRole("heading", { name: "Ask", level: 1 })).toBeVisible();
}

const qubi = (page: Page) => page.locator("[data-pet='qubi']");

test("Qubi sits beside the question box, as a decorative drawing", async ({ page }) => {
  await openAsk(page);
  await expect(qubi(page)).toHaveAttribute("data-state", "idle");
  await expect(qubi(page).locator("svg")).toHaveAttribute("aria-hidden", "true");
  const pet = await qubi(page).boundingBox();
  const box = await page.getByLabel("Your question").boundingBox();
  expect(pet!.x + pet!.width).toBeLessThanOrEqual(box!.x);
  expect(pet!.width).toBeCloseTo(72, 0);
});

test("Qubi listens to the fake microphone", async ({ page, context }) => {
  await context.grantPermissions(["microphone"]);
  await openAsk(page);
  await page.getByRole("button", { name: "Speak" }).click();
  await expect(qubi(page)).toHaveAttribute("data-state", "listening");
  await expect(page.getByRole("status").filter({ hasText: "Qubi is listening." })).toBeAttached();
  await page.waitForTimeout(1000);
  await page.getByRole("button", { name: "Stop" }).click();
  // Other tests use the same speech desk at the same time, so the transcript itself is not checked.
  await expect(qubi(page)).not.toHaveAttribute("data-state", "listening");
});

test("Qubi thinks while a question is answered", async ({ page }) => {
  await openAsk(page);
  await page.getByLabel("Your question").fill("What limits T1 in transmons?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(qubi(page)).toHaveAttribute("data-state", "thinking");
  await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();
  await expect(qubi(page)).toHaveAttribute("data-state", "idle");
});

test("Qubi can be hidden and shown again, and the choice is kept", async ({ page }) => {
  await openAsk(page);
  await page.getByRole("button", { name: "Hide Qubi" }).click();
  await expect(qubi(page)).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "Show Qubi" })).toBeVisible();
  await expect(qubi(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Show Qubi" }).click();
  await expect(qubi(page)).toBeVisible();
});

for (const colorScheme of ["light", "dark"] as const) {
  test.describe(`in the ${colorScheme} theme`, () => {
    test.use({ colorScheme });

    test("has no accessibility violations at rest, listening, thinking and hidden", async ({
      page,
      context,
    }) => {
      await context.grantPermissions(["microphone"]);
      await openAsk(page);
      await expectNoAxeViolations(page);

      await page.getByRole("button", { name: "Speak" }).click();
      await expect(qubi(page)).toHaveAttribute("data-state", "listening");
      await expectNoAxeViolations(page);
      await page.getByRole("button", { name: "Stop" }).click();
      // Whatever the transcription does (the speech desk is shared with other tests), let it end.
      await expect(qubi(page)).toHaveAttribute("data-state", /^(idle|error)$/);

      await page.getByLabel("Your question").fill("What limits T1 in transmons?");
      await page.getByRole("button", { name: "Ask", exact: true }).click();
      await expect(qubi(page)).toHaveAttribute("data-state", "thinking");
      await expectNoAxeViolations(page);
      await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();

      await page.getByRole("button", { name: "Hide Qubi" }).click();
      await expectNoAxeViolations(page);
    });
  });
}

test.describe("a narrow screen", () => {
  test.use({ viewport: { width: 375, height: 760 } });

  test("keeps Qubi, the form and the buttons inside the page", async ({ page }) => {
    await openAsk(page);
    await expect(qubi(page)).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    const box = await page.getByLabel("Your question").boundingBox();
    expect(box!.width).toBeGreaterThan(200);
    await expectNoAxeViolations(page);
  });
});

test("Qubi speaks in Spanish", async ({ page }) => {
  await openAsk(page);
  await page.getByRole("button", { name: /Language/ }).click();
  await page.getByRole("menuitemradio", { name: "Español" }).click();
  await expect(qubi(page)).toHaveAttribute("data-state", "idle");
  await expect(page.getByRole("button", { name: "Ocultar a Qubi" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Qubi descansa." })).toBeAttached();
  await expectNoAxeViolations(page);
});
