import { afterEach, describe, expect, it, vi } from "vitest";
import { installFakeMic, type FakeMic } from "@/test/mic";
import { MicError, micProblem, micSupport, startRecording, WORKLET_URL } from "./recorder";

let mic: FakeMic;
afterEach(() => mic.restore());

const block = (value: number, length = 4800) => new Float32Array(length).fill(value);

describe("startRecording", () => {
  it("records, returns a 16 kHz WAV and frees the microphone", async () => {
    mic = installFakeMic();
    const levels: number[] = [];
    const recording = await startRecording({ onLevel: (l) => levels.push(l) });
    mic.emit(block(0.25));
    mic.emit(block(0.25));
    const clip = await recording.stop();
    expect(mic.contexts[0]?.modules).toEqual([WORKLET_URL]);
    expect(clip.seconds).toBeCloseTo(0.2, 5);
    expect(clip.wav.length).toBe(44 + 3200 * 2);
    expect(levels[0]).toBeCloseTo(1);
    expect(mic.tracks[0]?.stop).toHaveBeenCalledTimes(1);
    expect(mic.contexts[0]?.closed).toBe(true);
  });

  it("asks to stop at the limit and never returns more than the limit", async () => {
    mic = installFakeMic({ sampleRate: 16_000 });
    const onLimit = vi.fn();
    const recording = await startRecording({ maxSeconds: 1, onLimit });
    mic.emit(block(0.1, 12_000));
    expect(onLimit).not.toHaveBeenCalled();
    mic.emit(block(0.1, 12_000));
    mic.emit(block(0.1, 12_000));
    expect(onLimit).toHaveBeenCalledTimes(1);
    expect((await recording.stop()).seconds).toBe(1);
  });

  it("cancel frees the microphone and ignores later audio", async () => {
    mic = installFakeMic();
    const onLevel = vi.fn();
    const recording = await startRecording({ onLevel });
    recording.cancel();
    recording.cancel();
    mic.emit(block(0.5));
    expect(onLevel).not.toHaveBeenCalled();
    expect(mic.tracks[0]?.stop).toHaveBeenCalledTimes(1);
  });
});

describe("microphone problems", () => {
  it.each([
    ["NotAllowedError", "denied"],
    ["NotFoundError", "missing"],
    ["NotReadableError", "busy"],
    ["SomethingElse", "other"],
  ])("%s becomes %s", async (name, problem) => {
    mic = installFakeMic({ failWith: name });
    await expect(startRecording()).rejects.toMatchObject({ problem });
  });

  it("refuses on an insecure page without asking for the microphone", async () => {
    mic = installFakeMic({ secure: false });
    expect(micSupport()).toBe("insecure");
    await expect(startRecording()).rejects.toMatchObject({ problem: "insecure" });
    expect(mic.getUserMedia).not.toHaveBeenCalled();
  });

  it("says unsupported without the audio APIs", () => {
    mic = installFakeMic();
    Reflect.deleteProperty(globalThis, "AudioWorkletNode");
    expect(micSupport()).toBe("unsupported");
  });

  it("maps errors by name", () => {
    mic = installFakeMic();
    expect(micProblem(new MicError("busy"))).toBe("busy");
    expect(micProblem("nope")).toBe("other");
  });
});
