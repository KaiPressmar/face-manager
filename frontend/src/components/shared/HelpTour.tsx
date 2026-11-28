import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import "./HelpTour.css";

export type HelpTopic = "photos" | "people" | "map" | "journeys" | "photobook" | "files" | "settings" | "activities";
export type HelpDestination = "import" | "review" | "map" | "renaming" | "settings" | "activities";

interface HelpStep {
  topic: HelpTopic;
  title: string;
  actions: ReactNode[];
  note?: ReactNode;
  destination: HelpDestination;
  actionLabel: string;
}

const steps: HelpStep[] = [
  {
    topic: "photos",
    title: "Bilder hinzufügen",
    actions: [
      <>Wähle oben <strong>Bilder hinzufügen</strong>.</>,
      <>Wähle <strong>Ordner auswählen</strong> und dann den Ordner mit deinen Fotos. Wenn du <strong>Pfad zum Bilderordner</strong> siehst, gib den Pfad ein und wähle <strong>Zur Liste hinzufügen</strong>.</>,
      <>Prüfe die Ordnerliste. Starte mit <strong>Bilder hinzufügen</strong> oder bei mehreren Ordnern etwa mit <strong>2 Ordner importieren</strong>.</>,
    ],
    note: <>Unter <strong>Aktivitäten</strong> siehst du den Fortschritt. Mit <strong>Zurück</strong> und <strong>Vorwärts</strong> wechselst du zwischen deinen Ansichten. <strong>Zur Ausgangsansicht</strong> startet die aktuelle Seite mit ihren Standardfiltern.</>,
    destination: "import",
    actionLabel: "Bilder hinzufügen",
  },
  {
    topic: "people",
    title: "Gesichtern Namen geben",
    actions: [
      <>Öffne <strong>Personen</strong> und wähle <strong>Prüfen</strong>. Unter <strong>Vorschläge</strong> kannst du Gesichter mit bestätigten Fotos vergleichen.</>,
      <>Für eine freie Zuordnung wähle <strong>Ohne Namen</strong> und öffne eine Gruppe. Wenn du mehrere Personen in der Gruppe siehst, markiere zuerst die passenden Gesichter.</>,
      <>Tippe einen Namen in <strong>Person suchen oder neu anlegen…</strong>. Wähle den passenden Namen und bestätige mit <strong>zuweisen</strong> oder <strong>anlegen und zuweisen</strong>.</>,
    ],
    note: <>Fehlt ein Gesicht? Öffne das Foto und wähle <strong>Gesicht markieren</strong>. Öffne im Verzeichnis ein Personenprofil, um den Namen zu bearbeiten oder Zuordnungen zu korrigieren. Mit <strong>Rückgängig</strong> nimmst du unterstützte Änderungen an der lokalen Bibliothek zurück.</>,
    destination: "review",
    actionLabel: "Personen öffnen",
  },
  {
    topic: "map",
    title: "Fotos auf der Karte finden",
    actions: [
      <>Öffne <strong>Karte</strong> und wähle <strong>Standorte</strong>. Wähle einen Punkt auf der Karte, um die Fotos dort zu sehen.</>,
      <>Wähle unter <strong>Zeitraum</strong> ein Aufnahmejahr oder gib eigene Daten ein. Unter <strong>Orte aus meinen Fotos</strong> kannst du direkt ein Ziel und ein Aufnahmejahr wählen.</>,
      <>Wähle <strong>Bereich wählen</strong> und ziehe ein Rechteck auf der Karte. Mit <strong>Sichtbaren Bereich wählen</strong> übernimmst du stattdessen den gesamten sichtbaren Ausschnitt.</>,
    ],
    note: <>Mit <strong>Fotoorte auf Karte zeigen</strong> wechselst du in kleinen Fenstern direkt zur Karte. Zoome auf dem Touchscreen mit zwei Fingern. <strong>Kartendetails online</strong> zeigt Straßen und feinere Kartenbilder. Kartenübersicht und Fotobuch-Export funktionieren offline.</>,
    destination: "map",
    actionLabel: "Karte öffnen",
  },
  {
    topic: "journeys",
    title: "Eine Reise als Album sammeln",
    actions: [
      <>Wähle in <strong>Karte</strong> deine Fotos und öffne <strong>Verlauf dieser Auswahl</strong>. Im Kartenmodus <strong>Verlauf</strong> kannst du den Zeitraum auch direkt eingrenzen.</>,
      <>Öffne <strong>Reisen entdecken</strong> und wähle einen Vorschlag. Unter <strong>Reisetag</strong> kannst du einen Tag auswählen. Eine Markierung mit × auf der Karte öffnet mehrere Stopps zur Auswahl.</>,
      <>Wenn die Auswahl passt, wähle <strong>Als Reisealbum speichern</strong> und gib dem Album einen Namen.</>,
    ],
    note: <>Über <strong>Album</strong> in <strong>Bilder</strong> oder <strong>Karte</strong> findest du das Reisealbum wieder.</>,
    destination: "map",
    actionLabel: "Karte öffnen",
  },
  {
    topic: "photobook",
    title: "Ein Bild fürs Fotobuch speichern",
    actions: [
      <>Öffne in <strong>Karte</strong> den gewünschten Bereich oder Reiseverlauf. Wähle oben <strong>Fotobuchbild gestalten</strong>.</>,
      <>Wähle das <strong>Format</strong> und das <strong>Layout</strong> deiner Seite. Mit <strong>Ausschnitt anpassen</strong> kannst du die Karte in der Vorschau vergrößern und verschieben. Bei einer Reise kannst du die Stopps beschriften.</>,
      <>Speichere eine Seite als PNG. Bei mehreren Seiten kannst du <strong>Alle Seiten als ZIP speichern</strong> wählen. Entpacke die Datei, um die einzelnen Bilder in dein Fotobuch einzufügen.</>,
    ],
    note: <>Ein PNG ist eine Bilddatei. Du kannst sie in dein Fotobuch einfügen.</>,
    destination: "map",
    actionLabel: "Karte öffnen",
  },
  {
    topic: "files",
    title: "Dateinamen vorab prüfen",
    actions: [
      <>Öffne <strong>Dateinamen</strong>. Vergleiche <strong>Aktueller Name</strong> und <strong>Vorgeschlagener Name</strong>.</>,
      <>Markiere die gewünschten Dateien. Mit <strong>Umbenennung prüfen</strong> siehst du die ausgewählten Dateien und ihre endgültigen Namen.</>,
      <>Wenn alles passt, wähle <strong>Dateien jetzt umbenennen</strong>. Mit <strong>Abbrechen</strong> schließt du die Prüfung.</>,
    ],
    destination: "renaming",
    actionLabel: "Dateinamen öffnen",
  },
  {
    topic: "settings",
    title: "Zuordnungen und Einstellungen sichern",
    actions: [
      <>Öffne <strong>Einstellungen</strong> und wähle <strong>Daten und Wartung</strong>.</>,
      <>Wähle <strong>Sicherung erstellen</strong> und bewahre die Datei an einem Ort auf, den du wiederfindest.</>,
      <>Wenn du später <strong>Sicherung wiederherstellen</strong> wählst, prüfe in der Vorschau, ob es die richtige Bibliothek ist. Bestätige erst dann.</>,
    ],
    note: <>Die Sicherung enthält die Namen und Zuordnungen, die du in Face Manager gespeichert hast. Sichere deine Fotodateien separat.</>,
    destination: "settings",
    actionLabel: "Einstellungen öffnen",
  },
  {
    topic: "activities",
    title: "Fortschritt und Hilfe finden",
    actions: [
      <>Öffne oben <strong>Aktivitäten</strong>. Unter <strong>Aktuell</strong> siehst du laufende und wartende Aufgaben.</>,
      <>Unter <strong>Zuletzt beendet</strong> siehst du, wie ein Import ausgegangen ist. Wähle bei Hinweisen <strong>Betroffene Pfade ansehen</strong>. Wenn alle Pfade erfasst wurden, kannst du sie mit <strong>Ausgelassene Pfade erneut prüfen</strong> einreihen.</>,
      <>Öffne oben <strong>Hardware</strong>. Dort steht zum Beispiel <strong>CPU</strong> für den Prozessor oder <strong>GPU</strong> für die Grafikkarte. Dort kannst du auch <strong>Hardware prüfen</strong> wählen.</>,
    ],
    note: <>Unter <strong>Hilfe</strong> findest du diese Schritte jederzeit.</>,
    destination: "activities",
    actionLabel: "Aktivitäten öffnen",
  },
];

export interface HelpTourProps {
  onClose: () => void;
  onOpenPage: (destination: HelpDestination) => void;
  initialTopic?: HelpTopic;
}

export default function HelpTour({ onClose, onOpenPage, initialTopic }: HelpTourProps) {
  const [index, setIndex] = useState(() => Math.max(0, steps.findIndex(({ topic }) => topic === initialTopic)));
  const dialog = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const focusHeading = useRef(true);
  const id = useId();
  const step = steps[index];
  const lastStep = index === steps.length - 1;
  useModalFocus(dialog, onClose);

  useLayoutEffect(() => {
    if (dialog.current) dialog.current.scrollTop = 0;
    if (focusHeading.current) heading.current?.focus({ preventScroll: true });
  }, [index]);

  const goToStep = (nextIndex: number, moveFocus = true) => {
    focusHeading.current = moveFocus;
    setIndex(nextIndex);
  };

  return <div className="help-tour-backdrop">
    <section ref={dialog} className="help-tour" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
      <header className="help-tour__header">
        <h2 id={`${id}-title`}>Erste Schritte</h2>
        <button type="button" onClick={onClose}>Schließen</button>
      </header>
      <div className="help-tour__content">
        <p className="help-tour__progress" id={`${id}-progress`}>Schritt {index + 1} von {steps.length}</p>
        <h3 ref={heading} tabIndex={-1} data-initial-focus aria-describedby={`${id}-progress`}>{step.title}</h3>
        <label className="help-tour__topic">
          <span>Thema</span>
          <select aria-label="Thema" value={index} onChange={(event) => goToStep(event.target.selectedIndex, false)}>
            {steps.map(({ topic, title }, position) => <option key={topic} value={position}>{title}</option>)}
          </select>
        </label>
        <ol>{step.actions.map((action, position) => <li key={position}>{action}</li>)}</ol>
        {step.note && <p className="help-tour__note">{step.note}</p>}
        <button type="button" className="help-tour__open" onClick={() => onOpenPage(step.destination)}>{step.actionLabel}</button>
      </div>
      <footer className="help-tour__footer">
        <button type="button" disabled={index === 0} onClick={() => goToStep(index - 1)}>Zurück</button>
        <button type="button" className="help-tour__next" onClick={() => {
          if (lastStep) onClose();
          else goToStep(index + 1);
        }}>{lastStep ? "Fertig" : "Weiter"}</button>
      </footer>
    </section>
  </div>;
}
