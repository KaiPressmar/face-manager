import React, { useEffect, useId, useMemo, useState } from "react";

export interface PersonOption {
  id: number;
  name: string;
}

interface Props {
  people: PersonOption[];
  peopleStatus: "idle" | "loading" | "ready" | "error";
  busy: boolean;
  faceCount: number;
  wholeGroup: boolean;
  currentPersonName?: string;
  explicitScope?: boolean;
  onRetry: () => void;
  onAssign: (name: string) => void;
}

const personKey = (name: string) => name.trim().normalize("NFC").toLocaleLowerCase("de");

const PersonAssignment: React.FC<Props> = ({
  people, peopleStatus, busy, faceCount, wholeGroup, currentPersonName, explicitScope, onRetry, onAssign,
}) => {
  const listId = useId();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activePersonId, setActivePersonId] = useState<number | null>(null);

  const name = query.trim();
  const search = personKey(name);
  const exact = people.find((person) => personKey(person.name) === search);
  const options = useMemo(() => {
    const matches = search ? people.filter((person) => personKey(person.name).includes(search)) : people;
    return (exact ? [exact, ...matches.filter((person) => person.id !== exact.id)] : matches).slice(0, 8);
  }, [people, search, exact]);

  const disabled = busy || faceCount === 0;
  const expanded = open && !disabled && options.length > 0;
  const activeIndex = options.findIndex((person) => person.id === activePersonId);
  const highlighted = expanded ? options[activeIndex] : undefined;
  const optionId = (index: number) => listId + "-" + index;

  useEffect(() => {
    if (expanded && activeIndex >= 0) {
      document.getElementById(listId + "-" + activeIndex)?.scrollIntoView({ block: "nearest" });
    }
  }, [activeIndex, expanded, listId]);

  const closeOptions = () => {
    setOpen(false);
    setActivePersonId(null);
  };

  const choosePerson = (person: PersonOption) => {
    setQuery(person.name);
    closeOptions();
  };

  const alreadyAssigned = Boolean(currentPersonName && search === personKey(currentPersonName));
  const canAssign = !alreadyAssigned && !disabled && peopleStatus === "ready" && !!name;
  const submit = () => {
    if (!canAssign) return;
    closeOptions();
    onAssign(exact?.name ?? name);
  };
  const count = faceCount === 1 ? "1 Gesicht" : `${faceCount} Gesichter`;
  const actionLabel = explicitScope && name && !alreadyAssigned
    ? exact ? `${count} „${exact.name}“ zuordnen` : `„${name}“ anlegen und ${count} zuordnen`
    : !name || alreadyAssigned
    ? "Person zuweisen"
    : exact
      ? "„" + exact.name + "“ zuweisen"
      : "„" + name + "“ anlegen und zuweisen";

  return (
    <div className="cluster-assignment-workflow">
      <div className="cluster-assign-bar">
        <div
          className="cluster-person-combobox"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) closeOptions();
          }}
        >
          <input
            type="text"
            role="combobox"
            className="cluster-person-combobox__input"
            aria-label="Person suchen oder neuen Namen eingeben"
            aria-autocomplete="list"
            aria-expanded={expanded}
            aria-controls={expanded ? listId : undefined}
            aria-activedescendant={highlighted ? optionId(activeIndex) : undefined}
            aria-describedby={listId + "-scope"}
            placeholder="Person suchen oder neu anlegen…"
            value={query}
            disabled={disabled}
            onChange={(event) => {
              setQuery(event.target.value);
              setActivePersonId(null);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                if (options.length === 0) return;
                event.preventDefault();
                const direction = event.key === "ArrowDown" ? 1 : -1;
                setOpen(true);
                const nextIndex = activeIndex < 0
                  ? direction === 1 ? 0 : options.length - 1
                  : (activeIndex + direction + options.length) % options.length;
                setActivePersonId(options[nextIndex].id);
                return;
              }
              if (event.key === "Escape" && open) {
                event.preventDefault();
                closeOptions();
                return;
              }
              if (event.key === "Enter") {
                event.preventDefault();
                if (highlighted) choosePerson(highlighted);
                else if (exact) submit();
              }
            }}
          />
          {expanded && (
            <ul id={listId} className="cluster-person-combobox__menu" role="listbox" aria-label="Passende Personen">
              {options.map((person, index) => (
                <li
                  id={optionId(index)}
                  key={person.id}
                  role="option"
                  aria-selected={activeIndex === index}
                  className="cluster-person-combobox__option"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => choosePerson(person)}
                >
                  {person.name}
                </li>
              ))}
            </ul>
          )}
        </div>
        <button className="cluster-action-button cluster-action-button--primary" type="button" disabled={!canAssign} onClick={submit}>
          {actionLabel}
        </button>
      </div>
      <p id={listId + "-scope"} className="cluster-assignment-workflow__scope">
        {wholeGroup ? "Ganze Gruppe" : "Auswahl"} · {faceCount === 1 ? "1 Gesicht" : faceCount + " Gesichter"}
      </p>
      {alreadyAssigned && <p className="cluster-assignment-workflow__scope" role="status">
        Die Gesichter sind dieser Person bereits zugeordnet.
      </p>}
      {peopleStatus === "loading" && <p className="cluster-assignment-workflow__scope" role="status">Personen werden geladen …</p>}
      {peopleStatus === "error" && (
        <div className="review-workflow-feedback" role="alert">
          <span>Die Personenliste konnte nicht geladen werden.</span>
          <button className="secondary-button" type="button" disabled={busy} onClick={onRetry}>Erneut laden</button>
        </div>
      )}
    </div>
  );
};

export default PersonAssignment;
