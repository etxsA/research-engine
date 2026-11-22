/**
 * What "Read aloud" says: the answer as plain text, its language, the voice and the pieces it is
 * spoken in. Everything here is a pure function, so it is tested without a browser.
 */

export type SpeechLanguage = "es" | "en";

export const LOCALE: Record<SpeechLanguage, string> = { es: "es-MX", en: "en-US" };

/** Chrome stops an utterance after about 15 s, so the browser voice is queued in pieces this long. */
export const CHUNK_CHARS = 200;

/** The speech server takes one sentence per request, of at most about this many characters. */
export const SENTENCE_CHARS = 300;

const MATH_WORD: Record<SpeechLanguage, string> = { es: "ecuación", en: "equation" };

// A citation the answer prints: `(<16 hex> pages 3-4)`, or several joined by commas.
const CITATION = /\(\s*[0-9a-f]{16}\b[^()]*\)/gi;
const DISPLAY_MATH = [
  /\$\$[\s\S]*?\$\$/g,
  /\\\[[\s\S]*?\\\]/g,
  /\\begin\{([a-z*]+)\}[\s\S]*?\\end\{\1\}/gi,
];
const INLINE_MATH = [/\\\([\s\S]*?\\\)/g, /\$(?=\S)[^$\n]+?(?<=\S)\$/g];
const MATH_MARK = "\u0001";

/** Replaces every formula with `word` (a space when empty). Adjacent formulas become one. */
function replaceMath(text: string, word: string): string {
  let out = text;
  for (const pattern of [...DISPLAY_MATH, ...INLINE_MATH]) out = out.replace(pattern, MATH_MARK);
  return out.replace(new RegExp(`${MATH_MARK}(?:[\\s,;]*${MATH_MARK})*`, "g"), ` ${word} `);
}

/** Markdown syntax removed; lines that do not end a sentence get a full stop so pieces split well. */
function plainMarkdown(text: string): string {
  const lines: string[] = [];
  let fenced = false;
  for (const raw of text.split("\n")) {
    if (/^\s*(```|~~~)/.test(raw)) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;
    let line = raw
      .replace(/^\s*\|?(\s*:?-{2,}:?\s*\|)+\s*:?-*:?\s*$/, "") // table separator row
      .replace(/^\s{0,3}#{1,6}\s+/, "")
      .replace(/^\s*>+\s?/, "")
      .replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[\^[^\]]*\]/g, "")
      .replace(/<[^>\n]+>/g, "")
      .replace(/https?:\/\/\S+/g, "")
      .replace(/(\*{1,3}|_{1,3}|~~|`)/g, "")
      .replace(/\s*\|\s*/g, ", ")
      .replace(/^,\s*|,\s*$/g, "")
      .trim();
    if (line === "") {
      lines.push("");
      continue;
    }
    if (!/[.!?…:;,]$/.test(line)) line += ".";
    lines.push(line);
  }
  return lines.join("\n");
}

/** The text of an answer as it is spoken: no citations, no markdown, a short word for each formula. */
export function speakableText(answer: string, language?: SpeechLanguage): string {
  const withoutCitations = answer.replace(CITATION, " ");
  const lang = language ?? detectLanguage(replaceMath(withoutCitations, ""));
  const text = plainMarkdown(replaceMath(withoutCitations, MATH_WORD[lang]));
  return text
    .replace(/[ \t]+/g, " ")
    .replace(/ +([,.;:!?])/g, "$1")
    .replace(/\n{2,}/g, "\n")
    .trim();
}

const SPANISH_WORDS = new Set(
  "de la el que los las en y es un una por con para se del al como su más pero este esta son ser fue entre sobre también cuando".split(
    " ",
  ),
);
const ENGLISH_WORDS = new Set(
  "the of and is are to in that for with as on by this which from be it an at was were has have not or".split(
    " ",
  ),
);

/** Spanish when the text has Spanish stop words or accented letters, English otherwise. */
export function detectLanguage(text: string): SpeechLanguage {
  const lower = text.toLowerCase();
  const accents = (lower.match(/[áéíóúñ¿¡]/g) ?? []).length;
  let spanish = 0;
  let english = 0;
  for (const word of lower.match(/[a-záéíóúñü]+/g) ?? []) {
    if (SPANISH_WORDS.has(word)) spanish++;
    if (ENGLISH_WORDS.has(word)) english++;
  }
  if (accents > 0 && spanish >= english) return "es";
  if (spanish >= 2 && spanish > english) return "es";
  return "en";
}

/** The best voice for a language: of its language only, a local one first, then es-MX / en-US. */
export function pickVoice(
  voices: readonly SpeechSynthesisVoice[],
  language: SpeechLanguage,
): SpeechSynthesisVoice | undefined {
  const norm = (lang: string) => lang.replace("_", "-").toLowerCase();
  const target = norm(LOCALE[language]);
  const score = (voice: SpeechSynthesisVoice) =>
    (voice.localService ? 2 : 0) + (norm(voice.lang) === target ? 1 : 0);
  let best: SpeechSynthesisVoice | undefined;
  for (const voice of voices) {
    if (!norm(voice.lang).startsWith(language)) continue;
    if (best === undefined || score(voice) > score(best)) best = voice;
  }
  return best;
}

function splitLong(sentence: string, max: number): string[] {
  const pieces: string[] = [];
  let rest = sentence.trim();
  while (rest.length > max) {
    let cut = rest.lastIndexOf(",", max - 1);
    if (cut < max / 2) cut = rest.lastIndexOf(" ", max - 1);
    const end = cut > 0 ? cut + 1 : max;
    pieces.push(rest.slice(0, end).trim());
    rest = rest.slice(end).trim();
  }
  if (rest) pieces.push(rest);
  return pieces;
}

/** The text cut into sentences; one longer than `max` is cut at a comma or a space. */
export function splitSentences(text: string, max = SENTENCE_CHARS): string[] {
  return text
    .split(/\n+|(?<=[.!?…])\s+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .flatMap((s) => (s.length > max ? splitLong(s, max) : [s]));
}

/** Sentences packed into pieces of about `max` characters (a sentence is never cut unless it is longer). */
export function chunkText(text: string, max = CHUNK_CHARS): string[] {
  const chunks: string[] = [];
  let current = "";
  for (const sentence of splitSentences(text, max)) {
    if (current !== "" && current.length + 1 + sentence.length > max) {
      chunks.push(current);
      current = sentence;
    } else {
      current = current === "" ? sentence : `${current} ${sentence}`;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

export interface ReadingPlan {
  language: SpeechLanguage;
  /** The cleaned text, as it is spoken. Empty when nothing is left to say. */
  text: string;
}

/** An answer ready to speak. */
export function readingPlan(answer: string): ReadingPlan {
  const language = detectLanguage(replaceMath(answer.replace(CITATION, " "), ""));
  return { language, text: speakableText(answer, language) };
}
