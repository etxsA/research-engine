/**
 * remark-math closes a display block only at a line made of dollars alone. The converted papers
 * often close one in the middle of a line (`\end{equation}$$ where ...`) or open one with its
 * first line after the dollars (`$$\begin{equation}`, which the parser takes for a label and
 * drops), and then the rest of the page reads as one formula. The text is rewritten before it is
 * parsed so that the dollars of a display block stand on lines of their own:
 *
 *     $$\begin{equation}            $$
 *     x                             \begin{equation}
 *     \end{equation}$$ where y      x
 *                            ->     \end{equation}
 *                                   $$
 *
 *                                   where y
 *
 * Only a block that opens at the start of a line counts. A line with `$$...$$` inside, inline
 * math, code spans and fenced code are left as they were.
 */

const FENCE = /^\s*(```|~~~)/;
const OPEN = /^( {0,3})(\${2,})([^$]*)$/;

export function closeDisplayMathOnItsOwnLine(text: string): string {
  if (!text.includes("$$")) return text;
  const out: string[] = [];
  let fenced = false;
  let size = 0; // length of the dollars that opened the block, 0 outside one
  let opener = ""; // indentation of the line that opened the block
  const lines = text.split("\n");
  for (let n = 0; n < lines.length; n++) {
    const line = lines[n] ?? "";
    if (size === 0) {
      if (FENCE.test(line)) fenced = !fenced;
      const open = fenced ? null : OPEN.exec(line);
      if (!open) {
        out.push(line);
        continue;
      }
      const indent = open[1] ?? "";
      opener = indent;
      size = open[2]?.length ?? 2;
      const first = open[3]?.trim() ?? "";
      // The indentation stays on both lines, so a formula inside a list item stays inside it.
      if (first) out.push(`${indent}${open[2] ?? "$$"}`, `${indent}${first}`);
      else out.push(line);
      continue;
    }
    const closing = /^ {0,3}(\$+)\s*$/.exec(line);
    if (closing && (closing[1]?.length ?? 0) >= size) {
      size = 0;
      out.push(line);
      continue;
    }
    const index = line.indexOf("$".repeat(size));
    if (index === -1) {
      out.push(line);
      continue;
    }
    let end = index + size;
    while (line[end] === "$") end += 1;
    const before = line.slice(0, index).trimEnd();
    const after = line.slice(end).trim();
    if (before) out.push(before);
    out.push(opener + line.slice(index, end));
    // The text after it is read as a new line, since it may open the next block (`$$ $$\begin{...}`).
    if (after) {
      out.push("");
      lines.splice(n + 1, 0, opener + after);
    }
    size = 0;
  }
  return out.join("\n");
}
