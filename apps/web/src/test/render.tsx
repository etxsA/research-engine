import { render } from "@testing-library/react";
import { createMemoryRouter, type InitialEntry } from "react-router";
import { App, createApp, type AppInstance } from "@/app/App";

/**
 * Renders the whole app on an in-memory router at `path`, or at the last of several history
 * entries (an entry may carry router state, as a link with `state` leaves it).
 */
export function renderApp(path: string | InitialEntry[]): AppInstance {
  const initialEntries = typeof path === "string" ? [path] : path;
  const app = createApp((routes) =>
    createMemoryRouter(routes, { initialEntries, initialIndex: initialEntries.length - 1 }),
  );
  render(<App app={app} />);
  return app;
}
