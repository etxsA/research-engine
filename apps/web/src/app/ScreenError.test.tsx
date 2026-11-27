import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ANA } from "@/test/auth-mock";
import { preloadScreens, renderApp } from "@/test/render";
import { authMock } from "@/test/server";
import { reloadPage } from "@/shared/lib/page";

preloadScreens(() => import("@/features/ask/AskPage")); // the other two screens are mocked below

vi.mock("@/shared/lib/page", () => ({ reloadPage: vi.fn() }));

// One screen fails while it renders, another while its chunk is imported.
vi.mock("@/features/circuits/CircuitsPage", () => ({
  Component: () => {
    throw new Error("boom");
  },
}));
vi.mock("@/features/qsets/SetsPage", () => {
  throw new TypeError("Failed to fetch dynamically imported module");
});

describe("an error inside a screen", () => {
  beforeEach(() => {
    authMock.handle({
      method: "POST",
      path: "/api/v1/auth/login",
      body: { username: ANA.username, password: ANA.password },
    });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  it("replaces the screen and keeps the layout", async () => {
    renderApp("/circuits");
    expect(
      await screen.findByRole("heading", { name: "Something went wrong" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("This screen could not be shown.");
    expect(screen.getAllByRole("main")).toHaveLength(1);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Ask" })).toBeInTheDocument();
  });

  it("recovers by following the navigation", async () => {
    const user = userEvent.setup();
    renderApp("/circuits");
    await screen.findByRole("heading", { name: "Something went wrong" });
    const nav = screen.getByRole("navigation", { name: "Main" });
    await user.click(within(nav).getByRole("link", { name: "Ask" }));
    expect(await screen.findByRole("heading", { name: "Ask" })).toBeInTheDocument();
  });

  it("reloads the page on Try again when a chunk cannot be imported", async () => {
    const user = userEvent.setup();
    renderApp("/sets");
    await screen.findByRole("heading", { name: "Something went wrong" });
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(reloadPage).toHaveBeenCalledOnce();
  });
});
