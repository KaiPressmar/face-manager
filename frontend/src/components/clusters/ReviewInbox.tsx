import React, { useEffect, useId, useMemo, useRef, useState } from "react";

import { faceCropUrl, type PersonSuggestion, type PersonSuggestionDecision, type ReviewSuggestion } from "../../utils/api";
import { useModalFocus } from "../../hooks/useModalFocus";
import "./review-workflow.css";

interface Props {
  personSuggestions: PersonSuggestion[];
  reviewSuggestions: ReviewSuggestion[];
  busy: boolean;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenCluster: (clusterId: number) => void;
  onOpenFace?: (faceId: number, proposalToken: string, opener: HTMLButtonElement) => void;
  onAcceptPerson: (decision: PersonSuggestionDecision) => Promise<boolean>;
  onAcceptSelectedPeople: (decisions: PersonSuggestionDecision[]) => void;
  onDismissPerson: (item: PersonSuggestion) => void;
  onAcceptReview: (suggestions: ReviewSuggestion[]) => void;
  onDismissReview: (item: ReviewSuggestion) => void;
  onOpenNewFaces: () => void;
}

const CARD_BATCH_SIZE = 18;
const FACE_BATCH_SIZE = 24;
type FaceStatus = "loaded" | "error";
type FaceLoads = Partial<Record<number, FaceStatus>>;
type OpenFace = (id: number, opener: HTMLButtonElement) => void;
const EMPTY_LOADS: FaceLoads = {};
const EMPTY_EXCLUSIONS: ReadonlySet<number> = new Set();

const personKey = (item: PersonSuggestion) => item.cluster_id + ":" + item.proposal_token;
const archiveKey = (item: ReviewSuggestion) =>
  JSON.stringify([item.library_id, item.cluster_id, item.review_status, item.face_count, item.support_count, item.preview_face_ids]);

function selectedFaces(ids: number[], loads: FaceLoads, excluded: ReadonlySet<number>) {
  return ids.filter((id) => loads[id] === "loaded" && !excluded.has(id));
}

function personDecision(
  item: PersonSuggestion, face_ids: number[], scope: PersonSuggestionDecision["scope"],
): PersonSuggestionDecision {
  return { cluster_id: item.cluster_id, person_id: item.person_id, proposal_token: item.proposal_token, face_ids, scope };
}

interface FaceSelectionProps {
  ids: number[];
  loads: FaceLoads;
  excluded: ReadonlySet<number>;
  blocked: boolean;
  eager?: boolean;
  onLoad: (id: number, status: FaceStatus) => void;
  onToggle?: (id: number) => void;
  onOpenFace?: OpenFace;
}

const FaceSelection: React.FC<FaceSelectionProps> = ({
  ids, loads, excluded, blocked, eager, onLoad, onToggle, onOpenFace,
}) => (
  <div className="review-person-faces">
    {ids.map((id, index) => (
      <div className="review-person-face" key={id}>
        {loads[id] === "error" ? (
          <span className="review-person-face__unavailable">Vorschau nicht verfügbar</span>
        ) : (
          <img
            src={faceCropUrl(id)}
            alt={"Gesicht " + (index + 1)}
            loading={eager ? "eager" : "lazy"}
            onLoad={(event) => onLoad(id, event.currentTarget.naturalWidth > 0 ? "loaded" : "error")}
            onError={() => onLoad(id, "error")}
          />
        )}
        {onToggle && (
          <label className="review-person-face__select">
            <input
              type="checkbox"
              aria-label={"Gesicht " + (index + 1) + " auswählen"}
              checked={loads[id] === "loaded" && !excluded.has(id)}
              disabled={blocked || loads[id] !== "loaded"}
              onChange={() => onToggle(id)}
            />
            <span>Auswählen</span>
          </label>
        )}
        {onOpenFace && (
          <button
            type="button"
            className="review-person-face__open"
            aria-label={"Original zu Gesicht " + (index + 1) + " öffnen"}
            disabled={blocked}
            onClick={(event) => onOpenFace(id, event.currentTarget)}
          >
            Original
          </button>
        )}
      </div>
    ))}
  </div>
);

const ComparisonFaces: React.FC<{ ids: number[]; busy: boolean; onOpenFace?: OpenFace }> = ({
  ids, busy, onOpenFace,
}) => {
  if (!ids.length) return null;
  return (
    <div className="review-person-reference">
      <strong>Bestätigte Vergleichsfotos</strong>
      <div className="review-person-reference__photos">
        {ids.slice(0, 2).map((id, index) => {
          const label = "Vergleichsfoto " + (index + 1);
          const image = <img alt={label} loading="lazy" src={faceCropUrl(id)} />;
          return onOpenFace ? (
            <button type="button" aria-label={label + " öffnen"} key={id} disabled={busy} onClick={(event) => onOpenFace(id, event.currentTarget)}>
              {image}
            </button>
          ) : <React.Fragment key={id}>{image}</React.Fragment>;
        })}
      </div>
    </div>
  );
};

interface PersonDetailProps {
  item: PersonSuggestion;
  current: PersonSuggestion | undefined;
  stale: boolean;
  busy: boolean;
  blocked: boolean;
  initialExclusions: ReadonlySet<number>;
  onClose: () => void;
  onRefresh: () => void;
  onOpenCluster: () => void;
  onOpenFace?: OpenFace;
  onAccept: Props["onAcceptPerson"];
}

const PersonDetail: React.FC<PersonDetailProps> = ({
  item, current, stale, busy, blocked, initialExclusions, onClose, onRefresh, onOpenCluster, onOpenFace, onAccept,
}) => {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const [visibleCount, setVisibleCount] = useState(FACE_BATCH_SIZE);
  const [loads, setLoads] = useState<FaceLoads>({});
  const [excluded, setExcluded] = useState<Set<number>>(() => new Set(initialExclusions));
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  useModalFocus(dialogRef, onClose, !busy && !saving);

  const ids = item.face_ids.slice(0, visibleCount);
  const selected = selectedFaces(ids, loads, excluded);
  const waiting = ids.some((id) => !loads[id]);
  const disabled = blocked || saving || stale;

  const accept = async () => {
    if (disabled || waiting || !selected.length) return;
    setSaving(true);
    setFailed(false);
    try {
      const saved = await onAccept(personDecision(item, selected, "detail"));
      if (saved) onClose();
      else setFailed(true);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop review-person-detail-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy && !saving) onClose();
    }}>
      <section className="review-person-detail" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="review-person-detail__header">
          <div>
            <h2 id={titleId}>Gruppe prüfen</h2>
            <p>Automatischer Vorschlag für <strong>{item.person_name}</strong></p>
          </div>
          <button type="button" className="modal-close-button" aria-label="Gruppenprüfung schließen" disabled={busy || saving} onClick={onClose}>×</button>
        </header>
        {stale || failed ? (
          <div className="review-workflow-feedback" role="alert">
            <span>{stale ? "Der Vorschlag hat sich geändert." : "Die Zuordnung konnte nicht gespeichert werden."}</span>
            <button type="button" className="secondary-button" disabled={busy || saving} onClick={() => {
              setFailed(false);
              onRefresh();
            }}>{current && current.proposal_token !== item.proposal_token ? "Aktuellen Vorschlag laden" : "Vorschläge neu laden"}</button>
          </div>
        ) : null}
        <ComparisonFaces ids={item.reference_face_ids} busy={disabled} onOpenFace={onOpenFace} />
        <h3>Gesichter zum Prüfen</h3>
        <p>{item.face_count} in der Gruppe · {item.face_ids.length} im Vorschlag</p>
        <FaceSelection
          ids={ids}
          loads={loads}
          excluded={excluded}
          blocked={disabled}
          eager
          onLoad={(id, status) => setLoads((currentLoads) => (
            currentLoads[id] === status ? currentLoads : { ...currentLoads, [id]: status }
          ))}
          onToggle={(id) => setExcluded((currentExcluded) => {
            const next = new Set(currentExcluded);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
          })}
          onOpenFace={onOpenFace}
        />
        {visibleCount < item.face_ids.length && (
          <button type="button" className="secondary-button" disabled={disabled} onClick={() => setVisibleCount((count) => count + FACE_BATCH_SIZE)}>
            Weitere Gesichter anzeigen
          </button>
        )}
        <footer className="review-person-detail__actions">
          <p role="status">{selected.length} von {item.face_count} Gesichtern ausgewählt</p>
          <button type="button" className="primary-button" onClick={() => void accept()} disabled={disabled || waiting || !selected.length}>Auswahl zuordnen</button>
          <button type="button" className="secondary-button" disabled={disabled} onClick={onOpenCluster}>Gesichtsgruppe öffnen</button>
        </footer>
      </section>
    </div>
  );
};

const ReviewInbox: React.FC<Props> = ({
  personSuggestions, reviewSuggestions, busy, loading, error, onRetry, onOpenCluster, onOpenFace,
  onAcceptPerson, onAcceptSelectedPeople, onDismissPerson, onAcceptReview, onDismissReview, onOpenNewFaces,
}) => {
  const [visiblePersonCount, setVisiblePersonCount] = useState(CARD_BATCH_SIZE);
  const [visibleReviewCount, setVisibleReviewCount] = useState(CARD_BATCH_SIZE);
  const [loadsByKey, setLoadsByKey] = useState<Record<string, FaceLoads>>({});
  const [excludedByKey, setExcludedByKey] = useState<Record<string, ReadonlySet<number>>>({});
  const [personMarks, setPersonMarks] = useState<Record<string, PersonSuggestionDecision>>({});
  const [archiveMarks, setArchiveMarks] = useState<Set<string>>(() => new Set());
  const [detail, setDetail] = useState<PersonSuggestion | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);

  const conflictingIds = useMemo(() => {
    const personIds = new Set(personSuggestions.map((item) => item.cluster_id));
    return new Set(reviewSuggestions.filter((item) => personIds.has(item.cluster_id)).map((item) => item.cluster_id));
  }, [personSuggestions, reviewSuggestions]);
  const blocked = busy || loading || !!error;
  const shownPeople = personSuggestions.slice(0, visiblePersonCount);
  const shownReviews = reviewSuggestions.slice(0, visibleReviewCount);
  const currentDetail = detail ? personSuggestions.find((item) => item.cluster_id === detail.cluster_id) : undefined;

  const faceLoaded = (key: string, id: number, status: FaceStatus) => {
    setLoadsByKey((current) => {
      if (current[key]?.[id] === status) return current;
      return { ...current, [key]: { ...current[key], [id]: status } };
    });
  };

  const previewsFor = (item: PersonSuggestion) => {
    const eligibleIds = new Set(item.face_ids);
    return item.preview_face_ids.filter((id) => eligibleIds.has(id));
  };

  const preparedPeople = shownPeople.map((item) => {
    const key = personKey(item);
    const ids = previewsFor(item);
    const loads = loadsByKey[key] ?? EMPTY_LOADS;
    const excluded = excludedByKey[key] ?? EMPTY_EXCLUSIONS;
    const selected = selectedFaces(ids, loads, excluded);
    const ready = !!item.proposal_token && selected.length > 0 && ids.every((id) => !!loads[id])
      && !conflictingIds.has(item.cluster_id);
    return { item, key, ids, loads, excluded, selected, ready };
  });

  const preparedArchive = shownReviews.map((item) => {
    const key = archiveKey(item);
    const loads = loadsByKey[key] ?? EMPTY_LOADS;
    const ready = item.face_count > 0 && item.face_count === item.preview_face_ids.length
      && item.preview_face_ids.every((id) => loads[id] === "loaded")
      && !conflictingIds.has(item.cluster_id);
    return { item, key, loads, ready };
  });

  const markablePeople = preparedPeople.filter(({ item, ready }) => item.recommended && ready);
  const markableArchive = preparedArchive.filter(({ item, ready }) => item.recommended && ready);
  const markedPeople = markablePeople.flatMap(({ key, selected }) => {
    const mark = personMarks[key];
    if (!mark || mark.face_ids.length !== selected.length
      || mark.face_ids.some((id, index) => id !== selected[index])) return [];
    return [mark];
  });
  const markedArchive = markableArchive.filter(({ key }) => archiveMarks.has(key));
  const markedKeys = new Set(markedPeople.map((item) => item.cluster_id + ":" + item.proposal_token));
  const markedFaceCount = markedPeople.reduce((count, item) => count + item.face_ids.length, 0);

  useEffect(() => {
    const peopleKeys = new Set(personSuggestions.map(personKey));
    const reviewKeys = new Set(reviewSuggestions.map(archiveKey));
    const trimRecord = <T,>(current: Record<string, T>, keep: (key: string) => boolean) => {
      const next = Object.entries(current).filter(([key]) => keep(key));
      return next.length === Object.keys(current).length ? current : Object.fromEntries(next);
    };
    setLoadsByKey((current) => trimRecord(current, (key) => peopleKeys.has(key) || reviewKeys.has(key)));
    setExcludedByKey((current) => trimRecord(current, (key) => peopleKeys.has(key)));
    setPersonMarks((current) => trimRecord(current, (key) => peopleKeys.has(key)));
    setArchiveMarks((current) => {
      const next = new Set(Array.from(current).filter((key) => reviewKeys.has(key)));
      return next.size === current.size ? current : next;
    });
  }, [personSuggestions, reviewSuggestions]);

  const togglePreviewFace = (key: string, id: number) => {
    setExcludedByKey((current) => {
      const next = new Set(current[key] ?? EMPTY_EXCLUSIONS);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...current, [key]: next };
    });
    setPersonMarks((current) => Object.fromEntries(Object.entries(current).filter(([storedKey]) => storedKey !== key)));
  };

  const closeDetail = (restoreFocus = true) => {
    setDetail(null);
    if (restoreFocus) {
      window.requestAnimationFrame(() => {
        if (document.activeElement !== document.body) return;
        if (openerRef.current?.isConnected && !openerRef.current.disabled) openerRef.current.focus({ preventScroll: true });
        else headingRef.current?.focus({ preventScroll: true });
      });
    }
  };

  const hasSuggestions = personSuggestions.length > 0 || reviewSuggestions.length > 0;
  return (
    <div className="review-inbox" aria-busy={loading || busy}>
      {error && <div className="review-workflow-feedback" role="alert">
        <span>{error}</span>
        <button type="button" className="secondary-button" onClick={onRetry} disabled={busy}>Erneut laden</button>
      </div>}
      {!hasSuggestions && loading && <p className="review-workflow-feedback" role="status">Vorschläge werden geladen …</p>}
      {!hasSuggestions && !loading && !error && (
        <section className="review-inbox-empty">
          <h2 ref={headingRef} tabIndex={-1}>Keine Vorschläge offen</h2>
          <p>Unter „Ohne Namen“ kannst du Gesichter auch ohne Vorschlag prüfen und zuweisen.</p>
          <button className="neon-button" onClick={onOpenNewFaces} type="button">Ohne Namen öffnen</button>
        </section>
      )}

      {(markablePeople.length > 0 || markableArchive.length > 0) && (
        <section className="review-inbox-bulk" aria-label="Sammelbestätigung">
          <div>
            <strong>Geprüfte Vorschläge gesammelt bestätigen</strong>
            <p>Bei Personenvorschlägen gilt deine Auswahl aus den geladenen Vorschaubildern.</p>
            <p role="status">{markedPeople.length + markedArchive.length} markiert · {markedFaceCount} Gesichter zur Zuordnung</p>
          </div>
          <div>
            {markablePeople.length + markableArchive.length > 1 && <button className="review-inbox-bulk__select" disabled={blocked} onClick={() => {
              setPersonMarks((current) => ({
                ...current,
                ...Object.fromEntries(markablePeople.map(({ item, key, selected }) => [key, personDecision(item, selected, "preview")])),
              }));
              setArchiveMarks((current) => new Set([...current, ...markableArchive.map(({ key }) => key)]));
            }} type="button">Angezeigte Empfehlungen markieren</button>}
            {markablePeople.length > 0 && <button disabled={blocked || markedPeople.length === 0} onClick={() => onAcceptSelectedPeople(markedPeople)} type="button">
              {markedPeople.length === 1 ? "1 Zuordnung bestätigen" : markedPeople.length + " Zuordnungen bestätigen"}
            </button>}
            {markableArchive.length > 0 && <button disabled={blocked || markedArchive.length === 0} onClick={() => onAcceptReview(markedArchive.map(({ item }) => item))} type="button">
              {markedArchive.length === 1 ? "1 Gruppe aussortieren" : markedArchive.length + " Gruppen aussortieren"}
            </button>}
          </div>
        </section>
      )}

      {personSuggestions.length > 0 && (
        <section className="review-inbox-section">
          <header><div><h2 ref={headingRef} tabIndex={-1}>Personenvorschläge prüfen</h2><p>Die Gesichter ähneln Fotos bereits bestätigter Personen.</p></div><b>{personSuggestions.length}</b></header>
          <div className="review-inbox-grid">
            {preparedPeople.map(({ item, key, ids, loads, excluded, selected, ready }) => {
              const conflicting = conflictingIds.has(item.cluster_id);
              const marked = markedKeys.has(key);
              const openFace: OpenFace | undefined = onOpenFace && ((id, opener) => onOpenFace(id, item.proposal_token, opener));
              return (
                <article className="review-inbox-card review-person-card" aria-label={"Personenvorschlag für " + item.person_name} key={key}
                  data-suggestion-anchor={`person:${item.cluster_id}:${item.person_id}`} data-marked={marked || undefined}>
                  <div className="review-inbox-card__identity">
                    <div><span>Automatischer Vorschlag</span><strong>{item.person_name}</strong></div>
                    <b className={item.recommended && !conflicting ? "review-confidence review-confidence--high" : "review-confidence"}>
                      {conflicting ? "Widersprüchliche Vorschläge" : item.recommended ? "Hohe Ähnlichkeit" : "Einzeln prüfen"}
                    </b>
                  </div>
                  <p className="review-person-card__summary">{item.face_count} in der Gruppe · {item.support_count} stützen den Vorschlag</p>
                  <div className="review-person-card__previews">
                    <div className="review-person-card__candidates">
                      <strong>Gesichter zum Prüfen</strong>
                      {ids.length ? (
                        <FaceSelection
                          ids={ids}
                          loads={loads}
                          excluded={excluded}
                          blocked={blocked || conflicting}
                          onLoad={(id, status) => faceLoaded(key, id, status)}
                          onToggle={(id) => togglePreviewFace(key, id)}
                          onOpenFace={openFace}
                        />
                      ) : <p>Keine Vorschau zum Zuordnen</p>}
                    </div>
                    <ComparisonFaces ids={item.reference_face_ids} busy={busy} onOpenFace={openFace} />
                  </div>
                  <p className="review-person-card__selection">{selected.length} von {item.face_count} Gesichtern ausgewählt</p>
                  <div className="review-inbox-card__actions">
                    {!conflicting && <button disabled={blocked || !ready} onClick={() => void onAcceptPerson(personDecision(item, selected, "preview"))} type="button">Auswahl zuordnen</button>}
                    <button disabled={busy} onClick={(event) => {
                      openerRef.current = event.currentTarget;
                      setDetail(item);
                    }} type="button">Gruppe prüfen</button>
                    <button disabled={busy} onClick={() => onOpenCluster(item.cluster_id)} type="button">Gesichtsgruppe öffnen</button>
                    <button disabled={blocked} onClick={() => onDismissPerson(item)} type="button">Vorschlag verwerfen</button>
                  </div>
                  {item.recommended && !conflicting && (
                    <label className="review-inbox-card__mark">
                      <input
                        type="checkbox"
                        checked={marked}
                        disabled={blocked || !ready}
                        aria-label={item.person_name + " für die Sammelbestätigung markieren"}
                        onChange={() => setPersonMarks((current) => {
                          const next = { ...current };
                          if (marked) delete next[key];
                          else next[key] = personDecision(item, selected, "preview");
                          return next;
                        })}
                      />
                      Für Sammelbestätigung markieren
                    </label>
                  )}
                </article>
              );
            })}
          </div>
          {visiblePersonCount < personSuggestions.length && <button className="review-inbox-load-more" onClick={() => setVisiblePersonCount((count) => count + CARD_BATCH_SIZE)} type="button">Weitere Personenvorschläge anzeigen</button>}
        </section>
      )}

      {reviewSuggestions.length > 0 && (
        <section className="review-inbox-section">
          <header><div><h2>Vorschläge für die Ablage prüfen</h2><p>Diese Gesichter ähneln früher aussortierten Gruppen.</p></div><b>{reviewSuggestions.length}</b></header>
          <div className="review-inbox-grid">
            {preparedArchive.map(({ item, key, loads, ready }) => {
              const label = item.review_status === "not_face" ? "Kein Gesicht" : "Unbekannte Person";
              const complete = item.face_count === item.preview_face_ids.length && item.face_count > 0;
              const conflicting = conflictingIds.has(item.cluster_id);
              return (
                <article className="review-inbox-card" key={key} data-suggestion-anchor={`archive:${item.cluster_id}`}
                  data-marked={archiveMarks.has(key) || undefined}>
                  <div className="review-inbox-card__preview">
                    <FaceSelection ids={item.preview_face_ids} loads={loads} excluded={EMPTY_EXCLUSIONS} blocked={busy} onLoad={(id, status) => faceLoaded(key, id, status)} />
                  </div>
                  <div className="review-inbox-card__body">
                    <div className="review-inbox-card__identity">
                      <div><span>Vorschlag für die Ablage</span><strong>{label}</strong></div>
                      <b className="review-confidence">{conflicting ? "Widersprüchliche Vorschläge" : item.recommended ? "Hohe Ähnlichkeit" : "Einzeln prüfen"}</b>
                    </div>
                    <p>{item.face_count} {item.face_count === 1 ? "Gesicht" : "Gesichter"} in der Gruppe</p>
                    {!complete && <p>Öffne die Gesichtsgruppe, um alle Gesichter zu prüfen.</p>}
                    <div className="review-inbox-card__actions">
                      {complete && !conflicting && <button disabled={blocked || !ready} onClick={() => onAcceptReview([item])} type="button">Als „{label}“ ablegen</button>}
                      <button disabled={busy} onClick={() => onOpenCluster(item.cluster_id)} type="button">Gesichtsgruppe öffnen</button>
                      <button disabled={blocked} onClick={() => onDismissReview(item)} type="button">Vorschlag verwerfen</button>
                    </div>
                    {complete && item.recommended && !conflicting && (
                      <label className="review-inbox-card__mark">
                        <input type="checkbox" checked={archiveMarks.has(key)} disabled={blocked || !ready} onChange={() => setArchiveMarks((current) => {
                          const next = new Set(current);
                          if (next.has(key)) next.delete(key);
                          else next.add(key);
                          return next;
                        })} />
                        Für Sammelablage markieren
                      </label>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
          {visibleReviewCount < reviewSuggestions.length && <button className="review-inbox-load-more" onClick={() => setVisibleReviewCount((count) => count + CARD_BATCH_SIZE)} type="button">Weitere Vorschläge für die Ablage anzeigen</button>}
        </section>
      )}

      {detail && (
        <PersonDetail
          key={personKey(detail)}
          item={detail}
          current={currentDetail}
          stale={!currentDetail || currentDetail.proposal_token !== detail.proposal_token || conflictingIds.has(detail.cluster_id)}
          blocked={blocked}
          busy={busy}
          initialExclusions={excludedByKey[personKey(detail)] ?? EMPTY_EXCLUSIONS}
          onAccept={onAcceptPerson}
          onClose={closeDetail}
          onRefresh={() => {
            if (currentDetail && currentDetail.proposal_token !== detail.proposal_token) setDetail(currentDetail);
            else onRetry();
          }}
          onOpenCluster={() => {
            closeDetail(false);
            onOpenCluster(detail.cluster_id);
          }}
          onOpenFace={onOpenFace && ((id, opener) => onOpenFace(id, detail.proposal_token, opener))}
        />
      )}
    </div>
  );
};

export default ReviewInbox;
