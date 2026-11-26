import type { RefObject } from "react";

type Close = () => void;
const closeRequests = new Map<RefObject<HTMLElement>, Close>();

export function topNavigationDialog() {
  const dialogs = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]'));
  return dialogs.reverse().find((element) => element.getClientRects().length > 0
    && getComputedStyle(element).visibility === "visible"
    && !element.closest('[inert], [aria-hidden="true"]'));
}

export function registerNavigationDialog(dialog: RefObject<HTMLElement>, close: Close) {
  closeRequests.set(dialog, close);
  return () => { closeRequests.delete(dialog); };
}

/** An overlay can consume Back even if it is busy or asks about an unsaved draft. */
export function closeTopNavigationDialog() {
  const dialog = topNavigationDialog();
  if (!dialog) return false;
  for (const [ref, close] of closeRequests) {
    if (ref.current === dialog) {
      close();
      return true;
    }
  }
  return false;
}
