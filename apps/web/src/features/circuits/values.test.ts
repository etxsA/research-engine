import {
  addMissingNames,
  bindingKey,
  parseStateInput,
  parseUnitaryInput,
  parseValues,
  symbolKey,
} from "./values";

describe("parseValues", () => {
  it("reads numbers as numbers and angles as text", () => {
    expect(parseValues("theta = \\pi/2\nphi=0.5\n\n k = -3 ")).toEqual({
      values: { theta: "\\pi/2", phi: 0.5, k: -3 },
      errors: [],
    });
  });

  it("keeps an angle that only looks like a number", () => {
    expect(parseValues("a = 1/2").values).toEqual({ a: "1/2" });
    expect(parseValues("a = 1e-3").values).toEqual({ a: 0.001 });
  });

  it("reports a line with no equals sign, no name or no value", () => {
    const { errors } = parseValues("theta\n= 3\nphi =");
    expect(errors).toEqual([
      { line: 1, code: "format", name: "theta" },
      { line: 2, code: "noName", name: "" },
      { line: 3, code: "noValue", name: "phi" },
    ]);
  });

  it("refuses a key that is not one symbol before the request, as the API would", () => {
    const { values, errors } = parseValues("thetta = 1\npi = 2\ne = 3\ntheta_12 = 4\ntheta = 5");
    expect(values).toEqual({ theta: 5 });
    expect(errors).toEqual([
      { line: 1, code: "notName", name: "thetta" },
      { line: 2, code: "notName", name: "pi" },
      { line: 3, code: "notName", name: "e" },
      { line: 4, code: "notName", name: "theta_12" },
    ]);
  });

  it("takes a subscript of two characters in braces, and pi or \\pi in a value", () => {
    expect(parseValues("\\theta_{12} = pi/4\nphi = \\pi/4")).toEqual({
      values: { "\\theta_{12}": "pi/4", phi: "\\pi/4" },
      errors: [],
    });
  });

  it("refuses a symbol named twice, also under two spellings", () => {
    const { values, errors } = parseValues(
      "theta = 1\n\\theta = 2\n{theta} = 3\n\\theta_{1} = 4\ntheta_1 = 5",
    );
    expect(values).toEqual({ theta: 1, "\\theta_{1}": 4 });
    expect(errors.map((error) => error.code)).toEqual(["duplicate", "duplicate", "duplicate"]);
  });
});

// The cases of quantum-sim's tests of `symbol_name` (packages/quantum-sim/tests/test_angles.py,
// "Binding names"), so the values box and the API agree on every key. Keep the two in step.
const NAMES: [string, string][] = [
  ["theta", "theta"],
  ["\\theta", "theta"],
  ["{\\theta}", "theta"],
  ["{ \\theta }", "theta"],
  [" {{\\theta}} ", "theta"],
  ["\\theta_1", "theta_1"],
  ["\\theta_{1}", "theta_1"],
  ["theta_{1}", "theta_1"],
  ["{\\theta_{1}}", "theta_1"],
  ["x", "x"],
  ["x_1", "x_1"],
  ["\\alpha_{12}", "alpha_12"],
  ["\\Gamma", "Gamma"],
  ["phi_{ab}", "phi_ab"],
  ["theta\n", "theta"],
  [" theta ", "theta"],
  ["\n\\theta_{1}\t", "theta_1"],
];

const NOT_NAMES = [
  "",
  "   ",
  "{}",
  "pi",
  "\\pi",
  "e",
  "E",
  "i",
  "j",
  "thetta",
  "\\x",
  "a\nb",
  "x y",
  "the{ta}",
  "\\\\theta",
  "$\\theta$",
  "theta_12",
  "theta_{123456789}",
  "\\theta _1",
  "x\nOPENQASM 2.0;\n//",
  "θ",
];

const GREEK =
  "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu " +
  "xi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega " +
  "Gamma Delta Theta Lambda Xi Sigma Upsilon Phi Psi Omega";

describe("symbolKey", () => {
  it.each(NAMES)("reads %j as the symbol %s", (key, name) => {
    expect(symbolKey(key)).toBe(name);
  });

  it.each(NOT_NAMES)("reads %j as no name", (key) => {
    expect(symbolKey(key)).toBeNull();
  });

  it("names every Greek word with and without a subscript, and one ASCII letter", () => {
    for (const word of GREEK.split(" ")) {
      expect(symbolKey(`\\${word}`)).toBe(word);
      expect(symbolKey(`\\${word}_1`)).toBe(`${word}_1`);
      expect(symbolKey(`\\${word}_{a1}`)).toBe(`${word}_a1`);
    }
    for (const letter of ["x", "k", "Z"]) expect(symbolKey(`${letter}_2`)).toBe(`${letter}_2`);
  });

  it("reads no name in a key longer than an angle", () => {
    expect(symbolKey("{".repeat(10) + "\\theta" + "}".repeat(10))).toBe("theta");
    expect(symbolKey("{".repeat(300) + "\\theta" + "}".repeat(300))).toBeNull();
    expect(symbolKey("{".repeat(500_000) + "x" + "}".repeat(500_000))).toBeNull();
  });

  it("counts the characters of a key and strips the white space as Python does", () => {
    // 250 astral characters are 500 code points (a name could not hold them anyway)
    expect(symbolKey(" ".repeat(499) + "x")).toBe("x");
    expect(symbolKey(" ".repeat(500) + "x")).toBeNull();
    expect(symbolKey("\u0085theta\u001c")).toBe("theta");
    expect(symbolKey("\ufefftheta")).toBeNull();
  });
});

describe("bindingKey", () => {
  it("keeps a key and writes a reader's name as a key the rule takes", () => {
    expect(bindingKey("theta")).toBe("theta");
    expect(bindingKey("theta_1")).toBe("theta_1");
    expect(bindingKey("theta_{12}")).toBe("theta_{12}");
    expect(bindingKey("theta_12")).toBe("theta_{12}");
    expect(bindingKey("alpha_ab")).toBe("alpha_{ab}");
    expect(bindingKey("\\theta")).toBe("\\theta");
  });
});

describe("addMissingNames", () => {
  it("adds a line for each name without one", () => {
    expect(addMissingNames("", ["theta", "phi"])).toBe("theta = \nphi = ");
    expect(addMissingNames("theta = 1", ["theta", "phi"])).toBe("theta = 1\nphi = ");
    expect(addMissingNames("theta = 1\n", ["\\theta"])).toBe("theta = 1\n");
  });

  it("writes a subscript of two or more characters in braces, so the line is a valid key", () => {
    // the simulator's unbound names are the reader's (theta_12), the export's are keys
    const text = addMissingNames("", ["theta_12", "phi_1"]);
    expect(text).toBe("theta_{12} = \nphi_1 = ");
    expect(parseValues(text).errors.map((error) => error.code)).toEqual(["noValue", "noValue"]);
    expect(addMissingNames("", ["theta_{12}"])).toBe("theta_{12} = ");
  });

  it("adds nothing for a symbol that already has a line under another spelling", () => {
    expect(addMissingNames("\\theta_{12} = 1", ["theta_12", "theta_{12}"])).toBe(
      "\\theta_{12} = 1",
    );
    expect(addMissingNames("", ["theta_12", "theta_{12}"])).toBe("theta_{12} = ");
  });
});

describe("parseStateInput", () => {
  it("accepts empty, a basis label and a list", () => {
    expect(parseStateInput("")).toEqual({ ok: true, value: undefined });
    expect(parseStateInput(" 010 ")).toEqual({ ok: true, value: "010" });
    expect(parseStateInput("[0, [0.5, 0.5]]")).toEqual({ ok: true, value: [0, [0.5, 0.5]] });
  });

  it("refuses anything else", () => {
    for (const bad of ["012", "ket", "[1, ", "{}", "0 1"]) {
      expect(parseStateInput(bad), bad).toEqual({ ok: false, code: "format" });
    }
  });
});

describe("parseUnitaryInput", () => {
  it("accepts a list of rows", () => {
    expect(parseUnitaryInput("[[0, 1], [1, 0]]")).toEqual({
      ok: true,
      value: [
        [0, 1],
        [1, 0],
      ],
    });
  });

  it("refuses text, an empty list and a flat list", () => {
    for (const bad of ["x", "[]", "[1, 2]", "{}"]) {
      expect(parseUnitaryInput(bad), bad).toEqual({ ok: false, code: "format" });
    }
  });
});
