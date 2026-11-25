import React, { useEffect, useRef, useState } from "react";
import type { SharedLibraryController } from "../../hooks/useSharedLibrary";
import { fetchSharedConflictEvidence, resolveSharedLibrary, type SharedLibraryConflict, type SharedLibraryDecision } from "../../utils/api";
import { navigationHash } from "../../utils/navigation";
import "../settings/shared-library.css";

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

function DecisionPreview({ label, decision, image, loading, onDecoded }: {
  label: string;
  decision: SharedLibraryDecision;
  image?: string;
  loading: boolean;
  onDecoded: (valid: boolean) => void;
}) {
  const [imageError, setImageError] = useState(false);
  const canShow = image?.startsWith("data:image/jpeg;base64,") && !imageError;
  return (
    <div className="shared-library__decision">
      <h4>{label}</h4>
      <p>{decision.device}</p>
      {canShow && <img src={image} alt={`Gesichtsausschnitt · ${label}`}
        onLoad={(event) => {
          const element = event.currentTarget;
          void element.decode().then(() => {
            if (element.isConnected) onDecoded(element.naturalWidth > 0 && element.getClientRects().length > 0);
          }, () => { if (element.isConnected) onDecoded(false); });
        }}
        onError={() => { setImageError(true); onDecoded(false); }} />}
      {!canShow && <p>{loading ? "Ausschnitt wird geladen …" : "Kein geprüfter Ausschnitt verfügbar"}</p>}
      <strong>{decisionLabel(decision)}</strong>
    </div>
  );
}

export default function SharedLibraryReview({ controller }: { controller: SharedLibraryController }) {
  const { resource, readError, working, configured, refresh, mutate } = controller;
  const [selectedCase, setSelectedCase] = useState<SharedLibraryConflict | null>(null);
  const [needsReload, setNeedsReload] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<{
    key: string;
    images?: Awaited<ReturnType<typeof fetchSharedConflictEvidence>>;
    local?: boolean;
    shared?: boolean;
  } | null>(null);
  const [previewRetry, setPreviewRetry] = useState(0);
  const [showPhoto, setShowPhoto] = useState(false);
  const caseHeading = useRef<HTMLHeadingElement>(null);
  const conflictsHeading = useRef<HTMLHeadingElement>(null);
  const caseOpener = useRef<HTMLButtonElement | null>(null);
  const conflicts = resource?.conflicts ?? [];
  const count = resource?.conflict_count ?? conflicts.length;
  const current = selectedCase && conflicts.find(({ id }) => id === selectedCase.id);
  const stale = Boolean(selectedCase && (needsReload || current?.revision !== selectedCase.revision));
  const choices = (["local", "shared"] as const).filter((choice) => {
    if (!current?.can_resolve || !selectedCase?.can_resolve || resource?.preview || resource?.resolution_pending) return false;
    const key = choice === "local" ? "can_keep_local" : "can_use_shared";
    return current[key] !== false && selectedCase[key] !== false;
  });
  const editable = resource?.status === "online" && !resource.paused && !readError && !stale;
  const previewKey = selectedCase && `${selectedCase.id}/${selectedCase.revision}/${previewRetry}`;
  const currentPreview = preview?.key === previewKey ? preview : null;
  const images = currentPreview?.images;
  const previewLoading = Boolean(selectedCase?.evidence_available && !currentPreview);
  const cropsDecoded = currentPreview?.local === true && currentPreview.shared === true;
  const previewFailed = Boolean(currentPreview && (!images || currentPreview.local === false || currentPreview.shared === false ||
    (choices.length > 0 && (!images.local?.startsWith("data:image/jpeg;base64,") || !images.shared?.startsWith("data:image/jpeg;base64,")))));

  const markCrop = (side: "local" | "shared", valid: boolean) => {
    setPreview((previous) => previous && previous.key === previewKey ? { ...previous, [side]: valid } : previous);
  };

  useEffect(() => {
    conflictsHeading.current?.focus();
  }, []);

  useEffect(() => {
    setShowPhoto(false);
    if (!selectedCase?.evidence_available || !previewKey || stale) return;
    const abort = new AbortController();
    void fetchSharedConflictEvidence(selectedCase, abort.signal).then(
      (images) => { if (!abort.signal.aborted) setPreview({ key: previewKey, images }); },
      () => { if (!abort.signal.aborted) setPreview({ key: previewKey }); },
    );
    return () => abort.abort();
  }, [selectedCase, previewKey, stale]);

  const closeCase = () => {
    setSelectedCase(null);
    setPreview(null);
    setNeedsReload(false);
    setActionError(null);
    window.requestAnimationFrame(() => {
      if (caseOpener.current?.isConnected) caseOpener.current.focus();
      else conflictsHeading.current?.focus();
    });
  };

  const resolve = async (choice: "local" | "shared") => {
    if (!selectedCase || !choices.includes(choice) || !editable || !cropsDecoded) return;
    setNotice(null);
    setActionError(null);
    try {
      const next = await mutate(() => resolveSharedLibrary(selectedCase, choice));
      if (next) {
        closeCase();
        if (!next.resolution_pending) setNotice("Die Entscheidung wurde übernommen.");
      }
    } catch (error) {
      if (error instanceof Error && "status" in error && error.status === 409) setNeedsReload(true);
      else setActionError(error instanceof Error ? error.message : "Die Entscheidung konnte nicht gespeichert werden.");
    }
  };

  return (
    <section className="settings-card shared-library" aria-labelledby="shared-library-conflicts">
      <h2 id="shared-library-conflicts" tabIndex={-1} ref={conflictsHeading}>Abgleich prüfen</h2>
      {readError && <div className="settings-feedback settings-feedback--error">
        <p role="alert">{readError}</p>
        <button className="secondary-button" type="button" disabled={working} onClick={() => void refresh(true)}>Erneut prüfen</button>
      </div>}
      {!resource && !readError && <p role="status">Gemeinsamer Stand wird geladen …</p>}
      {resource?.available === false && <p>Der gemeinsame Abgleich ist auf diesem Gerät nicht verfügbar.</p>}
      {resource && resource.available !== false && !configured && <p>Hier erscheinen offene Fälle eines eingerichteten gemeinsamen Ordners.</p>}
      {configured && <>
        <p role="status">{resource.status === "checking" ? "Freigabe wird geprüft …" : count === 1 ? "1 Fall zu prüfen" : `${count} Fälle zu prüfen`}</p>
        {resource.error && <p role="alert">{resource.error}</p>}
        {resource.resolution_pending && <p role="status">Die Entscheidung ist zur Prüfung vorgemerkt.</p>}
        {resource.concurrent_choices && <p role="status">Für ein Gesicht wurden auf zwei Geräten unterschiedliche Entscheidungen getroffen. Prüfe die Zuordnung in der lokalen Bibliothek.</p>}
        {resource.choice_error && <p className="settings-feedback" role="alert">{resource.choice_error}</p>}
        {resource.status === "online" && !conflicts.length && count === 0 && <p>Gerade sind keine Fälle offen.</p>}
        {!conflicts.length && count > 0 && <p>Die Fälle sind noch nicht verfügbar. Lade den Stand erneut.</p>}
        {!conflicts.length && count > 0 && <button className="secondary-button" type="button" onClick={() => void refresh(true)}>Stand neu laden</button>}
        <div className="shared-library__conflicts">
        {selectedCase && <section aria-labelledby="shared-library-case" className="shared-library__case">
          <h3 id="shared-library-case" tabIndex={-1} ref={caseHeading}>{selectedCase.relative_path}</h3>
          {stale ? <div className="settings-feedback settings-feedback--error">
            <p role="alert">Der Fall hat sich geändert. Lade den aktuellen Stand.</p>
            <button type="button" className="secondary-button" onClick={async () => {
              const next = await refresh(true);
              if (!next) return;
              const updated = next.conflicts?.find(({ id }) => id === selectedCase.id);
              if (updated) {
                setPreview(null);
                setPreviewRetry((previous) => previous + 1);
                setSelectedCase(updated);
                setNeedsReload(false);
                window.requestAnimationFrame(() => caseHeading.current?.focus());
              } else closeCase();
            }}>Aktuellen Fall laden</button>
          </div> : <>
            <p>{CONFLICT_REASON[selectedCase.reason] ?? "Dieser Fall braucht eine Prüfung."}</p>
            <div className="shared-library__decisions">
              <DecisionPreview key={`local-${previewKey}`} label="Auf diesem Gerät" decision={selectedCase.local} image={images?.local}
                loading={previewLoading} onDecoded={(valid) => markCrop("local", valid)} />
              <DecisionPreview key={`shared-${previewKey}`} label="Auf der Freigabe" decision={selectedCase.shared} image={images?.shared}
                loading={previewLoading} onDecoded={(valid) => markCrop("shared", valid)} />
            </div>
            {previewFailed && <div className="settings-feedback settings-feedback--error">
              <p role="alert">Die geprüfte Vorschau konnte nicht geladen werden.</p>
              <button className="secondary-button" type="button" disabled={working}
                onClick={() => setPreviewRetry((previous) => previous + 1)}>Vorschau erneut laden</button>
            </div>}
            {images?.photo?.startsWith("data:image/jpeg;base64,") && <>
              <button className="secondary-button" type="button" aria-expanded={showPhoto} onClick={() => setShowPhoto(!showPhoto)}>
                {showPhoto ? "Foto ausblenden" : "Geprüftes Foto ansehen"}
              </button>
              {showPhoto && <img className="shared-library__full-preview" src={images.photo} alt={`Geprüftes Foto · ${selectedCase.relative_path}`} />}
            </>}
            {resource?.preview && <p>Dieser Fall ist in der Vorschau zur Prüfung vorgemerkt.</p>}
            {!resource?.preview && !resource?.resolution_pending && choices.length === 0 && <p>Für eine sichere Entscheidung fehlen eindeutige Treffer. Der Fall bleibt offen.</p>}
            {choices.length > 0 && !editable && <p>Zum Übernehmen einer Entscheidung braucht dieses Gerät eine verbundene, schreibbare Freigabe.</p>}
            {choices.length > 0 && !cropsDecoded && <p>Zum Übernehmen müssen beide geprüften Ausschnitte sichtbar sein.</p>}
            {choices.length > 0 && <div className="settings-actions">
              {choices.map((choice) => <button key={choice} type="button" className="secondary-button"
                disabled={working || !editable || !cropsDecoded} onClick={() => {
                  void resolve(choice);
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
              setPreview(null);
              setPreviewRetry((previous) => previous + 1);
              setSelectedCase(item);
              setNeedsReload(false);
              setActionError(null);
              window.requestAnimationFrame(() => caseHeading.current?.focus());
            }}>Fall prüfen</button>
          </li>)}
        </ul>
        </div>
      </>}
      {working && <p role="status">Änderung wird gespeichert …</p>}
      {notice && !resource?.resolution_pending && <p className="settings-feedback" role="status">{notice}</p>}
      {actionError && <p className="settings-feedback settings-feedback--error" role="alert">{actionError}</p>}
      {resource?.available !== false && <a href={navigationHash({ page: "settings", settingsSection: "daten" })}>Zum gemeinsamen Ordner in den Einstellungen</a>}
    </section>
  );
}
