import { petState, type PetInput } from "./petState";

const REST: PetInput = { mic: "idle", ask: null, playback: "idle", unavailable: false };
const pet = (change: Partial<PetInput>) => petState({ ...REST, ...change });

describe("petState", () => {
  it.each<[string, Partial<PetInput>, string]>([
    ["nothing going on", {}, "idle"],
    ["a finished transcript", { mic: "done" }, "idle"],
    ["a finished or cancelled question", { ask: "done" }, "idle"],
    ["a cancelled question", { ask: "cancelled" }, "idle"],
    ["a question just sent", { ask: "sending" }, "idle"],
    ["recording", { mic: "recording" }, "listening"],
    ["transcribing", { mic: "transcribing" }, "transcribing"],
    ["a queued question", { ask: "queued" }, "waiting"],
    ["searching the papers", { ask: "retrieving" }, "thinking"],
    ["writing the answer", { ask: "answering" }, "thinking"],
    ["reading the answer aloud", { playback: "speaking" }, "speaking"],
    ["a microphone error", { mic: "error" }, "error"],
    ["a failed question", { ask: "failed" }, "error"],
    ["an engine that cannot answer", { unavailable: true }, "unavailable"],
  ])("%s", (_name, change, expected) => {
    expect(pet(change)).toBe(expected);
  });

  it("follows the priority error > speaking > listening > transcribing > thinking > waiting", () => {
    const all: PetInput = {
      mic: "error",
      ask: "failed",
      playback: "speaking",
      unavailable: true,
    };
    expect(petState(all)).toBe("error");
    expect(petState({ ...all, mic: "recording", ask: "answering" })).toBe("speaking");
    expect(petState({ ...all, mic: "recording", ask: "answering", playback: "idle" })).toBe(
      "listening",
    );
    expect(petState({ ...REST, mic: "transcribing", ask: "answering" })).toBe("transcribing");
    expect(petState({ ...REST, ask: "answering", unavailable: true })).toBe("thinking");
    expect(petState({ ...REST, ask: "queued", unavailable: true })).toBe("waiting");
    expect(petState({ ...REST, unavailable: true })).toBe("unavailable");
  });
});
