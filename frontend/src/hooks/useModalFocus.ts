import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const isVisible = (element: HTMLElement) =>
  element.getClientRects().length > 0 &&
  getComputedStyle(element).visibility === "visible" &&
  !element.closest("[inert]");

/** Keep keyboard focus inside an open dialog and return it to its opener. */
export function useModalFocus(
  dialogRef: RefObject<HTMLElement>,
  onClose: () => void,
  canClose = true,
  active = true,
) {
  const onCloseRef = useRef(onClose);
  const canCloseRef = useRef(canClose);
  const openerRef = useRef<HTMLElement | null>(null);
  const wasActiveRef = useRef(false);
  if (active && !wasActiveRef.current) {
    openerRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  }
  wasActiveRef.current = active;
  onCloseRef.current = onClose;
  canCloseRef.current = canClose;

  useEffect(() => {
    if (!active) return;
    // A page can request a dialog before its async data has arrived. Read the
    // live ref for every event rather than capturing a missing or replaced node.
    const onKeyDown = (event: KeyboardEvent) => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      const topmost = Array.from(
        document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]'),
      ).filter(isVisible).at(-1);
      if (topmost !== dialog) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        if (canCloseRef.current) onCloseRef.current();
      } else if (event.key === "Tab") {
        const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(isVisible);
        if (items.length === 0) {
          event.preventDefault();
          dialog.focus();
          return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        if (!dialog.contains(document.activeElement)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      if (openerRef.current?.isConnected && isVisible(openerRef.current)) openerRef.current.focus();
    };
  }, [dialogRef, active]);

  // Run after each render to cover a delayed dialog mount as well as focus
  // lost when a save temporarily disables the focused input.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!active || !canClose || !dialog || !isVisible(dialog) || dialog.contains(document.activeElement)) return;
    const topmost = Array.from(
      document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]'),
    ).filter(isVisible).at(-1);
    if (topmost !== dialog) return;
    if (!dialog.hasAttribute("tabindex")) dialog.tabIndex = -1;
    const target = dialog.querySelector<HTMLElement>("[data-initial-focus], [autofocus]");
    if (target && isVisible(target) && !target.hasAttribute("disabled")) {
      target.focus();
    } else {
      (Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).find(isVisible) ?? dialog).focus();
    }
  });
}
