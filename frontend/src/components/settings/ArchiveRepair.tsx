import { useRef, useState } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { faceCropUrl } from "../../utils/api";
import { countWithLabel } from "../../utils/germanFormatting";
import {
  addArchiveRepairSnapshot, applyArchiveRepair, inspectArchiveRepair, undoArchiveRepair,
  type ArchiveRepairCandidate, type ArchiveRepairPreview,
} from "../../utils/libraryMaintenance";
import "./LibraryMaintenance.css";

const PAGE_SIZE = 24;
const shortDate = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium" });

function Confirmation({ undo, count, onClose, onConfirm }: {
  undo: boolean;
  count: number;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  useModalFocus(dialog, onClose);
  const amount = countWithLabel(count, "Gesicht", "Gesichter");
  return <div className="modal-backdrop">
    <section ref={dialog} className="settings-card library-maintenance-dialog"
      role="dialog" aria-modal="true" aria-labelledby="archive-repair-confirm-heading">
      <h2 id="archive-repair-confirm-heading">{undo ? "Letzte Korrektur rückgängig machen?" : "Gesichter wieder aussortieren?"}</h2>
      <p>{undo
        ? `Für ${amount} wird die Zuordnung von vor der Korrektur wiederhergestellt.`
        : `Für ${amount} wird der frühere Status aus der Sicherung übernommen. Bestätige die Gesichtsauswahl nur, wenn sie stimmt.`}</p>
      <div className="settings-actions">
        <button type="button" className="secondary-button" data-initial-focus onClick={onClose}>Abbrechen</button>
        <button type="button" className="primary-button" onClick={onConfirm}>
          {undo ? "Korrektur rückgängig machen" : "Auswahl wieder aussortieren"}
        </button>
      </div>
    </section>
  </div>;
}

function RepairCandidate({ candidate, busy, selected, onChange }: {
  candidate: ArchiveRepairCandidate;
  busy: boolean;
  selected: boolean;
  onChange: (checked: boolean) => void;
}) {
  const [photo, setPhoto] = useState<"loading" | "ready" | "missing">("loading");
  return <label className="archive-repair-face">
    <input type="checkbox" disabled={busy || photo !== "ready"} checked={selected}
      onChange={(event) => onChange(event.currentTarget.checked)} />
    {photo !== "missing" && <img loading="lazy" src={faceCropUrl(candidate.face_id)} alt=""
      onLoad={() => setPhoto("ready")} onError={() => { setPhoto("missing"); onChange(false); }} />}
    <span>
      <strong>{candidate.person_name}</strong>
      {candidate.photo_filename && <small>{candidate.photo_filename}</small>}
      <small>Früher {candidate.restore_status === "unknown_person" ? "„Unbekannt“" : "„Kein Gesicht“"}</small>
      <small>{candidate.source_name.startsWith("registered-") ? "Vergleichsdatei hinzugefügt am" : "Sicherungsdatei vom"}{" "}
        {shortDate.format(new Date(candidate.source_date))}</small>
      {photo === "missing" && <small>Vorschau fehlt. Verbinde den Fotoordner und starte die Prüfung erneut.</small>}
    </span>
  </label>;
}

export default function ArchiveRepair() {
  const [preview, setPreview] = useState<ArchiveRepairPreview>();
  const [selected, setSelected] = useState<string[]>([]);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [confirmation, setConfirmation] = useState<"apply" | "undo">();
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ text: string; error?: boolean }>();
  const inFlight = useRef(false);
  const snapshotInput = useRef<HTMLInputElement>(null);
  const [scanNumber, setScanNumber] = useState(0);

  const run = async (action: "inspect" | "apply" | "undo", file?: File) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setFeedback(undefined);
    setConfirmation(undefined);
    let success = "";
    try {
      if (file) {
        await addArchiveRepairSnapshot(file);
        success = "Die Sicherung wurde zum Vergleich hinzugefügt.";
      }
      if (action === "apply") {
        const batch = await applyArchiveRepair(selected);
        success = `${countWithLabel(batch.count, "Gesicht wurde", "Gesichter wurden")} wieder aussortiert.`;
      }
      if (action === "undo" && preview?.latest_batch) {
        await undoArchiveRepair(preview.latest_batch.batch_id);
        success = "Die letzte Korrektur wurde rückgängig gemacht.";
      }
      setSelected([]);
      setPreview(undefined);
      setPreview(await inspectArchiveRepair());
      setScanNumber((number) => number + 1);
      setVisibleCount(PAGE_SIZE);
      if (success) setFeedback({ text: success });
    } catch (error) {
      const reason = error instanceof Error ? error.message : "Die Prüfung ist fehlgeschlagen.";
      setFeedback({ error: true, text: success ? `${success} ${reason}` : reason });
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  return <section className="settings-card">
    <h2 className="settings-card__title">Aussortierte Gesichter prüfen</h2>
    <p className="settings-card__copy">
      Face Manager korrigiert eindeutige Widersprüche beim Öffnen der Bibliothek.
      Eine frühere Sicherung kann zeigen, welche weiteren Gesichter damals aussortiert waren.
    </p>
    <div className="settings-actions">
      <button type="button" className="secondary-button" disabled={busy} onClick={() => void run("inspect")}>
        {busy ? "Zuordnungen werden geprüft …" : "Sicherungen nach früheren Zuordnungen durchsuchen"}
      </button>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => snapshotInput.current?.click()}>
        Eigene Sicherung vergleichen
      </button>
      <input ref={snapshotInput} type="file" hidden accept=".sqlite,.sqlite3,.db,application/octet-stream"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (file) void run("inspect", file);
        }} />
    </div>
    {feedback && <p className={feedback.error ? "settings-feedback settings-feedback--error" : "settings-feedback"}
      role={feedback.error ? "alert" : "status"}>{feedback.text}</p>}
    {preview && <div className="archive-repair-results">
      <p role="status">{countWithLabel(preview.checked_backups, "passende Sicherung geprüft", "passende Sicherungen geprüft")}.
        {preview.automatic_repaired > 0 && ` ${countWithLabel(preview.automatic_repaired, "Widerspruch wurde", "Widersprüche wurden")} automatisch korrigiert.`}
      </p>
      {preview.skipped_conflicts > 0 && <p>
        {countWithLabel(preview.skipped_conflicts, "Gesicht hatte", "Gesichter hatten")} in den Sicherungen widersprüchliche Zuordnungen. Diese Fälle bleiben unverändert.
      </p>}
      {preview.candidates.length === 0 ? <p>Es wurden keine eindeutigen Hinweise aus älteren Sicherungen gefunden.</p>
        : <>
          <p>Prüfe, welche Gesichter wieder aussortiert werden sollen. Die Sicherung zeigt ihren früheren Status.</p>
          <div className="archive-repair-faces">
            {preview.candidates.slice(0, visibleCount).map((candidate) => <RepairCandidate
              key={`${scanNumber}:${candidate.token}`} candidate={candidate} busy={busy}
              selected={selected.includes(candidate.token)} onChange={(checked) => setSelected((values) => checked
                ? Array.from(new Set([...values, candidate.token]))
                : values.filter((token) => token !== candidate.token))} />)}
          </div>
          <div className="settings-actions">
            {visibleCount < preview.candidates.length && <button type="button" className="secondary-button" disabled={busy}
              onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}>Weitere Gesichter anzeigen</button>}
            <button type="button" className="secondary-button" disabled={busy || selected.length === 0}
              onClick={() => setSelected([])}>Auswahl aufheben</button>
            <button type="button" className="primary-button" disabled={busy || selected.length === 0}
              onClick={() => setConfirmation("apply")}>
              {countWithLabel(selected.length, "Gesicht", "Gesichter")} wieder aussortieren
            </button>
          </div>
        </>}
      {preview.truncated && <p>Die Prüfung zeigt einen Teil der verfügbaren Daten.</p>}
      {preview.latest_batch && <button type="button" className="secondary-button" disabled={busy}
        onClick={() => setConfirmation("undo")}>Letzte Korrektur rückgängig machen</button>}
    </div>}
    {confirmation && <Confirmation undo={confirmation === "undo"}
      count={confirmation === "undo" ? preview?.latest_batch?.count ?? 0 : selected.length}
      onClose={() => setConfirmation(undefined)} onConfirm={() => void run(confirmation)} />}
  </section>;
}
