/**
 * Copying text for the person. The clipboard API needs a secure context and the browser's
 * permission, and may refuse at any time; then the text is selected on the page instead (and the
 * element focused), so Ctrl+C or the context menu copies it. The caller says which of the two
 * happened, in words a screen reader announces.
 */

export type CopyOutcome = "copied" | "selected" | "failed";

/** Copy `text`; when the browser refuses, select the contents of `fallback` instead. */
export async function copyText(text: string, fallback?: HTMLElement | null): Promise<CopyOutcome> {
  // missing outside a secure context, and in some embedded browsers
  const clipboard = navigator.clipboard as Clipboard | undefined;
  try {
    if (!clipboard) throw new Error("no clipboard");
    await clipboard.writeText(text);
    return "copied";
  } catch {
    return selectContents(fallback) ? "selected" : "failed";
  }
}

/** Focus `element` and select everything in it. False when there is nothing to select. */
export function selectContents(element?: HTMLElement | null): boolean {
  const selection = window.getSelection();
  if (!element || !selection) return false;
  element.focus();
  const range = document.createRange();
  range.selectNodeContents(element);
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}
