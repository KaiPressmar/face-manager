/** Copy plain text in browsers and embedded webviews with a legacy fallback. */
export async function copyTextToClipboard(value: string): Promise<void> {
  const text = String(value ?? "");
  if (!text) throw new Error("Es ist kein Dateipfad verfügbar.");

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Some embedded webviews expose Clipboard API but deny it at runtime.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (!document.execCommand("copy")) {
      throw new Error("Der Dateipfad konnte nicht kopiert werden.");
    }
  } finally {
    textarea.remove();
  }
}
