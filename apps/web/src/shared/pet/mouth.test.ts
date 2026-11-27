import { describe, expect, it } from "vitest";
import { mouthFor } from "./mouth";

describe("mouthFor", () => {
  it.each([
    [0, "closed"],
    [0.1, "closed"],
    [0.149, "closed"],
    [0.15, "half"],
    [0.39, "half"],
    [0.4, "open"],
    [0.9, "open"],
    [1, "open"],
    [Number.NaN, "closed"],
    [-1, "closed"],
  ] as const)("level %s shows the %s mouth", (level, shape) => {
    expect(mouthFor(level)).toBe(shape);
  });

  it("holds the current shape inside the hysteresis band", () => {
    expect(mouthFor(0.16, "closed")).toBe("closed");
    expect(mouthFor(0.19, "closed")).toBe("half");
    expect(mouthFor(0.14, "half")).toBe("half");
    expect(mouthFor(0.11, "half")).toBe("closed");
    expect(mouthFor(0.41, "half")).toBe("half");
    expect(mouthFor(0.44, "half")).toBe("open");
    expect(mouthFor(0.39, "open")).toBe("open");
    expect(mouthFor(0.36, "open")).toBe("half");
  });
});
