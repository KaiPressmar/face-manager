import React, { useEffect, useRef, useState } from "react";
import type { SharedLibraryController } from "../../hooks/useSharedLibrary";
import { fetchSharedConflictEvidence, resolveSharedLibrary, sharedLibraryConflictFolder, type SharedLibraryConflict, type SharedLibraryDecision } from "../../utils/api";
import { navigationHash } from "../../utils/navigation";
import "../settings/shared-library.css";

const CONFLICT_REASON: Record<string, string> = {
  different_decision: "Für dieses Gesicht gibt es unterschiedliche Entscheidungen.",
  concurrent_choices: "Zwei Geräte haben für dieses Gesicht unterschiedliche Entscheidungen gespeichert.",
  different_person: "Dieses Gesicht wurde unterschiedlichen Personen zugeordnet.",
  unmatched: "Das Gesicht konnte nicht eindeutig zugeordnet werden.",
  ambiguous_geometry: "Mehrere Gesichtsausschnitte kommen für den Abgleich infrage.",
  missing_source: "Das benötigte Foto ist zurzeit nicht erreichbar.",
  photo_match_limit: "Für dieses Foto gibt es zu viele mögliche Treffer.",
};
const DECISION_SIDES = ["local", "shared"] as const;

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

function DecisionPreview({ label, decision, image, loading, onDecoded, children }: {
  label: string;
  decision: SharedLibraryDecision;
  image?: string;
  loading: boolean;
  onDecoded: (valid: boolean) => void;
  children?: React.ReactNode;
}) {
  const [imageError, setImageError] = useState(false);
  const canShow = image?.startsWith("data:image/jpeg;base64,") && !imageError;
  return (
    <article className="shared-library__decision">
      <header>
        <h4>{label}</h4>
        <p>{decision.device}</p>
      </header>
      <div className="shared-library__crop">
        {canShow ? (
          <img src={image} alt={`Gesichtsausschnitt · ${label}`}
            onLoad={(event) => {
              const element = event.currentTarget;
              void element.decode().then(() => {
                if (element.isConnected) onDecoded(element.naturalWidth > 0 && element.getClientRects().length > 0);
              }, () => { if (element.isConnected) onDecoded(false); });
            }}
            onError={() => { setImageError(true); onDecoded(false); }} />
        ) : <p>{loading ? "Ausschnitt wird geladen …" : "Ausschnitt nicht verfügbar"}</p>}
      </div>
      <strong>{decisionLabel(decision)}</strong>
      {children}
    </article>
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
  const caseHeader = useRef<HTMLElement>(null);
  const conflictsHeading = useRef<HTMLHeadingElement>(null);
  const caseList = useRef<HTMLUListElement>(null);
  const caseOpener = useRef<HTMLButtonElement | null>(null);
  const openedCase = useRef<string | null>(null);
  const conflicts = resource?.conflicts ?? [];
  const count = resource?.conflict_count ?? conflicts.length;
  const current = selectedCase && conflicts.find(({ id }) => id === selectedCase.id);
  const folder = selectedCase && sharedLibraryConflictFolder(resource, selectedCase);
  const concurrent = selectedCase?.reason === "concurrent_choices";
  const stale = Boolean(selectedCase && (needsReload || current?.revision !== selectedCase.revision));
  const choices = DECISION_SIDES.filter((choice) => {
    if (!current?.can_resolve || !selectedCase?.can_resolve || !folder || folder.preview || folder.resolution_pending) return false;
    const key = choice === "local" ? "can_keep_local" : "can_use_shared";
    return current[key] !== false && selectedCase[key] !== false;
  });
  const editable = folder?.status === "online" && folder.available !== false && !folder.paused && !readError && !stale;
  const previewKey = selectedCase && `${selectedCase.id}/${selectedCase.revision}/${previewRetry}`;
  const currentPreview = preview?.key === previewKey ? preview : null;
  const images = currentPreview?.images;
  const previewLoading = Boolean(selectedCase?.evidence_available && !currentPreview);
  const cropsDecoded = currentPreview?.local === true && currentPreview.shared === true;
  const previewFailed = Boolean(currentPreview && (!images || currentPreview.local === false || currentPreview.shared === false ||
    (choices.length > 0 && (!images.local?.startsWith("data:image/jpeg;base64,") || !images.shared?.startsWith("data:image/jpeg;base64,")))));
  const fullPhoto = images?.photo?.startsWith("data:image/jpeg;base64,") ? images.photo : undefined;

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

  const focusCase = () => {
    window.requestAnimationFrame(() => {
      caseHeader.current?.scrollIntoView({ block: "start" });
      caseHeading.current?.focus({ preventScroll: true });
    });
  };

  const closeCase = () => {
    openedCase.current = null;
    setSelectedCase(null);
    setPreview(null);
    setNeedsReload(false);
    setActionError(null);
    window.requestAnimationFrame(() => {
      const opener = caseOpener.current?.isConnected ? caseOpener.current : caseList.current?.querySelector("button");
      (opener ?? conflictsHeading.current)?.focus();
    });
  };

  const openCase = (item: SharedLibraryConflict, opener: HTMLButtonElement) => {
    openedCase.current = item.id;
    caseOpener.current = opener;
    setPreview(null);
    setPreviewRetry((previous) => previous + 1);
    setSelectedCase(item);
    setNeedsReload(false);
    setActionError(null);
    focusCase();
  };

  const reloadCase = async () => {
    if (!selectedCase) return;
    const next = await refresh(true);
    if (!next || openedCase.current !== selectedCase.id) return;
    const updated = next.conflicts?.find(({ id }) => id === selectedCase.id);
    if (!updated) {
      closeCase();
      return;
    }
    setPreview(null);
    setPreviewRetry((previous) => previous + 1);
    setSelectedCase(updated);
    setNeedsReload(false);
    focusCase();
  };

  const resolve = async (choice: "local" | "shared") => {
    if (!selectedCase || !choices.includes(choice) || !editable || !cropsDecoded) return;
    setNotice(null);
    setActionError(null);
    try {
      const next = await mutate(() => resolveSharedLibrary(selectedCase, choice));
      if (next) {
        closeCase();
        if (!next.resolution_pending && !sharedLibraryConflictFolder(next, selectedCase)?.resolution_pending) {
          setNotice("Die Entscheidung wurde zur Prüfung vorgemerkt.");
        }
      }
    } catch (error) {
      if (error instanceof Error && "status" in error && error.status === 409) setNeedsReload(true);
      else setActionError(error instanceof Error ? error.message : "Die Entscheidung konnte nicht gespeichert werden.");
    }
  };

  const reviewMessage = (() => {
    if (folder?.resolution_pending) return "Die Entscheidung wird bereits geprüft.";
    if (folder?.status === "read_only") return "Nur ansehen. Diese Freigabe ist schreibgeschützt.";
    if (folder?.preview) return "Nur ansehen. In der Vorschau lassen sich keine Entscheidungen übernehmen.";
    if (!editable) return "Nur ansehen. Zum Übernehmen muss die Freigabe verbunden und beschreibbar sein.";
    if (choices.length === 0) return "Der Fall bleibt offen, bis eine eindeutige Zuordnung möglich ist.";
    if (previewFailed) return null;
    if (!selectedCase?.evidence_available) return "Zum Übernehmen fehlen die geprüften Ausschnitte.";
    if (!cropsDecoded) return "Zum Übernehmen müssen beide Ausschnitte geladen sein.";
    return "Vergleiche die Ausschnitte und übernimm die passende Entscheidung.";
  })();

  return (
    <section className="settings-card shared-library shared-library--review" aria-labelledby="shared-library-conflicts">
      <h2 id="shared-library-conflicts" className={selectedCase ? "shared-library__sr-only" : undefined}
        tabIndex={-1} ref={conflictsHeading}>Abgleich prüfen</h2>
      {readError && <div className="settings-feedback settings-feedback--error">
        <p role="alert">{readError}</p>
        <button className="secondary-button" type="button" disabled={working} onClick={() => void refresh(true)}>Erneut prüfen</button>
      </div>}
      {!resource && !readError && <p role="status">Gemeinsamer Stand wird geladen …</p>}
      {resource?.available === false && <p>Der gemeinsame Abgleich ist auf diesem Gerät nicht verfügbar.</p>}
      {resource && resource.available !== false && !configured && <p>Hier erscheinen offene Fälle eines eingerichteten gemeinsamen Ordners.</p>}
      {configured && <>
        {!selectedCase && <p role="status">{resource.status === "checking" ? "Freigabe wird geprüft …" : count === 1 ? "1 Fall zu prüfen" : `${count} Fälle zu prüfen`}</p>}
        {resource.error && <p role="alert">{resource.error}</p>}
        {!selectedCase && resource.resolution_pending && <p role="status">Die Entscheidung ist zur Prüfung vorgemerkt.</p>}
        {resource.concurrent_choices && !conflicts.some(({ reason }) => reason === "concurrent_choices") && <p role="status">Für ein Gesicht liegen unterschiedliche Entscheidungen vor. Sobald der Fall verfügbar ist, kannst du ihn hier prüfen.</p>}
        {resource.choice_error && <p className="settings-feedback" role="alert">{resource.choice_error}</p>}
        {resource.status === "online" && !conflicts.length && count === 0 && <p>Gerade sind keine Fälle offen.</p>}
        {!conflicts.length && count > 0 && <p>Die Fälle sind noch nicht verfügbar. Lade den Stand erneut.</p>}
        {!conflicts.length && count > 0 && <button className="secondary-button" type="button" onClick={() => void refresh(true)}>Stand neu laden</button>}
        <div className="shared-library__conflicts">
        {selectedCase && <section aria-labelledby="shared-library-case" className="shared-library__case">
          <header className="shared-library__case-header" ref={caseHeader}>
            <button type="button" className="secondary-button" disabled={working} onClick={closeCase}>
              <span aria-hidden="true">← </span>Zur Fallliste
            </button>
            <div>
              <h3 id="shared-library-case" tabIndex={-1} ref={caseHeading}>{selectedCase.relative_path}</h3>
              {selectedCase.source_path && <p>Gemeinsamer Ordner: {selectedCase.source_path}</p>}
            </div>
          </header>
          {stale ? <div className="settings-feedback settings-feedback--error">
            <p role="alert">Der Fall hat sich geändert.</p>
            <button type="button" className="secondary-button" disabled={working}
              onClick={() => void reloadCase()}>Aktuellen Fall laden</button>
          </div> : <>
            <p>{CONFLICT_REASON[selectedCase.reason] ?? "Dieser Fall braucht eine Prüfung."}</p>
            {reviewMessage && <p className="shared-library__review-status" role="status">{reviewMessage}</p>}
            <div className="shared-library__decisions">
              {DECISION_SIDES.map((side) => {
                const decision = selectedCase[side];
                const label = concurrent
                  ? (side === "local" ? "Erste Entscheidung" : "Zweite Entscheidung")
                  : (side === "local" ? "Auf diesem Gerät" : "Auf der Freigabe");
                return (
                  <DecisionPreview key={`${side}-${previewKey}`} label={label} decision={decision} image={images?.[side]}
                    loading={previewLoading} onDecoded={(valid) => markCrop(side, valid)}>
                    {editable && choices.includes(side) && (
                      <button type="button" className="secondary-button" disabled={working || !cropsDecoded}
                        aria-label={`Übernehmen: ${label} · ${decisionLabel(decision)}`}
                        onClick={() => void resolve(side)}>Übernehmen</button>
                    )}
                  </DecisionPreview>
                );
              })}
            </div>
            {previewFailed && <div className="settings-feedback settings-feedback--error">
              <p role="alert">Die geprüfte Vorschau konnte nicht geladen werden.</p>
              <button className="secondary-button" type="button" disabled={working}
                onClick={() => setPreviewRetry((previous) => previous + 1)}>Vorschau erneut laden</button>
            </div>}
            {fullPhoto && <div className="shared-library__photo">
              <button className="secondary-button" type="button" aria-expanded={showPhoto} aria-controls="shared-library-original"
                onClick={() => setShowPhoto(!showPhoto)}>
                {showPhoto ? "Ganzes Foto ausblenden" : "Ganzes Foto ansehen"}
              </button>
              <div id="shared-library-original" hidden={!showPhoto}>
                {showPhoto && <img className="shared-library__full-preview" src={fullPhoto} alt={`Geprüftes Foto · ${selectedCase.relative_path}`} />}
              </div>
            </div>}
          </>}
        </section>}
        <ul className="shared-library__case-list" hidden={Boolean(selectedCase)} ref={caseList}>
          {conflicts.map((item, index) => <li key={item.id}>
            <div>
              <strong>{item.relative_path}</strong>
              <p>{decisionLabel(item.local)} · {decisionLabel(item.shared)}</p>
              {item.source_path && <p>{item.source_path}</p>}
            </div>
            <button type="button" className="secondary-button" aria-label={`Fall prüfen: ${item.relative_path}${item.source_path ? ` · ${item.source_path}` : ""} · ${index + 1} von ${conflicts.length}`}
              onClick={(event) => openCase(item, event.currentTarget)}>Fall prüfen</button>
          </li>)}
        </ul>
        </div>
      </>}
      {working && <p role="status">Änderung wird gespeichert …</p>}
      {notice && !resource?.resolution_pending && <p className="settings-feedback" role="status">{notice}</p>}
      {actionError && <p className="settings-feedback settings-feedback--error" role="alert">{actionError}</p>}
      {!selectedCase && resource?.available !== false && <a href={navigationHash({ page: "settings", settingsSection: "daten" })}>Gemeinsame Ordner in den Einstellungen</a>}
    </section>
  );
}
