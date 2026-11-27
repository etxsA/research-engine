import { DANI, expect as baseExpect, expectNoAxeViolations, signIn, test } from "./fixtures";
import type { Page } from "@playwright/test";

// Qupi, the pet of the Ask screen, in a browser against the real API in fake mode. Like the
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

const qupi = (page: Page) => page.locator("[data-pet='qupi']");

test("Qupi sits beside the question box, as a decorative drawing", async ({ page }) => {
  await openAsk(page);
  await expect(qupi(page)).toHaveAttribute("data-state", "idle");
  const images = qupi(page).locator("img");
  await expect(images).toHaveCount(9);
  for (const image of await images.all()) await expect(image).toHaveAttribute("alt", "");
  const pet = await qupi(page).boundingBox();
  const box = await page.getByLabel("Your question").boundingBox();
  expect(pet!.x + pet!.width).toBeLessThanOrEqual(box!.x);
  expect(pet!.width).toBeCloseTo(120, 0);
  expect(pet!.height).toBeCloseTo(120, 0);
});

test("Qupi is nine WebP layers, stacked in order, that all load", async ({ page }) => {
  await openAsk(page);
  const layers = await qupi(page)
    .locator("img")
    .evaluateAll((nodes) =>
      (nodes as HTMLImageElement[]).map((node) => ({
        layer: node.dataset.layer,
        loaded: node.complete && node.naturalWidth > 0,
        square: node.naturalWidth === node.naturalHeight,
        opacity: getComputedStyle(node).opacity,
      })),
    );
  expect(layers.map((item) => item.layer)).toEqual([
    "tail",
    "body",
    "ear-left",
    "ear-right",
    "head",
    "mark",
    "eyelids",
    "mouth-closed",
    "mouth-half",
  ]);
  expect(layers.every((item) => item.loaded && item.square)).toBe(true);
  // At rest the eyes are open and the open smile shows: the three overlays are hidden.
  const hidden = layers.filter((item) =>
    ["eyelids", "mouth-closed", "mouth-half"].includes(item.layer ?? ""),
  );
  expect(hidden.map((item) => item.opacity)).toEqual(["0", "0", "0"]);
});

test.describe("with motion welcome", () => {
  test.use({ reducedMotion: "no-preference" });

  test("Qupi breathes at rest, and the two orbit qubits circle while it thinks", async ({
    page,
  }) => {
    await openAsk(page);
    await expect(qupi(page)).toHaveAttribute("data-motion", "full");
    const running = (name: string) =>
      qupi(page)
        .locator(name)
        .evaluate((node) =>
          node.getAnimations().some((animation) => animation.playState === "running"),
        );
    await expect.poll(() => running(".qupi-l-body")).toBe(true);
    await expect.poll(() => running(".qupi-l-tail")).toBe(true);

    await page.getByLabel("Your question").fill("What limits T1 in transmons?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(qupi(page)).toHaveAttribute("data-state", "thinking");
    await expect(qupi(page).locator(".qupi-orbit circle[fill='#2dd4bf']").first()).toBeAttached();
    await expect(qupi(page).locator(".qupi-orbit circle[fill='#a78bfa']").first()).toBeAttached();
  });
});

test("Qupi keeps still when reduced motion is asked for", async ({ page }) => {
  await openAsk(page);
  await expect(qupi(page)).toHaveAttribute("data-motion", "still");
  const running = await qupi(page).evaluate((node) => node.getAnimations({ subtree: true }).length);
  expect(running).toBe(0);
});

test("the mouth and eyelid overlays follow data-mouth and data-blink", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" }); // no blink timer runs on its own
  await openAsk(page);
  const opacities = () =>
    qupi(page).evaluate((node) => {
      const read = (name: string) =>
        Number(getComputedStyle(node.querySelector(`.qupi-l-${name}`)!).opacity);
      return { lids: read("eyelids"), closed: read("mouth-closed"), half: read("mouth-half") };
    });
  const setup = (state: string, mouth: string | null, blink: boolean) =>
    qupi(page).evaluate(
      (node, [nextState, nextMouth, nextBlink]) => {
        // The styles are read, not the app: state, mouth and blink are set as QupiPet would.
        node.setAttribute("data-state", nextState);
        node.setAttribute("data-motion", "still");
        const wrap = node.parentElement!;
        if (nextMouth) wrap.dataset.mouth = nextMouth;
        else delete wrap.dataset.mouth;
        const lids = node.querySelector<HTMLElement>(".qupi-l-eyelids")!;
        if (nextBlink) lids.dataset.blink = "true";
        else delete lids.dataset.blink;
      },
      [state, mouth, blink] as const,
    );

  await setup("speaking", "closed", false);
  expect(await opacities()).toEqual({ lids: 0, closed: 1, half: 0 });
  await setup("speaking", "half", false);
  expect(await opacities()).toEqual({ lids: 0, closed: 0, half: 1 });
  await setup("speaking", "open", false);
  expect(await opacities()).toEqual({ lids: 0, closed: 0, half: 0 });
  await setup("speaking", "closed", true);
  expect((await opacities()).lids).toBe(1);
  await setup("idle", null, true);
  expect(await opacities()).toEqual({ lids: 1, closed: 0, half: 0 });
  await setup("unavailable", null, false);
  expect((await opacities()).lids).toBe(1);
});

test("Qupi listens to the fake microphone", async ({ page, context }) => {
  await context.grantPermissions(["microphone"]);
  await openAsk(page);
  await page.getByRole("button", { name: "Speak" }).click();
  await expect(qupi(page)).toHaveAttribute("data-state", "listening");
  await expect(page.getByRole("status").filter({ hasText: "Qupi is listening." })).toBeAttached();
  await page.waitForTimeout(1000);
  await page.getByRole("button", { name: "Stop" }).click();
  // Other tests use the same speech desk at the same time, so the transcript itself is not checked.
  await expect(qupi(page)).not.toHaveAttribute("data-state", "listening");
});

test("Qupi thinks while a question is answered", async ({ page }) => {
  await openAsk(page);
  await page.getByLabel("Your question").fill("What limits T1 in transmons?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(qupi(page)).toHaveAttribute("data-state", "thinking");
  await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();
  await expect(qupi(page)).toHaveAttribute("data-state", "idle");
});

test("Qupi can be hidden and shown again, and the choice is kept", async ({ page }) => {
  await openAsk(page);
  await page.getByRole("button", { name: "Hide Qupi" }).click();
  await expect(qupi(page)).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "Show Qupi" })).toBeVisible();
  await expect(qupi(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Show Qupi" }).click();
  await expect(qupi(page)).toBeVisible();
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
      await expect(qupi(page)).toHaveAttribute("data-state", "listening");
      await expectNoAxeViolations(page);
      await page.getByRole("button", { name: "Stop" }).click();
      // Whatever the transcription does (the speech desk is shared with other tests), let it end.
      await expect(qupi(page)).toHaveAttribute("data-state", /^(idle|error)$/);

      await page.getByLabel("Your question").fill("What limits T1 in transmons?");
      await page.getByRole("button", { name: "Ask", exact: true }).click();
      await expect(qupi(page)).toHaveAttribute("data-state", "thinking");
      await expectNoAxeViolations(page);
      await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();

      await page.getByRole("button", { name: "Hide Qupi" }).click();
      await expectNoAxeViolations(page);
    });
  });
}

test.describe("a narrow screen", () => {
  test.use({ viewport: { width: 375, height: 760 } });

  test("keeps Qupi, the form and the buttons inside the page", async ({ page }) => {
    await openAsk(page);
    await expect(qupi(page)).toBeVisible();
    const pet = await qupi(page).boundingBox();
    expect(pet!.width).toBeCloseTo(80, 0);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    const box = await page.getByLabel("Your question").boundingBox();
    expect(box!.width).toBeGreaterThan(200);
    await expectNoAxeViolations(page);
  });
});

test("Qupi speaks in Spanish", async ({ page }) => {
  await openAsk(page);
  await page.getByRole("button", { name: /Language/ }).click();
  await page.getByRole("menuitemradio", { name: "Español" }).click();
  await expect(qupi(page)).toHaveAttribute("data-state", "idle");
  await expect(page.getByRole("button", { name: "Ocultar a Qupi" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Qupi descansa." })).toBeAttached();
  await expectNoAxeViolations(page);
});
