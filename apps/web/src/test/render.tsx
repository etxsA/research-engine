import { render } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { App, createApp, type AppInstance } from "@/app/App";

/** Renders the whole app on an in-memory router at `path`. */
export function renderApp(path: string): AppInstance {
  const app = createApp((routes) => createMemoryRouter(routes, { initialEntries: [path] }));
  render(<App app={app} />);
  return app;
}

/**
 * Loads the lazy screens a file renders before its first test. A route's code is fetched on demand,
 * and the first fetch in a worker transforms the whole screen (components, icons, art), which on a
 * loaded machine can outlast the default `findBy` wait of one second and fail whichever test runs
 * first. Loading it here keeps that cost out of the test, and out of any wait.
 */
export function preloadScreens(...screens: (() => Promise<unknown>)[]): void {
  beforeAll(async () => {
    await Promise.all(screens.map((load) => load()));
  }, 60_000);
}
