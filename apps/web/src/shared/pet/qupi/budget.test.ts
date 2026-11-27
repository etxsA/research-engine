import { readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { QUPI_LAYERS } from "./rig";

const here = path.dirname(fileURLToPath(import.meta.url));
const BUDGET_BYTES = 150 * 1024;

describe("the Qupi art", () => {
  const files = readdirSync(here).filter((name) => name.endsWith(".webp"));

  it("has a 1x and a 2x file for every layer", () => {
    expect(files.sort()).toEqual(
      QUPI_LAYERS.flatMap((layer) => [`${layer}.webp`, `${layer}@2x.webp`]).sort(),
    );
  });

  it("stays under the 150 KB budget of the README", () => {
    const total = files.reduce((sum, name) => sum + statSync(path.join(here, name)).size, 0);
    expect(total).toBeLessThan(BUDGET_BYTES);
  });
});
