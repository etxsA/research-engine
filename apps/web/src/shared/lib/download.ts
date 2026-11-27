/**
 * Saving text the page already holds as a file, with the browser's own download. Nothing goes to
 * the server. Some browsers start the download after the click returns, so the object URL is kept
 * for a while before it is revoked.
 */

export const REVOKE_AFTER_MS = 10_000;

/** Download `blob` as a file named `name`. */
export function downloadBlob(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
}

/** Download `text` as a file named `name`, of MIME type `type`. */
export function downloadText(name: string, text: string, type: string): void {
  downloadBlob(name, new Blob([text], { type }));
}
