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
const qupi = () => document.querySelector<HTMLElement>("[data-pet]")!;

describe("Qupi on the Ask screen", () => {
  it("sits beside the question box and rests at first", async () => {
    await openAsk();
    expect(qupi()).toHaveAttribute("data-state", "idle");
    expect(qupi().closest("form")).toBeNull();
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
    await waitFor(() => expect(qupi()).toHaveAttribute("data-state", "listening"));
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(qupi()).toHaveAttribute("data-state", "transcribing"));
    await waitFor(() => expect(qupi()).toHaveAttribute("data-state", "idle"));
  });

  it("shows the microphone error", async () => {
    mic = installFakeMic({ failWith: "NotAllowedError" });
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: "Speak" }));
    await waitFor(() => expect(qupi()).toHaveAttribute("data-state", "error"));
  });

  it("speaks when the reader speaks, and can be hidden", async () => {
    const user = await openAsk();
    act(() => usePlayback.getState().setState("speaking"));
    expect(qupi()).toHaveAttribute("data-state", "speaking");
    await user.click(screen.getByRole("button", { name: "Hide Qupi" }));
    expect(document.querySelector("[data-pet]")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show Qupi" }));
    expect(qupi()).toHaveAttribute("data-state", "speaking");
  });
});
