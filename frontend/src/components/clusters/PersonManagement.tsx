import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { deletePerson, invalidatePersonsCache, listPersons, renamePerson } from "../../utils/api";
import type { PersonDirectoryEntry } from "../../utils/personDirectoryApi";
import "./person-management.css";

type Person = Pick<PersonDirectoryEntry, "id" | "name" | "photo_count" | "assignment_library_id">;

interface Props {
  person: Person;
  onClose: () => void;
  onChanged: (personId?: number) => void;
}

type PersonOption = Pick<Person, "id" | "name">;
type Mode = "rename" | "merge" | "remove";
type ArchiveDestination = Parameters<typeof deletePerson>[1];
type Confirmation =
  | { kind: "merge"; target: PersonOption }
  | { kind: "remove"; destination: ArchiveDestination };

const DESTINATIONS: Record<ArchiveDestination, string> = {
  unassigned: "Ohne Namen",
  unknown_person: "Unbekannte Personen",
  not_face: "Keine Gesichter",
};

const matchName = (name: string) => name.trim().toLocaleLowerCase("de");

export default function PersonManagement({ person, onClose, onChanged }: Props) {
  const { current: expectedLibraryId } = useRef(person.assignment_library_id);
  const dialogRef = useRef<HTMLElement>(null);
  const mounted = useRef(false);
  const saving = useRef(false);
  const titleId = useId();
  const [mode, setMode] = useState<Mode>("rename");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [name, setName] = useState(person.name);
  const [query, setQuery] = useState("");
  const [targetId, setTargetId] = useState<number | null>(null);
  const [destination, setDestination] = useState<ArchiveDestination | "">("");
  const [people, setPeople] = useState<PersonOption[] | null>(null);
  const [listError, setListError] = useState(false);
  const [listRevision, setListRevision] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useModalFocus(dialogRef, onClose, !busy);
  useLayoutEffect(() => {
    dialogRef.current?.querySelector<HTMLElement>("[data-initial-focus]")?.focus({ preventScroll: true });
  }, [mode, confirmation]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    void listPersons().then((items: PersonOption[]) => {
      if (active) setPeople(items.filter(({ id }) => id !== person.id));
    }).catch(() => {
      if (active) setListError(true);
    });
    return () => { active = false; };
  }, [person.id, listRevision]);

  const proposedName = name.trim();
  const match = people?.find((item) => matchName(item.name) === matchName(proposedName));
  const target = people?.find(({ id }) => id === targetId);
  const searching = matchName(query);
  const matches = people?.filter((item) => item.id === targetId || matchName(item.name).includes(searching));
  const photoCount = person.photo_count.toLocaleString("de-DE");
  const photoScope = photoCount + (person.photo_count === 1 ? " Foto im Profil" : " Fotos im Profil");

  const changeMode = (next: Mode) => {
    setError(null);
    setConfirmation(null);
    setMode(next);
  };

  const reloadPeople = () => {
    setError(null);
    setListError(false);
    setPeople(null);
    setConfirmation(null);
    invalidatePersonsCache();
    setListRevision((value) => value + 1);
  };

  const write = async (action: () => Promise<number | undefined>) => {
    if (saving.current) return;
    saving.current = true;
    setError(null);
    setBusy(true);
    try {
      const nextId = await action();
      if (mounted.current) onChanged(nextId);
    } catch (failure) {
      if (mounted.current) {
        setError(failure instanceof Error ? failure.message : "Die Änderung konnte nicht gespeichert werden.");
      }
    } finally {
      saving.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (saving.current) return;
    setError(null);
    if (confirmation?.kind === "merge") {
      const { target } = confirmation;
      void write(async () => (await renamePerson(person.id, target.name, target.id, expectedLibraryId)).person_id);
    } else if (confirmation?.kind === "remove") {
      const { destination } = confirmation;
      void write(async () => {
        await deletePerson(person.id, destination, expectedLibraryId);
        return undefined;
      });
    } else if (mode === "rename" && people && proposedName && proposedName !== person.name) {
      if (match) setConfirmation({ kind: "merge", target: match });
      else void write(async () => (await renamePerson(person.id, proposedName, null, expectedLibraryId)).person_id);
    } else if (mode === "merge" && target) {
      setConfirmation({ kind: "merge", target });
    } else if (mode === "remove" && destination) {
      setConfirmation({ kind: "remove", destination });
    }
  };

  const title = confirmation?.kind === "merge" ? "Personen zusammenführen"
    : confirmation?.kind === "remove" ? "Person entfernen" : "Person verwalten";
  const actionLabel = confirmation?.kind === "merge" ? "Personen zusammenführen"
    : confirmation?.kind === "remove" ? "Person entfernen"
      : mode === "merge" || match && mode === "rename" ? "Zusammenführung prüfen"
        : mode === "remove" ? "Entfernen prüfen" : "Name speichern";
  const canSubmit = confirmation || (
    mode === "rename" ? people && proposedName && proposedName !== person.name
      : mode === "merge" ? target : destination
  );

  return (
    <div className="modal-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !saving.current) onClose();
    }}>
      <section className="cluster-action-modal cluster-action-modal--narrow person-management" ref={dialogRef}
        role="dialog" aria-modal="true" aria-labelledby={titleId} aria-busy={busy}>
        <header className="cluster-action-modal__header">
          <div>
            <h2 id={titleId} tabIndex={-1} data-initial-focus={confirmation ? true : undefined}>{title}</h2>
            <p>{person.name}</p>
          </div>
          <button type="button" className="modal-close-button" onClick={onClose} disabled={busy} aria-label="Schließen">×</button>
        </header>

        {!confirmation && <nav className="person-management__modes" aria-label="Personenaktionen">
          {([
            ["rename", "Name ändern"],
            ["merge", "Zusammenführen"],
            ["remove", "Entfernen"],
          ] as const).map(([key, label]) => (
            <button type="button" className="secondary-button" key={key} aria-pressed={mode === key}
              disabled={busy} onClick={() => changeMode(key)}>{label}</button>
          ))}
        </nav>}

        <form className="person-management__form" onSubmit={submit}>
          {confirmation?.kind === "merge" ? (
            <div className="person-management__confirmation">
              <p><strong>{person.name}</strong> wird mit <strong>{confirmation.target.name}</strong> zusammengeführt.</p>
              <p>{photoScope}. Alle zugeordneten Gesichter wechseln zu „{confirmation.target.name}“. Dieser Name bleibt sichtbar.</p>
            </div>
          ) : confirmation?.kind === "remove" ? (
            <div className="person-management__confirmation">
              <p><strong>{person.name}</strong> wird aus dem Personenverzeichnis entfernt.</p>
              <p>{photoScope}. Zugeordnete Gesichter wechseln zu „{DESTINATIONS[confirmation.destination]}“. Die Originalfotos bleiben in der Bibliothek.</p>
            </div>
          ) : mode === "rename" ? (
            <>
              <label className="person-management__field">
                Personenname
                <input type="text" className="cluster-assignment-form__input" value={name}
                  onChange={(event) => setName(event.target.value)} disabled={busy} data-initial-focus />
              </label>
              {match && proposedName !== person.name && <p role="status">„{match.name}“ ist schon vergeben. Du kannst die Zusammenführung prüfen.</p>}
            </>
          ) : mode === "merge" ? (
            <>
              <p>Wähle die Person, deren Name erhalten bleiben soll.</p>
              {(people?.length ?? 0) > 15 && <label className="person-management__field">
                Personen finden
                <input type="search" className="cluster-assignment-form__input" value={query}
                  onChange={(event) => setQuery(event.target.value)} disabled={busy} />
              </label>}
              <label className="person-management__field">
                Zielperson
                <select className="app-select" value={targetId ?? ""} disabled={busy || !matches?.length}
                  onChange={(event) => setTargetId(event.target.value ? Number(event.target.value) : null)} data-initial-focus>
                  <option value="">Person auswählen</option>
                  {matches?.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
                </select>
              </label>
              {people && matches?.length === 0 && <p role="status">{query.trim() ? "Keine passende Person gefunden." : "Es gibt keine weitere Person zum Zusammenführen."}</p>}
            </>
          ) : (
            <label className="person-management__field">
              Wohin sollen die zugeordneten Gesichter?
              <select className="app-select" value={destination} disabled={busy} data-initial-focus
                onChange={(event) => setDestination(event.target.value as ArchiveDestination | "")}>
                <option value="">Bereich auswählen</option>
                {Object.entries(DESTINATIONS).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
              </select>
            </label>
          )}

          {!confirmation && mode !== "remove" && !people && !listError && <p role="status">Personen werden geladen …</p>}
          {!confirmation && mode !== "remove" && listError && <p role="alert">Die Personenliste konnte nicht geladen werden.</p>}
          {error && <p role="alert" className="person-management__error">{error}</p>}
          {(mode !== "remove" && (error || listError)) && (
            <button type="button" className="secondary-button person-management__retry" onClick={reloadPeople} disabled={busy}>
              Personenliste erneut laden
            </button>
          )}

          <footer className="person-management__actions">
            {confirmation && <button type="button" className="secondary-button" onClick={() => {
              setError(null);
              setConfirmation(null);
            }} disabled={busy}>Zurück</button>}
            <button className="primary-button" data-destructive={confirmation?.kind === "remove" || undefined}
              type="submit" disabled={!canSubmit || busy}>
              {busy ? "Wird gespeichert …" : actionLabel}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
