import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  faceCropUrl,
  fetchSharedLibrary,
  imageFileUrl,
  resolveSharedLibrary,
  updateSharedLibrary,
  type SharedLibraryConfig,
  type SharedLibraryConflict,
  type SharedLibraryDecision,
  type SharedLibraryState,
} from "../../utils/api";
import "./shared-library.css";

const STATUS_LABEL: Record<SharedLibraryState["status"], string> = {
  off: "Abgleich aus",
  checking: "Freigabe wird geprüft …",
  online: "Freigabe verbunden",
  offline: "Freigabe nicht erreichbar",
  read_only: "Freigabe schreibgeschützt",
  paused: "Abgleich pausiert",
  attention: "Gemeinsamen Ordner prüfen",
};

const CONFLICT_REASON: Record<string, string> = {
  different_decision: "Für dieses Gesicht gibt es unterschiedliche Entscheidungen.",
  different_person: "Dieses Gesicht wurde unterschiedlichen Personen zugeordnet.",
  unmatched: "Das Gesicht konnte nicht eindeutig zugeordnet werden.",
  ambiguous_geometry: "Mehrere Gesichtsausschnitte kommen für den Abgleich infrage.",
  missing_source: "Das benötigte Foto ist zurzeit nicht erreichbar.",
  photo_match_limit: "Für dieses Foto gibt es zu viele mögliche Treffer.",
};

function decisionLabel(decision: SharedLibraryDecision) {
  switch (decision.kind) {
    case "person": return decision.person_name || "Einer Person zugeordnet";
    case "unknown_person": return "Als unbekannte Person aussortiert";
    case "not_face": return "Als kein Gesicht aussortiert";
    case "unassigned": return "Ohne Namen";
    case "detected": return "Erkannt, noch nicht geprüft";
    case "missing": return "Kein Gesicht erkannt";
  }
}

function configOf(state: SharedLibraryState): SharedLibraryConfig {
  const { source_path, sync_path, paused, share_embeddings, write_enabled } = state;
  return { source_path, sync_path, paused, share_embeddings, write_enabled };
}

function DecisionPreview({ label, decision, faceId }: {
  label: string;
  decision: SharedLibraryDecision;
  faceId?: number | null;
}) {
  const [imageError, setImageError] = useState(false);
  const canShow = typeof faceId === "number" && Number.isSafeInteger(faceId) && faceId > 0 && !imageError;
  return (
    <div className="shared-library__decision">
      <h4>{label}</h4>
      <p>{decision.device}</p>
      {canShow && <img src={faceCropUrl(faceId)} alt={`Gesichtsausschnitt · ${label}`} onError={() => setImageError(true)} />}
      {!canShow && <p>Kein geprüfter Ausschnitt verfügbar</p>}
      <strong>{decisionLabel(decision)}</strong>
    </div>
  );
}

export default function SharedLibrarySettings() {
  const [resource, setResource] = useState<SharedLibraryState | null>(null);
  const [draft, setDraft] = useState<SharedLibraryConfig | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [selectedCase, setSelectedCase] = useState<SharedLibraryConflict | null>(null);
  const [needsReload, setNeedsReload] = useState(false);
  const readInFlight = useRef<AbortController | null>(null);
  const mutationInFlight = useRef(false);
  const panelRef = useRef<HTMLElement>(null);
  const panelHeading = useRef<HTMLHeadingElement>(null);
  const caseHeading = useRef<HTMLHeadingElement>(null);
  const conflictsHeading = useRef<HTMLHeadingElement>(null);
  const caseOpener = useRef<HTMLButtonElement | null>(null);

  const refresh = useCallback(async (force = false) => {
    if (mutationInFlight.current || (readInFlight.current && !force)) return null;
    readInFlight.current?.abort();
    const controller = new AbortController();
    readInFlight.current = controller;
    try {
      const next = await fetchSharedLibrary(controller.signal);
      if (controller.signal.aborted) return null;
      setResource(next);
      setReadError(null);
      return next;
    } catch (error) {
      if (!controller.signal.aborted) {
        setReadError(error instanceof Error ? error.message : "Der gemeinsame Stand konnte nicht geladen werden.");
      }
      return null;
    } finally {
      if (readInFlight.current === controller) readInFlight.current = null;
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      readInFlight.current?.abort();
      readInFlight.current = null;
    };
  }, [refresh]);

  const configured = resource?.available !== false && Boolean(resource?.source_path && resource.sync_path);
  useEffect(() => {
    if (!configured) return;
    const timer = window.setInterval(() => {
      if (!document.hidden && panelRef.current?.closest(".page-pane--active")) void refresh();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [configured, refresh]);

  const mutate = async (action: () => Promise<SharedLibraryState>, success: string, onStale?: () => void) => {
    if (mutationInFlight.current) return null;
    mutationInFlight.current = true;
    readInFlight.current?.abort();
    readInFlight.current = null;
    setWorking(true);
    setNotice(null);
    setActionError(null);
    try {
      const next = await action();
      setResource(next);
      setReadError(null);
      setNotice(success);
      return next;
    } catch (error) {
      if (onStale && error instanceof Error && "status" in error && error.status === 409) {
        onStale();
      } else {
        setActionError(error instanceof Error ? error.message : "Die Änderung konnte nicht gespeichert werden.");
      }
      return null;
    } finally {
      mutationInFlight.current = false;
      setWorking(false);
    }
  };

  const closeCase = () => {
    setSelectedCase(null);
    setNeedsReload(false);
    window.requestAnimationFrame(() => {
      if (caseOpener.current?.isConnected) caseOpener.current.focus();
      else (conflictsHeading.current ?? panelHeading.current)?.focus();
    });
  };

  const conflicts = resource?.conflicts ?? [];
  const currentCase = selectedCase && conflicts.find(({ id }) => id === selectedCase.id);
  const stale = Boolean(selectedCase && (needsReload || currentCase?.revision !== selectedCase.revision));
  const editable = resource && !readError && resource.status === "online" && !resource.paused;
  const inputs = draft ?? (resource ? configOf(resource) : null);
  const canSave = Boolean(inputs?.source_path?.trim() && inputs.sync_path?.trim());

  if (resource?.available === false || (!resource && !readError)) return null;

  return (
    <section className="settings-card shared-library" aria-labelledby="shared-library-title" ref={panelRef}>
      <h2 id="shared-library-title" ref={panelHeading} tabIndex={-1} className="settings-card__title">Gemeinsamer Ordner</h2>
      <p className="settings-card__copy">{resource?.preview
        ? "Diese Vorschau prüft vorhandene Zuordnungen zu einem freigegebenen Fotoordner. Die Prüfung speichert keine Änderungen an der Freigabe oder an der Fotobibliothek."
        : "Face Manager gleicht bestätigte Zuordnungen für einen freigegebenen Fotoordner ab. Die Originaldateien bleiben an ihrem Speicherort."}</p>
      {readError && <div className="settings-feedback settings-feedback--error">
        <p role="alert">{readError}</p>
        <button type="button" className="secondary-button" disabled={working} onClick={() => void refresh(true)}>Erneut prüfen</button>
      </div>}
      {resource && <>
        {configured && <div className="shared-library__status">
          <p role="status"><strong>{readError ? "Letzter bekannter Stand" : STATUS_LABEL[resource.status]}</strong></p>
          {resource.device_name && <p>Dieses Gerät: {resource.device_name}</p>}
          {Boolean(resource.active_devices?.length) && <p>Aktive Geräte: {resource.active_devices?.join(", ")}</p>}
          {resource.preview && resource.proposed_count !== undefined &&
            <p>{resource.proposed_count} eindeutig zuordenbar · {resource.conflict_count ?? conflicts.length} zu prüfen</p>}
          {!resource.preview && (resource.imported_decisions !== undefined || resource.conflict_count !== undefined || conflicts.length > 0) &&
            <p>{resource.imported_decisions ?? 0} Zuordnungen übernommen · {resource.conflict_count ?? conflicts.length} zu prüfen</p>}
          {resource.status === "offline" && <p>Die lokale Bibliothek bleibt verfügbar. Der Abgleich kann erst bei erreichbarer Freigabe fortgesetzt werden.</p>}
          {resource.status === "read_only" && <p>Dieses Gerät kann den gemeinsamen Stand lesen. Änderungen daran benötigen Schreibzugriff.</p>}
        </div>}
        {resource.error && <p className="settings-feedback settings-feedback--error" role="alert">{resource.error}</p>}
        <details>
          <summary>{configured ? "Einrichtung bearbeiten" : "Gemeinsamen Ordner einrichten"}</summary>
          {inputs && <form className="shared-library__form" onSubmit={(event) => {
            event.preventDefault();
            if (!canSave) return;
            void mutate(() => updateSharedLibrary({
              ...inputs,
              source_path: inputs.source_path?.trim() || null,
              sync_path: inputs.sync_path?.trim() || null,
              write_enabled: resource.write_available === true ? inputs.write_enabled === true : undefined,
            }), "Die Einrichtung wurde gespeichert.").then((next) => { if (next) setDraft(null); });
          }}>
            <p>Trage auf jedem Gerät den Pfad zum selben Fotoordner und zum selben Abgleichordner ein. Der Fotoordner darf schreibgeschützt sein.</p>
            <label className="settings-field">
              <span>Fotoordner</span>
              <input className="settings-number-input settings-text-input" value={inputs.source_path ?? ""} disabled={working} required
                autoComplete="off" spellCheck={false} onChange={(event) => setDraft({ ...inputs, source_path: event.target.value, write_enabled: false })} />
            </label>
            <label className="settings-field">
              <span>Abgleichordner</span>
              <input className="settings-number-input settings-text-input" value={inputs.sync_path ?? ""} disabled={working} required
                autoComplete="off" spellCheck={false} onChange={(event) => setDraft({ ...inputs, sync_path: event.target.value, write_enabled: false })} />
            </label>
            <p>Wer auf den Abgleichordner zugreifen kann, kann gemeinsame Personennamen und Fotobezüge sehen.</p>
            {resource.write_available === true && <>
              <label className="shared-library__toggle">
                <input
                  type="checkbox"
                  checked={inputs.write_enabled === true}
                  aria-describedby="shared-library-write-description"
                  disabled={working}
                  onChange={(event) => setDraft({ ...inputs, write_enabled: event.target.checked })}
                />
                Schreibenden Abgleich für diese Ordner erlauben
              </label>
              <p id="shared-library-write-description">Face Manager kann Personennamen im Abgleichordner veröffentlichen. Eindeutige Zuordnungen des eingerichteten Testgeräts können übernommen werden.</p>
            </>}
            {configured && <label className="shared-library__toggle">
              <input type="checkbox" checked={inputs.paused} disabled={working} onChange={(event) => setDraft({ ...inputs, paused: event.target.checked })} />
              Abgleich auf diesem Gerät pausieren
            </label>}
            <div className="settings-actions">
              <button type="submit" className="neon-card" disabled={working || !canSave}>Einrichtung speichern</button>
              {configured && <button type="button" className="secondary-button" disabled={working} onClick={() => {
                if (!window.confirm("Den Abgleich für dieses Gerät ausschalten?")) return;
                void mutate(() => updateSharedLibrary({
                  source_path: null,
                  sync_path: null,
                  paused: false,
                  share_embeddings: inputs.share_embeddings,
                  write_enabled: resource.write_available === true ? false : undefined,
                }),
                  "Der Abgleich wurde auf diesem Gerät ausgeschaltet.").then((next) => {
                    if (next) { setDraft(null); setSelectedCase(null); }
                  });
              }}>Abgleich ausschalten</button>}
            </div>
          </form>}
        </details>
      </>}
      {configured && (conflicts.length > 0 || selectedCase) && <section className="shared-library__conflicts" aria-labelledby="shared-library-conflicts">
        <h3 id="shared-library-conflicts" tabIndex={-1} ref={conflictsHeading}>Abgleich prüfen</h3>
        {selectedCase && <section aria-labelledby="shared-library-case" className="shared-library__case">
          <h4 id="shared-library-case" tabIndex={-1} ref={caseHeading}>{selectedCase.relative_path}</h4>
          {stale ? <div className="settings-feedback settings-feedback--error">
            <p role="alert">Der Fall hat sich geändert. Lade den aktuellen Stand.</p>
            <button type="button" className="secondary-button" onClick={async () => {
              const next = await refresh(true);
              if (!next) return;
              const updated = next.conflicts?.find(({ id }) => id === selectedCase.id);
              if (updated) {
                setSelectedCase(updated);
                setNeedsReload(false);
                window.requestAnimationFrame(() => caseHeading.current?.focus());
              } else closeCase();
            }}>Aktuellen Fall laden</button>
          </div> : <>
            <p>{CONFLICT_REASON[selectedCase.reason] ?? "Dieser Fall braucht eine Prüfung."}</p>
            <div className="shared-library__decisions">
              <DecisionPreview key={`local-${selectedCase.revision}`} label="Auf diesem Gerät" decision={selectedCase.local} faceId={selectedCase.local_face_id} />
              <DecisionPreview key={`shared-${selectedCase.revision}`} label="Auf der Freigabe" decision={selectedCase.shared} faceId={selectedCase.shared_face_id} />
            </div>
            {typeof selectedCase.photo_id === "number" && Number.isSafeInteger(selectedCase.photo_id) && selectedCase.photo_id > 0 &&
              <a href={imageFileUrl(selectedCase.photo_id)} target="_blank" rel="noreferrer">Originalfoto öffnen</a>}
            {resource?.preview && <p>Dieser Fall ist in der Vorschau zur Prüfung vorgemerkt.</p>}
            {!resource?.preview && !selectedCase.can_resolve && <p>Für eine sichere Entscheidung fehlen eindeutige Treffer. Der Fall bleibt offen.</p>}
            {selectedCase.can_resolve && !editable && <p>Zum Übernehmen einer Entscheidung braucht dieses Gerät eine verbundene, schreibbare Freigabe.</p>}
            {selectedCase.can_resolve && <div className="settings-actions">
              {(["local", "shared"] as const).map((choice) => <button key={choice} type="button" className="secondary-button"
                disabled={working || !editable} onClick={() => {
                  void mutate(() => resolveSharedLibrary(selectedCase, choice), "Die Entscheidung wurde übernommen.", () => setNeedsReload(true))
                    .then((next) => { if (next) closeCase(); });
                }}>{choice === "local" ? "Entscheidung dieses Geräts übernehmen" : "Entscheidung der Freigabe übernehmen"}</button>)}
            </div>}
          </>}
          <button type="button" className="secondary-button" disabled={working} onClick={closeCase}>Später prüfen</button>
        </section>}
        <ul className="shared-library__case-list" hidden={Boolean(selectedCase)}>
          {conflicts.map((item, index) => <li key={item.id}>
            <div>
              <strong>{item.relative_path}</strong>
              <p>{decisionLabel(item.local)} · {decisionLabel(item.shared)}</p>
            </div>
            <button type="button" className="secondary-button" aria-label={`Fall zu ${item.relative_path} prüfen · ${index + 1} von ${conflicts.length}`} onClick={(event) => {
              caseOpener.current = event.currentTarget;
              setSelectedCase(item);
              setNeedsReload(false);
              setActionError(null);
              window.requestAnimationFrame(() => caseHeading.current?.focus());
            }}>Fall prüfen</button>
          </li>)}
        </ul>
      </section>}
      {working && <p role="status">Änderung wird gespeichert …</p>}
      {notice && <p className="settings-feedback" role="status">{notice}</p>}
      {actionError && <p className="settings-feedback settings-feedback--error" role="alert">{actionError}</p>}
    </section>
  );
}
