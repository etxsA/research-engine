import { describe, expect, it } from "vitest";
import { downsample, encodeWav, SPEECH_RATE } from "./wav";

function text(bytes: Uint8Array, from: number, to: number): string {
  return String.fromCharCode(...bytes.subarray(from, to));
}

describe("encodeWav", () => {
  it("writes a 16 kHz mono 16-bit PCM header and the samples", () => {
    const wav = encodeWav(new Float32Array([0, 1, -1, 0.5]));
    const view = new DataView(wav.buffer);
    expect(wav.length).toBe(44 + 8);
    expect(text(wav, 0, 4)).toBe("RIFF");
    expect(text(wav, 8, 16)).toBe("WAVEfmt ");
    expect(text(wav, 36, 40)).toBe("data");
    expect(view.getUint32(4, true)).toBe(36 + 8);
    expect(view.getUint16(20, true)).toBe(1);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(SPEECH_RATE);
    expect(view.getUint32(28, true)).toBe(SPEECH_RATE * 2);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(8);
    expect([0, 1, 2, 3].map((i) => view.getInt16(44 + i * 2, true))).toEqual([
      0, 0x7fff, -0x8000, 0x3fff,
    ]);
  });

  it("clips samples outside -1 and 1", () => {
    const view = new DataView(encodeWav(new Float32Array([3, -3])).buffer);
    expect(view.getInt16(44, true)).toBe(0x7fff);
    expect(view.getInt16(46, true)).toBe(-0x8000);
  });

  it("stays under the API's 2 MiB for 60 seconds", () => {
    expect(encodeWav(new Float32Array(60 * SPEECH_RATE)).length).toBeLessThan(2 * 1024 * 1024);
  });
});

describe("downsample", () => {
  it("averages 48 kHz down to 16 kHz", () => {
    const out = downsample(new Float32Array([1, 2, 3, 4, 5, 6]), 48_000, 16_000);
    expect(Array.from(out)).toEqual([2, 5]);
  });

  it("keeps samples at the same rate and handles a non-integer ratio", () => {
    const same = new Float32Array([1, 2]);
    expect(downsample(same, 16_000, 16_000)).toBe(same);
    expect(downsample(new Float32Array(44_100), 44_100, 16_000).length).toBe(16_000);
  });
});
