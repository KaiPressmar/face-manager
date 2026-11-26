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
    note: <>Unter <strong>Aktivitäten</strong> siehst du, wie weit die Verarbeitung ist.</>,
    destination: "import",
    actionLabel: "Bilder hinzufügen",
  },
  {
    topic: "people",
    title: "Gesichtern Namen geben",
    actions: [
      <>Öffne <strong>Personen</strong>. Auf der Seite <strong>Gesichter prüfen</strong> findest du erkannte Gesichter.</>,
      <>Wähle <strong>Ohne Namen</strong> und öffne eine Gruppe. Wenn du mehrere Personen in der Gruppe siehst, markiere zuerst die passenden Gesichter.</>,
      <>Tippe einen Namen in <strong>Person suchen oder neu anlegen…</strong>. Wähle den passenden Namen und bestätige mit <strong>zuweisen</strong> oder <strong>anlegen und zuweisen</strong>.</>,
    ],
    note: <>Fehlt ein Gesicht? Öffne das Foto und wähle <strong>Gesicht markieren</strong>. Ziehe einen Rahmen auf und wähle die Person. Unter <strong>Personen korrigieren</strong> kannst du eine Zuordnung später ändern.</>,
    destination: "review",
    actionLabel: "Personen öffnen",
  },
  {
    topic: "map",
    title: "Fotos auf der Karte finden",
    actions: [
      <>Öffne <strong>Karte</strong> und wähle <strong>Standorte</strong>. Wähle einen Punkt auf der Karte, um die Fotos dort zu sehen.</>,
      <>Wähle unter <strong>Zeitraum</strong> einen Vorschlag oder gib bei <strong>Von</strong> und <strong>Bis</strong> eigene Daten ein.</>,
      <>Wähle <strong>Bereich wählen</strong> und ziehe ein Rechteck auf der Karte. Mit <strong>Sichtbaren Bereich wählen</strong> übernimmst du stattdessen den gesamten sichtbaren Ausschnitt.</>,
    ],
    note: <>Auf der Karte erscheinen Fotos mit gespeicherten Standortdaten. Mit <strong>Auswahl löschen</strong> entfernst du den gewählten Bereich.</>,
    destination: "map",
    actionLabel: "Karte öffnen",
  },
  {
    topic: "journeys",
    title: "Eine Reise als Album sammeln",
    actions: [
      <>Öffne <strong>Karte</strong>, wähle <strong>Verlauf</strong> und stelle den <strong>Zeitraum</strong> deiner Reise ein.</>,
      <>Öffne <strong>Reisen entdecken</strong> und wähle einen Vorschlag. Schau dir die Stopps an. Unter <strong>Reisetag</strong> kannst du einen einzelnen Tag auswählen.</>,
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
      <>Öffne in <strong>Karte</strong> den Bereich oder Reiseverlauf, den du abbilden möchtest. Wähle <strong>Bild exportieren</strong>.</>,
      <>Gib einen <strong>Titel</strong> ein und wähle <strong>Format</strong> und <strong>Farbwelt</strong>. Schau dir die Vorschau an.</>,
      <>Wähle <strong>PNG speichern</strong>. Bei mehreren Seiten kannst du mit <strong>Diese Seite als PNG speichern</strong> jede Seite einzeln speichern.</>,
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
      <>Markiere die gewünschten Dateien. Mit <strong>Dateinamen aktualisieren</strong> öffnest du <strong>Dateinamen prüfen</strong>, wo du die Auswahl noch einmal siehst.</>,
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
      <>Unter <strong>Zuletzt beendet</strong> findest du Meldungen über abgeschlossene oder unterbrochene Aufgaben.</>,
      <>Öffne oben die Anzeige für die Gesichtserkennung. Dort steht zum Beispiel <strong>CPU</strong> für den Prozessor oder <strong>GPU</strong> für die Grafikkarte. Nach dem ersten Import kannst du dort <strong>Hardware prüfen</strong> wählen.</>,
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
