import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { usePetPrefs } from "@/shared/pet/hidden";
import { usePlayback } from "@/shared/voice/playback";
import { ANA } from "@/test/auth-mock";
import { installFakeMic, type FakeMic } from "@/test/mic";
import { renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";
import { useAskStore } from "./store";

let mic: FakeMic | undefined;
afterEach(() => {
  mic?.restore();
  mic = undefined;
  usePetPrefs.setState({ hidden: false });
  usePlayback.setState({ state: "idle" });
  useAskStore.setState({ question: "", entries: [], selectedKey: null });
});

async function openAsk() {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
  renderApp("/ask");
  await screen.findByRole("heading", { name: "Ask", level: 1 });
  return userEvent.setup();
}
const qubi = () => document.querySelector<HTMLElement>("[data-pet]")!;

describe("Qubi on the Ask screen", () => {
  it("sits beside the question box and rests at first", async () => {
    await openAsk();
    expect(qubi()).toHaveAttribute("data-state", "idle");
    expect(qubi().closest("form")).toBeNull();
  });

  it("listens while the Speak button records, and writes down while it transcribes", async () => {
    mic = installFakeMic();
    server.use(
      http.post("*/api/v1/speech/transcriptions", async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
        return HttpResponse.json({ text: "hola", language: "es", duration_s: 1 });
      }),
    );
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: "Speak" }));
    await screen.findByRole("button", { name: "Stop" });
    act(() => mic!.emit(new Float32Array(48_000).fill(0.2)));
    await waitFor(() => expect(qubi()).toHaveAttribute("data-state", "listening"));
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(qubi()).toHaveAttribute("data-state", "transcribing"));
    await waitFor(() => expect(qubi()).toHaveAttribute("data-state", "idle"));
  });

  it("shows the microphone error", async () => {
    mic = installFakeMic({ failWith: "NotAllowedError" });
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: "Speak" }));
    await waitFor(() => expect(qubi()).toHaveAttribute("data-state", "error"));
  });

  it("speaks when the reader speaks, and can be hidden", async () => {
    const user = await openAsk();
    act(() => usePlayback.getState().setState("speaking"));
    expect(qubi()).toHaveAttribute("data-state", "speaking");
    await user.click(screen.getByRole("button", { name: "Hide Qubi" }));
    expect(document.querySelector("[data-pet]")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show Qubi" }));
    expect(qubi()).toHaveAttribute("data-state", "speaking");
  });
});
