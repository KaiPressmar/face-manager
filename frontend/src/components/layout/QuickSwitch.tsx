import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useModalFocus } from "../../hooks/useModalFocus";
import type { AppPage } from "../../utils/navigation";
import { PRIMARY_ITEMS, SECONDARY_ITEMS } from "./workspaceDestinations";
import "./quick-switch.css";

const destinations = [...PRIMARY_ITEMS, ...SECONDARY_ITEMS];

function searchable(text: string) {
  return text.toLocaleLowerCase("de").normalize("NFD").replace(/\p{M}/gu, "");
}

export default function QuickSwitch({ route, onSelect }: { route: AppPage; onSelect: (page: AppPage) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const dialog = useRef<HTMLElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const searchId = useId();
  const terms = searchable(query).trim().split(/\s+/).filter(Boolean);
  const matches = destinations.filter(({ label, hint, keywords }) => {
    const text = searchable(`${label} ${hint} ${keywords}`);
    return terms.every((term) => text.includes(term));
  });
  const close = () => setOpen(false);
  useModalFocus(dialog, close, true, open);

  useEffect(() => {
    const shortcut = (event: globalThis.KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey ||
        event.key.toLowerCase() !== "k" || event.repeat || event.defaultPrevented) return;
      const otherDialog = [...document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]')]
        .some((element) => element !== dialog.current && element.getClientRects().length &&
          !element.closest('[inert], [aria-hidden="true"]'));
      if (otherDialog) return;
      event.preventDefault();
      if (open) input.current?.focus();
      else {
        setQuery("");
        setOpen(true);
      }
    };
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  }, [open]);

  const select = (page: AppPage) => {
    close();
    onSelect(page);
  };

  const move = (event: KeyboardEvent<HTMLElement>) => {
    if (event.nativeEvent.isComposing) return;
    const buttons = [...(dialog.current?.querySelectorAll<HTMLButtonElement>(".quick-switch__destination") ?? [])];
    const index = buttons.findIndex((button) => button === document.activeElement);
    if (event.key === "ArrowDown" && index + 1 < buttons.length) {
      event.preventDefault();
      buttons[index + 1].focus();
    } else if (event.key === "ArrowUp" && index >= 0) {
      event.preventDefault();
      (buttons[index - 1] ?? input.current)?.focus();
    } else if (event.key === "Enter" && event.target === input.current && matches.length > 0) {
      event.preventDefault();
      select(matches[0].page);
    }
  };

  return <>
    <button type="button" className="quick-switch__trigger" aria-haspopup="dialog"
      aria-label="Schnellwechsel" aria-keyshortcuts="Control+K Meta+K" title="Schnellwechsel (Strg+K)"
      onClick={() => { setQuery(""); setOpen(true); }}>
      <span className="quick-switch__icon" aria-hidden="true" />
      <span>Schnellwechsel</span>
    </button>
    {open && createPortal(<div className="modal-backdrop">
      <section ref={dialog} className="quick-switch" role="dialog" aria-modal="true"
        aria-labelledby={titleId} onKeyDown={move}>
        <header>
          <h2 id={titleId}>Schnellwechsel</h2>
          <button type="button" className="secondary-button" onClick={close}>Schließen</button>
        </header>
        <label htmlFor={searchId}>Bereich finden</label>
        <input ref={input} data-initial-focus id={searchId} type="search" value={query}
          placeholder="Zum Beispiel Karte oder Sicherung"
          onChange={(event) => setQuery(event.target.value)} />
        {matches.length === 0
          ? <p role="status">Kein Bereich gefunden. Versuche einen anderen Begriff.</p>
          : <ul>{matches.map(({ page, label, icon, hint }) => <li key={page}>
            <button className="quick-switch__destination" type="button"
              aria-current={route === page ? "page" : undefined} onClick={() => select(page)}>
              <span className="quick-switch__destination-icon" aria-hidden="true">{icon}</span>
              <span><strong>{label}</strong><small>{hint}</small></span>
              {route === page && <span className="quick-switch__current">Aktuell</span>}
            </button>
          </li>)}</ul>}
      </section>
    </div>, document.body)}
  </>;
}
