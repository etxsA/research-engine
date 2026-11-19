import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { ANA } from "@/test/auth-mock";
import { installFakeMic, type FakeMic } from "@/test/mic";
import { renderApp } from "@/test/render";
import { authMock, server, STATUS_BODY } from "@/test/server";
import { useAskStore } from "./store";

let mic: FakeMic;
afterEach(() => {
  mic.restore();
  useAskStore.setState({ question: "" });
});

interface Seen {
  contentType: string | null;
  bytes: number;
  lang: string | null;
}

function transcribes(text: string | Response = "what limits T1"): Seen {
  const seen: Seen = { contentType: null, bytes: 0, lang: null };
  server.use(
    http.post("*/api/v1/speech/transcriptions", async ({ request }) => {
      seen.contentType = request.headers.get("content-type");
      seen.bytes = (await request.arrayBuffer()).byteLength;
      seen.lang = new URL(request.url).searchParams.get("lang");
      if (text instanceof Response) return text;
      return HttpResponse.json({ text, language: "en", duration_s: 1 });
    }),
  );
  return seen;
}

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

/** Press, speak for a moment, press again. */
async function speak(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Speak" }));
  await screen.findByRole("button", { name: "Stop" });
  act(() => mic.emit(new Float32Array(48_000).fill(0.2)));
  await user.click(screen.getByRole("button", { name: "Stop" }));
}

describe("voice input on the Ask screen", () => {
  it("puts the transcript in the question box and frees the microphone", async () => {
    mic = installFakeMic();
    const seen = transcribes();
    const user = await openAsk();
    await speak(user);
    await waitFor(() =>
      expect(screen.getByLabelText("Your question")).toHaveValue("what limits T1"),
    );
    expect(seen.contentType).toBe("audio/wav");
    expect(seen.bytes).toBe(44 + 16_000 * 2);
    expect(seen.lang).toBe("en");
    expect(mic.tracks[0]?.stop).toHaveBeenCalled();
    expect(await screen.findByText(/added to the question box/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toHaveFocus();
  });

  it("appends to what is already typed", async () => {
    mic = installFakeMic();
    transcribes("in transmons?");
    const user = await openAsk();
    await user.type(screen.getByLabelText("Your question"), "What limits T1");
    await speak(user);
    await waitFor(() =>
      expect(screen.getByLabelText("Your question")).toHaveValue("What limits T1 in transmons?"),
    );
  });

  it("shows the recording state while the button is pressed", async () => {
    mic = installFakeMic();
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: "Speak" }));
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(stop).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Recording\./)).toBeInTheDocument();
  });

  it("says why a recording was refused by the server", async () => {
    mic = installFakeMic();
    transcribes(new HttpResponse(null, { status: 429 }) as Response);
    const user = await openAsk();
    await speak(user);
    expect(await screen.findByText(/Too many transcriptions/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toHaveValue("");
  });

  it("explains a blocked microphone", async () => {
    mic = installFakeMic({ failWith: "NotAllowedError" });
    const user = await openAsk();
    await user.click(screen.getByRole("button", { name: "Speak" }));
    expect(await screen.findByText(/Microphone access was blocked/)).toBeInTheDocument();
  });

  it("explains a page that is not a secure context, before any click", async () => {
    mic = installFakeMic({ secure: false });
    await openAsk();
    expect(await screen.findByText(/needs a secure page/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Speak" })).toBeDisabled();
  });

  it("turns the button off when the status says speech is unavailable", async () => {
    mic = installFakeMic();
    server.use(
      http.get("*/api/v1/status", () =>
        HttpResponse.json({ ...STATUS_BODY, speech: { stt: "unavailable" } }),
      ),
    );
    await openAsk();
    expect(await screen.findByText(/not set up on this server/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Speak" })).toBeDisabled();
  });
});
