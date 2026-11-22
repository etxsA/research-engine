import { describe, expect, it } from "vitest";
import {
  chunkText,
  detectLanguage,
  pickVoice,
  readingPlan,
  SENTENCE_CHARS,
  speakableText,
  splitSentences,
} from "./speakable";

const voice = (name: string, lang: string, localService: boolean) =>
  ({ name, lang, localService, default: false, voiceURI: name }) as SpeechSynthesisVoice;

describe("speakableText", () => {
  it("drops citations, single and joined", () => {
    const text =
      "T1 is limited by loss (40a228002007ca7f pages 3-3). Both agree (40a228002007ca7f pages 3-3, 1234567890abcdef pages 1-2).";
    expect(speakableText(text, "en")).toBe("T1 is limited by loss. Both agree.");
  });

  it("keeps a parenthesis that is not a citation", () => {
    expect(speakableText("The gap (about 5 GHz) matters.", "en")).toBe(
      "The gap (about 5 GHz) matters.",
    );
  });

  it("removes markdown syntax", () => {
    const text =
      "# Title\n\nSome **bold**, *italic* and `code` with a [link](https://example.org/x).\n\n- first item\n- second item\n\n1. one\n\n> quoted\n\n![pic](https://example.org/p.png)";
    expect(speakableText(text, "en")).toBe(
      "Title.\nSome bold, italic and code with a link.\nfirst item.\nsecond item.\none.\nquoted.\npic.",
    );
  });

  it("skips fenced code and table separators and reads table cells", () => {
    const text = "Before.\n\n```python\nx = 1\n```\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n";
    expect(speakableText(text, "en")).toBe("Before.\na, b.\n1, 2.");
  });

  it("says a short word instead of each formula, in the language of the text", () => {
    const en =
      "The energy is $E = \\hbar\\omega$ and $$\\int_0^1 x\\,dx$$ is simple. Also \\(a+b\\).";
    expect(speakableText(en, "en")).toBe(
      "The energy is equation and equation is simple. Also equation.",
    );
    const es = "La energía es $E = \\hbar\\omega$ en el sistema.";
    expect(speakableText(es)).toBe("La energía es ecuación en el sistema.");
  });

  it("reads an equation environment as one equation and joins neighbours", () => {
    const text = "See \\begin{equation} x^2 \\end{equation} for $a$ $b$ the rest.";
    expect(speakableText(text, "en")).toBe("See equation for equation the rest.");
  });

  it("leaves a lone dollar sign (a price) alone", () => {
    expect(speakableText("It costs $5 or 6 dollars.", "en")).toBe("It costs $5 or 6 dollars.");
  });

  it("is empty for empty or formula-less input", () => {
    expect(speakableText("")).toBe("");
    expect(speakableText("   \n\n ")).toBe("");
    expect(speakableText("(40a228002007ca7f pages 3-3)")).toBe("");
  });
});

describe("detectLanguage", () => {
  it("picks Spanish from stop words", () => {
    expect(detectLanguage("El tiempo de coherencia de los qubits es corto para la medición")).toBe(
      "es",
    );
  });
  it("picks Spanish from accents", () => {
    expect(detectLanguage("Resumen: coherencia cuántica")).toBe("es");
  });
  it("picks English otherwise", () => {
    expect(detectLanguage("The coherence time of the qubits is short")).toBe("en");
    expect(detectLanguage("")).toBe("en");
    expect(detectLanguage("T1 and T2 of a transmon, for the résumé")).toBe("en");
  });
});

describe("pickVoice", () => {
  const voices = [
    voice("remote-mx", "es-MX", false),
    voice("local-es", "es-ES", true),
    voice("local-mx", "es_MX", true),
    voice("local-en-gb", "en-GB", true),
    voice("remote-us", "en-US", false),
    voice("local-us", "en-US", true),
  ];
  it("prefers a local es-MX voice", () => {
    expect(pickVoice(voices, "es")?.name).toBe("local-mx");
  });
  it("prefers local over es-MX when it is the only difference", () => {
    expect(
      pickVoice(
        voices.filter((v) => v.name !== "local-mx"),
        "es",
      )?.name,
    ).toBe("local-es");
  });
  it("takes a remote es-MX over another remote Spanish", () => {
    const list = [voice("remote-es", "es-ES", false), voice("remote-mx", "es-MX", false)];
    expect(pickVoice(list, "es")?.name).toBe("remote-mx");
  });
  it("picks only voices of the language", () => {
    expect(pickVoice(voices, "en")?.name).toBe("local-us");
    expect(pickVoice([voice("fr", "fr-FR", true)], "en")).toBeUndefined();
    expect(pickVoice([], "es")).toBeUndefined();
  });
});

describe("chunkText", () => {
  it("packs sentences up to the size", () => {
    const text = "One two three. Four five six. Seven eight nine.";
    expect(chunkText(text, 30)).toEqual(["One two three. Four five six.", "Seven eight nine."]);
  });
  it("splits a sentence longer than the size at commas or spaces", () => {
    const long = `${"word, ".repeat(60)}end.`;
    const chunks = chunkText(long, 50);
    expect(chunks.length).toBeGreaterThan(5);
    for (const chunk of chunks) expect(chunk.length).toBeLessThanOrEqual(50);
    expect(chunks.join(" ").replace(/\s+/g, " ")).toBe(long.replace(/\s+/g, " "));
  });
  it("cuts a word longer than the size", () => {
    expect(chunkText("x".repeat(25), 10).every((c) => c.length <= 10)).toBe(true);
  });
  it("is empty for empty text", () => {
    expect(chunkText("")).toEqual([]);
  });
});

describe("splitSentences", () => {
  it("gives one piece per sentence and per line", () => {
    expect(splitSentences("One two. Three four!\nFive six? Seven")).toEqual([
      "One two.",
      "Three four!",
      "Five six?",
      "Seven",
    ]);
  });
  it("keeps every piece within about 300 characters", () => {
    const long = `${"word, ".repeat(120)}end.`;
    const pieces = splitSentences(long);
    expect(pieces.length).toBeGreaterThan(2);
    for (const piece of pieces) expect(piece.length).toBeLessThanOrEqual(SENTENCE_CHARS);
  });
  it("is empty for blank text", () => {
    expect(splitSentences(" \n ")).toEqual([]);
  });
});

describe("readingPlan", () => {
  it("gives the language and the cleaned text", () => {
    const answer = "El tiempo de coherencia de los qubits es corto (40a228002007ca7f pages 3-3).";
    expect(readingPlan(answer)).toEqual({
      language: "es",
      text: "El tiempo de coherencia de los qubits es corto.",
    });
  });
  it("says ecuación for a formula in a Spanish answer", () => {
    expect(readingPlan("La energía es $E=mc^2$ aquí.").text).toBe("La energía es ecuación aquí.");
  });
  it("has no text when nothing is left to say", () => {
    expect(readingPlan("(40a228002007ca7f pages 3-3)").text).toBe("");
  });
});
