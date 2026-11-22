import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { $api } from "@/shared/api/client";
import { installFakeAudio, type FakeAudio } from "@/test/audio";
import { server, STATUS_BODY } from "@/test/server";
import { installFakeSpeech, voice, type FakeSpeech } from "@/test/speech";
import { serveSynthesis } from "@/test/synthesis";
import { ReadAloudButton } from "./ReadAloudButton";
import { stopReading } from "./reader";

const ENGLISH = "The coherence time of the qubits is short (40a228002007ca7f pages 3-3).";
const SPANISH = "El tiempo de coherencia de los qubits es corto para la medición.";

let audio: FakeAudio;
let speech: FakeSpeech;
beforeEach(() => {
  audio = installFakeAudio();
  speech = installFakeSpeech(vi, [
    voice("Paulina", "es-MX", true),
    voice("Samantha", "en-US", true),
  ]);
});
afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 20));
  stopReading();
  speech.restore();
});

/** The status the button reads: the speech server ready or not. */
function withTts(tts: "ready" | "unavailable") {
  const body = { ...STATUS_BODY, speech: { stt: "ready", tts } };
  server.use(http.get("*/api/v1/status", () => HttpResponse.json(body)));
  return body;
}

function renderButtons(tts: "ready" | "unavailable", ...buttons: [string, string][]) {
  const body = withTts(tts);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData($api.queryOptions("get", "/api/v1/status", {}).queryKey, body);
  return render(
    <QueryClientProvider client={client}>
      {buttons.map(([key, answer]) => (
        <section key={key} aria-label={key}>
          <ReadAloudButton entryKey={key} answer={answer} />
        </section>
      ))}
    </QueryClientProvider>,
  );
}

describe("ReadAloudButton with the speech server", () => {
  it("plays the cleaned sentences and stops on a second press", async () => {
    const user = userEvent.setup();
    const synthesis = serveSynthesis();
    renderButtons("ready", ["a", ENGLISH]);
    const button = screen.getByRole("button", { name: "Read aloud" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    await user.click(button);

    const stop = await screen.findByRole("button", { name: "Stop reading" });
    expect(stop).toHaveAttribute("aria-pressed", "true");
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    expect(screen.getByRole("status")).toHaveTextContent(/Reading the answer aloud\. Press/);
    expect(synthesis.calls.map((call) => call.text)).toEqual([
      "The coherence time of the qubits is short.",
    ]);
    expect(synthesis.calls[0]?.language).toBe("en");
    expect(speech.spoken).toHaveLength(0);

    await user.click(stop);
    expect(audio.sources[0]?.stopped).toBe(true);
    expect(await screen.findByRole("button", { name: "Read aloud" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("goes back to idle when the last sentence ends", async () => {
    const user = userEvent.setup();
    serveSynthesis();
    renderButtons("ready", ["a", ENGLISH]);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    act(() => audio.endCurrent());
    expect(await screen.findByRole("button", { name: "Read aloud" })).toBeInTheDocument();
  });

  it("reads a Spanish answer in Spanish", async () => {
    const user = userEvent.setup();
    const synthesis = serveSynthesis();
    renderButtons("ready", ["b", SPANISH]);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    await vi.waitFor(() => expect(synthesis.calls).toHaveLength(1));
    expect(synthesis.calls[0]?.language).toBe("es");
  });

  it("starting one answer stops the other", async () => {
    const user = userEvent.setup();
    serveSynthesis();
    renderButtons("ready", ["a", ENGLISH], ["b", SPANISH]);
    const first = screen.getByRole("region", { name: "a" });
    const second = screen.getByRole("region", { name: "b" });
    await user.click(within(first).getByRole("button", { name: "Read aloud" }));
    expect(await within(first).findByRole("button", { name: "Stop reading" })).toBeInTheDocument();
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));

    await user.click(within(second).getByRole("button", { name: "Read aloud" }));
    expect(second).toHaveTextContent("Stop reading");
    expect(first).not.toHaveTextContent("Stop reading");
    expect(audio.sources[0]?.stopped).toBe(true);
  });

  it("stops when the answer unmounts", async () => {
    const user = userEvent.setup();
    serveSynthesis();
    const { unmount } = renderButtons("ready", ["a", ENGLISH]);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    await vi.waitFor(() => expect(audio.sources).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    unmount();
    expect(audio.sources[0]?.stopped).toBe(true);
  });

  it("says the server is busy on a 429", async () => {
    const user = userEvent.setup();
    serveSynthesis({ failAt: { index: 0, status: 429 } });
    renderButtons("ready", ["a", ENGLISH]);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    expect(await screen.findByText(/Too many readings/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read aloud" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("says it failed on another error", async () => {
    const user = userEvent.setup();
    serveSynthesis({ failAt: { index: 0, status: 500 } });
    renderButtons("ready", ["a", ENGLISH]);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    expect(await screen.findByText(/could not be read aloud/)).toBeInTheDocument();
  });

  it("falls back to the voice of the browser on a 503 and says so", async () => {
    const user = userEvent.setup();
    serveSynthesis({ failAt: { index: 0, status: 503 } });
    renderButtons("ready", ["a", ENGLISH]);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    await vi.waitFor(() => expect(speech.spoken).toHaveLength(1));
    expect(speech.spoken[0]?.text).toBe("The coherence time of the qubits is short.");
    expect(speech.spoken[0]?.voice?.name).toBe("Samantha");
    expect(screen.getByRole("status")).toHaveTextContent(/speech server is not available/);
    expect(screen.getByRole("button", { name: "Stop reading" })).toBeInTheDocument();
  });

  it("is in Spanish on the Spanish screen", async () => {
    const { default: i18n } = await import("@/shared/i18n");
    await i18n.changeLanguage("es");
    renderButtons("ready", ["a", ENGLISH]);
    expect(screen.getByRole("button", { name: "Leer en voz alta" })).toBeInTheDocument();
  });

  it("renders no button when there is nothing to say", () => {
    renderButtons("ready", ["a", "(40a228002007ca7f pages 3-3)"]);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("ReadAloudButton without the speech server", () => {
  it("speaks with the browser and sends nothing to the API", async () => {
    const user = userEvent.setup();
    const synthesis = serveSynthesis();
    renderButtons("unavailable", ["a", ENGLISH]);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    await vi.waitFor(() => expect(speech.spoken).toHaveLength(1));
    expect(synthesis.calls).toHaveLength(0);
    expect(audio.sources).toHaveLength(0);
    expect(speech.spoken[0]?.lang).toBe("en-US");
    expect(screen.getByRole("status")).toHaveTextContent(/voice of your browser/);

    await user.click(screen.getByRole("button", { name: "Stop reading" }));
    expect(speech.cancel).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Read aloud" })).toBeInTheDocument();
  });

  it("says so when the browser fails to speak", async () => {
    const user = userEvent.setup();
    renderButtons("unavailable", ["a", ENGLISH]);
    await user.click(screen.getByRole("button", { name: "Read aloud" }));
    await vi.waitFor(() => expect(speech.spoken).toHaveLength(1));
    act(() => speech.failOne());
    expect(await screen.findByText(/could not be read aloud/)).toBeInTheDocument();
  });

  it("renders no button when the browser has no speech synthesis", () => {
    Reflect.deleteProperty(window, "speechSynthesis");
    renderButtons("unavailable", ["a", ENGLISH]);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
