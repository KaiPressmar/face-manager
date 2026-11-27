import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { faceCropUrl, type PersonSuggestion, type PersonSuggestionDecision, type ReviewSuggestion } from "../../utils/api";
import type { ReviewProposal } from "../../utils/navigation";
import "./review-workflow.css";

interface Props {
  openProposal?: ReviewProposal;
  onNavigateProposal: (target?: ReviewProposal) => void;
  personSuggestions: PersonSuggestion[];
  reviewSuggestions: ReviewSuggestion[];
  busy: boolean;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenCluster: (clusterId: number) => void;
  onOpenFace?: (faceId: number, proposalToken: string, opener: HTMLButtonElement) => void;
  onAcceptPerson: (decision: PersonSuggestionDecision) => Promise<boolean>;
  onAcceptSelectedPeople: (decisions: PersonSuggestionDecision[]) => Promise<boolean>;
  onDismissPerson: (item: PersonSuggestion) => Promise<boolean>;
  onAcceptReview: (suggestions: ReviewSuggestion[]) => Promise<boolean>;
  onDismissReview: (item: ReviewSuggestion) => Promise<boolean>;
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
    {ids.map((id, index) => {
      const preview = loads[id] === "error" ? (
        <span className="review-person-face__unavailable">Vorschau nicht verfügbar</span>
      ) : (
        <img
          src={faceCropUrl(id)}
          alt={"Gesicht " + (index + 1)}
          loading={eager ? "eager" : "lazy"}
          onLoad={(event) => onLoad(id, event.currentTarget.naturalWidth > 0 ? "loaded" : "error")}
          onError={() => onLoad(id, "error")}
        />
      );
      return (
        <div className="review-person-face" key={id} data-selected={Boolean(onToggle && loads[id] === "loaded" && !excluded.has(id)) || undefined}>
          {onOpenFace ? (
            <button
              type="button"
              className="review-person-face__open"
              aria-label={"Original zu Gesicht " + (index + 1) + " öffnen"}
              title="Originalfoto öffnen"
              disabled={blocked}
              onClick={(event) => onOpenFace(id, event.currentTarget)}
            >
              {preview}
              <span className="review-person-face__expand" aria-hidden="true">↗</span>
            </button>
          ) : preview}
          {onToggle && (
            <label className="review-person-face__select">
              <input
                type="checkbox"
                aria-label={"Gesicht " + (index + 1) + " auswählen"}
                checked={loads[id] === "loaded" && !excluded.has(id)}
                disabled={blocked || loads[id] !== "loaded"}
                onChange={() => onToggle(id)}
              />
            </label>
          )}
        </div>
      );
    })}
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

const MoreActions: React.FC<React.PropsWithChildren> = ({ children }) => (
  <details className="review-inbox-more">
    <summary>Weitere Aktionen</summary>
    <div className="review-inbox-more__actions">{children}</div>
  </details>
);

const facesLabel = (count: number) => count === 1 ? "1 Gesicht" : `${count} Gesichter`;
const archiveLabel = (item: ReviewSuggestion) =>
  item.review_status === "not_face" ? "Kein Gesicht" : "Unbekannte Person";

type Proposal =
  | { kind: "person"; item: PersonSuggestion }
  | { kind: "archive"; item: ReviewSuggestion };

const itemKey = (proposal: Proposal) => proposal.kind === "person" ? personKey(proposal.item) : archiveKey(proposal.item);
const targetMatches = (proposal: Proposal, target: ReviewProposal) =>
  proposal.kind === target.kind && proposal.item.cluster_id === target.clusterId;

interface DetailProps {
  proposal: Proposal;
  current: Proposal | undefined;
  blocked: boolean;
  conflicting: boolean;
  onClose: () => void;
  onRefresh: () => void;
  onOpenCluster: () => void;
  onOpenFace?: OpenFace;
  onAccept: Props["onAcceptPerson"];
  onAcceptReview: Props["onAcceptReview"];
  onDismiss: () => Promise<boolean>;
}

const ProposalDetail: React.FC<DetailProps> = ({
  proposal, current, blocked, conflicting, onClose, onRefresh, onOpenCluster, onOpenFace,
  onAccept, onAcceptReview, onDismiss,
}) => {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const [visibleCount, setVisibleCount] = useState(FACE_BATCH_SIZE);
  const [loads, setLoads] = useState<FaceLoads>({});
  const [excluded, setExcluded] = useState<ReadonlySet<number>>(EMPTY_EXCLUSIONS);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const person = proposal.kind === "person" ? proposal.item : undefined;
  const archive = proposal.kind === "archive" ? proposal.item : undefined;
  const allIds = person?.face_ids ?? archive?.preview_face_ids ?? [];
  const ids = allIds.slice(0, visibleCount);
  const selected = selectedFaces(ids, loads, excluded);
  const waiting = ids.some((id) => !loads[id]);
  const completeArchive = !!archive && archive.face_count > 0 && archive.face_count === allIds.length;
  const stale = !current || itemKey(current) !== itemKey(proposal);
  const disabled = blocked || saving || stale || conflicting;
  const canAssign = !disabled && !!person?.proposal_token && !waiting && selected.length > 0;
  const canFile = !disabled && completeArchive && allIds.every((id) => loads[id] === "loaded");

  useLayoutEffect(() => titleRef.current?.focus({ preventScroll: true }), []);

  const save = async (action: () => Promise<boolean>) => {
    if (disabled) return;
    setSaving(true);
    setFailed(false);
    try {
      if (await action()) onClose();
      else setFailed(true);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="review-person-detail" aria-label={person ? "Gruppe prüfen" : "Ablagevorschlag prüfen"}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || saving) return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}>
      <button className="review-person-detail__back" type="button" disabled={saving} onClick={onClose}>
        <span aria-hidden="true">← </span>Zu den Vorschlägen
      </button>
      <header>
        <h2 ref={titleRef} tabIndex={-1}>
          {person ? `Zuordnung zu ${person.person_name}` : `Als „${archiveLabel(archive!)}“ ablegen`}
        </h2>
        <p>{person ? `${person.face_count} in der Gruppe · ${allIds.length} im Vorschlag` : facesLabel(archive!.face_count)}</p>
      </header>
      {(stale || failed || conflicting) && (
        <div className="review-workflow-feedback" role="alert">
          <span>{stale ? "Der Vorschlag hat sich geändert." : conflicting
            ? "Widersprüchliche Vorschläge. Prüfe die Gesichtsgruppe einzeln."
            : "Die Änderung konnte nicht gespeichert werden."}</span>
          {(stale || failed) && (
            <button type="button" className="secondary-button" disabled={blocked || saving} onClick={() => {
              setFailed(false);
              onRefresh();
            }}>{current && stale ? "Aktuellen Vorschlag laden" : "Vorschläge neu laden"}</button>
          )}
        </div>
      )}
      <div className="review-person-detail__evidence">
        {person && (
          <ComparisonFaces ids={person.reference_face_ids} busy={disabled} onOpenFace={onOpenFace} />
        )}
        <div>
          <h3>{person ? "Gesichter zum Prüfen" : "Gesichter in der Gruppe"}</h3>
          {person && <p className="review-person-detail__hint">Foto öffnet das Original. Das Häkchen wählt das Gesicht aus.</p>}
          {archive && !completeArchive && <p className="review-person-detail__hint">Öffne die Gesichtsgruppe, um alle Gesichter zu prüfen.</p>}
          <FaceSelection
            ids={ids}
            loads={loads}
            excluded={excluded}
            blocked={disabled}
            eager
            onLoad={(id, status) => setLoads((currentLoads) =>
              currentLoads[id] === status ? currentLoads : { ...currentLoads, [id]: status })}
            onToggle={person ? (id) => setExcluded((before) => {
              const next = new Set(before);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            }) : undefined}
            onOpenFace={onOpenFace}
          />
          {visibleCount < allIds.length && (
            <button type="button" className="secondary-button" disabled={saving}
              onClick={() => setVisibleCount((count) => count + FACE_BATCH_SIZE)}>Weitere Gesichter anzeigen</button>
          )}
        </div>
      </div>
      <footer className="review-person-detail__actions">
        {person && (
          <>
            <button type="button" className="primary-button" disabled={!canAssign}
              onClick={() => void save(() => onAccept(personDecision(person, selected, "detail")))}>
              {selected.length > 0 ? `${facesLabel(selected.length)} zuordnen` : "Gesichter zuordnen"}
            </button>
            <p role="status">{selected.length} von {allIds.length} ausgewählt</p>
          </>
        )}
        {archive && completeArchive && !conflicting && (
          <button type="button" className="primary-button" disabled={!canFile}
            onClick={() => void save(() => onAcceptReview([archive]))}>
            Als „{archiveLabel(archive)}“ ablegen
          </button>
        )}
        {archive && (
          <button type="button" className="secondary-button" disabled={saving} onClick={onOpenCluster}>
            Gesichtsgruppe öffnen
          </button>
        )}
        <MoreActions>
          {person && <button type="button" className="secondary-button" disabled={saving} onClick={onOpenCluster}>
            Gesichtsgruppe öffnen
          </button>}
          <button type="button" className="secondary-button" disabled={disabled} onClick={() => void save(onDismiss)}>
            Vorschlag verwerfen
          </button>
        </MoreActions>
      </footer>
    </section>
  );
};

function QueuePhotos({ ids, loads, onLoad }: {
  ids: number[];
  loads: FaceLoads;
  onLoad: (id: number, status: FaceStatus) => void;
}) {
  return (
    <span className="review-queue-row__photos" aria-hidden="true">
      {Array.from(new Set(ids)).map((id) => loads[id] === "error" ? (
        <span className="review-queue-row__placeholder" key={id}>–</span>
      ) : (
        <img key={id} alt="" loading="lazy" src={faceCropUrl(id)}
          onLoad={(event) => onLoad(id, event.currentTarget.naturalWidth > 0 ? "loaded" : "error")}
          onError={() => onLoad(id, "error")} />
      ))}
      {ids.length === 0 && <span className="review-queue-row__placeholder">–</span>}
    </span>
  );
}

const ReviewInbox: React.FC<Props> = ({
  openProposal, onNavigateProposal,
  personSuggestions, reviewSuggestions, busy, loading, error, onRetry, onOpenCluster, onOpenFace,
  onAcceptPerson, onAcceptSelectedPeople, onDismissPerson, onAcceptReview, onDismissReview, onOpenNewFaces,
}) => {
  const [visiblePersonCount, setVisiblePersonCount] = useState(CARD_BATCH_SIZE);
  const [visibleReviewCount, setVisibleReviewCount] = useState(CARD_BATCH_SIZE);
  const [loadsByKey, setLoadsByKey] = useState<Record<string, FaceLoads>>({});
  const [batch, setBatch] = useState<ReviewProposal["kind"] | null>(null);
  const [marks, setMarks] = useState<ReadonlySet<string>>(new Set());
  const [detailSnapshot, setDetailSnapshot] = useState<Proposal | undefined>();
  const rootRef = useRef<HTMLDivElement>(null);
  const queueRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const openerRef = useRef<{ button: HTMLButtonElement; next: HTMLButtonElement | undefined; scrollTop: number } | null>(null);
  const wasOpen = useRef(false);

  const proposals = useMemo<Proposal[]>(() => [
    ...personSuggestions.map((item): Proposal => ({ kind: "person", item })),
    ...reviewSuggestions.map((item): Proposal => ({ kind: "archive", item })),
  ], [personSuggestions, reviewSuggestions]);
  const conflictingIds = useMemo(() => {
    const personIds = new Set(personSuggestions.map((item) => item.cluster_id));
    return new Set(reviewSuggestions.filter((item) => personIds.has(item.cluster_id)).map((item) => item.cluster_id));
  }, [personSuggestions, reviewSuggestions]);

  const blocked = busy || loading || !!error;
  const shownPeople = personSuggestions.slice(0, visiblePersonCount);
  const shownArchive = reviewSuggestions.slice(0, visibleReviewCount);
  const current = openProposal ? proposals.find((proposal) => targetMatches(proposal, openProposal)) : undefined;
  const detail = openProposal && detailSnapshot && targetMatches(detailSnapshot, openProposal) ? detailSnapshot : current;

  useLayoutEffect(() => {
    if (!openProposal) {
      if (detailSnapshot) setDetailSnapshot(undefined);
    } else if (current && (!detailSnapshot || !targetMatches(detailSnapshot, openProposal))) {
      setDetailSnapshot(current);
    }
  }, [openProposal, current, detailSnapshot]);

  useLayoutEffect(() => {
    const opening = Boolean(openProposal);
    if (opening === wasOpen.current) return;
    wasOpen.current = opening;
    const scroller = rootRef.current?.closest<HTMLElement>(".page-content");
    if (opening) {
      scroller?.scrollTo({ top: 0 });
      return;
    }
    const opener = openerRef.current;
    openerRef.current = null;
    if (!opener) return;
    const frame = window.requestAnimationFrame(() => {
      if (!rootRef.current?.getClientRects().length || rootRef.current.closest("[inert]")) return;
      scroller?.scrollTo({ top: opener.scrollTop });
      const focus = [opener.button, opener.next, headingRef.current].find((node) =>
        node?.isConnected && node.getClientRects().length > 0 && !node.matches(":disabled"));
      focus?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [openProposal]);

  useEffect(() => {
    const keys = new Set(proposals.map(itemKey));
    setLoadsByKey((currentLoads) => {
      const kept = Object.entries(currentLoads).filter(([key]) => keys.has(key));
      return kept.length === Object.keys(currentLoads).length ? currentLoads : Object.fromEntries(kept);
    });
    setMarks((currentMarks) => {
      const kept = [...currentMarks].filter((key) => keys.has(key));
      return kept.length === currentMarks.size ? currentMarks : new Set(kept);
    });
  }, [proposals]);

  const faceLoaded = (key: string, id: number, status: FaceStatus) => {
    setLoadsByKey((currentLoads) => currentLoads[key]?.[id] === status ? currentLoads
      : { ...currentLoads, [key]: { ...currentLoads[key], [id]: status } });
  };

  const personIds = (item: PersonSuggestion) => {
    const eligible = new Set(item.face_ids);
    return item.preview_face_ids.filter((id) => eligible.has(id));
  };
  const batchEligible = (proposal: Proposal) => {
    const { item } = proposal;
    if (!item.recommended || conflictingIds.has(item.cluster_id)) return false;
    return proposal.kind === "person"
      ? !!proposal.item.proposal_token && personIds(proposal.item).length > 0
      : item.face_count > 0 && item.face_count === item.preview_face_ids.length;
  };
  const markable = (proposal: Proposal, ids: number[]) => {
    const loads = loadsByKey[itemKey(proposal)];
    return ids.length > 0 && batchEligible(proposal) && ids.every((id) => loads?.[id] === "loaded");
  };

  const chosenPeople = shownPeople.flatMap((item) => {
    const proposal = { kind: "person", item } as const;
    const ids = personIds(item);
    return marks.has(personKey(item)) && markable(proposal, ids) ? [personDecision(item, ids, "preview")] : [];
  });
  const chosenArchive = shownArchive.filter((item) => marks.has(archiveKey(item))
    && markable({ kind: "archive", item }, item.preview_face_ids));
  const readyBatchKeys = batch === "person" ? shownPeople.filter((item) => markable({ kind: "person", item }, personIds(item))).map(personKey)
    : shownArchive.filter((item) => markable({ kind: "archive", item }, item.preview_face_ids)).map(archiveKey);
  const chosenCount = batch === "person" ? chosenPeople.length : chosenArchive.length;
  const chosenFaces = batch === "person" ? chosenPeople.reduce((count, item) => count + item.face_ids.length, 0)
    : chosenArchive.reduce((count, item) => count + item.face_count, 0);

  const show = (proposal: Proposal, button: HTMLButtonElement) => {
    const buttons = Array.from(queueRef.current?.querySelectorAll<HTMLButtonElement>("button[data-proposal-open]") ?? []);
    const index = buttons.indexOf(button);
    openerRef.current = {
      button,
      next: buttons[index + 1] ?? buttons[index - 1],
      scrollTop: rootRef.current?.closest<HTMLElement>(".page-content")?.scrollTop ?? 0,
    };
    setDetailSnapshot(proposal);
    onNavigateProposal({ kind: proposal.kind, clusterId: proposal.item.cluster_id });
  };
  const toggleBatch = (kind: ReviewProposal["kind"]) => {
    setMarks(new Set());
    setBatch((currentBatch) => currentBatch === kind ? null : kind);
  };

  const row = (proposal: Proposal) => {
    const { item } = proposal;
    const person = proposal.kind === "person" ? proposal.item : undefined;
    const archive = proposal.kind === "archive" ? proposal.item : undefined;
    const key = itemKey(proposal);
    const inBatch = batch === proposal.kind && batchEligible(proposal);
    const ids = person ? personIds(person) : item.preview_face_ids;
    const previewIds = inBatch ? ids : person
      ? [...person.reference_face_ids.slice(0, 1), ...ids.slice(0, 2)] : ids.slice(0, 3);
    const label = person?.person_name ?? archiveLabel(archive!);
    const anchor = person ? `person:${item.cluster_id}:${person.person_id}` : `archive:${item.cluster_id}`;
    const summary = conflictingIds.has(item.cluster_id) ? "Widersprüchliche Vorschläge"
      : inBatch && person ? `${facesLabel(ids.length)} in der Vorschau` : facesLabel(item.face_count);
    const contents = (
      <>
        <QueuePhotos ids={previewIds} loads={loadsByKey[key] ?? EMPTY_LOADS}
          onLoad={(id, status) => faceLoaded(key, id, status)} />
        <span className="review-queue-row__text"><strong>{label}</strong><small>{summary}</small></span>
      </>
    );
    return (
      <article key={key} aria-label={person ? `Personenvorschlag für ${label}` : `Ablagevorschlag: ${label}`}
        data-suggestion-anchor={anchor} className="review-queue-row">
        {inBatch ? (
          <label className="review-queue-row__control" data-marked={marks.has(key) || undefined}>
            {contents}
            <input type="checkbox" checked={marks.has(key) && markable(proposal, ids)}
              aria-label={`${label} für die Sammelbestätigung markieren`}
              disabled={blocked || !markable(proposal, ids)}
              onChange={(event) => {
                const checked = event.currentTarget.checked;
                setMarks((before) => {
                  const next = new Set(before);
                  if (checked) next.add(key);
                  else next.delete(key);
                  return next;
                });
              }} />
          </label>
        ) : (
          <button type="button" className="review-queue-row__control" data-proposal-open disabled={busy}
            aria-label={person ? `Vorschlag für ${label} prüfen` : `Ablagevorschlag „${label}“ prüfen`}
            onClick={(event) => show(proposal, event.currentTarget)}>
            {contents}<span aria-hidden="true">›</span>
          </button>
        )}
      </article>
    );
  };

  return (
    <div ref={rootRef} className="review-inbox" aria-busy={loading || busy}>
      <div ref={queueRef} hidden={!!openProposal} className="review-queue">
        {error && <div className="review-workflow-feedback" role="alert">
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={onRetry} disabled={busy}>Erneut laden</button>
        </div>}
        {!proposals.length && loading && <p className="review-workflow-feedback" role="status">Vorschläge werden geladen …</p>}
        {!proposals.length && !loading && !error && (
          <section className="review-inbox-empty">
            <h2 ref={headingRef} tabIndex={-1}>Keine Vorschläge offen</h2>
            <button className="secondary-button" onClick={onOpenNewFaces} type="button">Ohne Namen öffnen</button>
          </section>
        )}
        {batch && (
          <section className="review-inbox-bulk" aria-label="Sammelbestätigung">
            <p role="status">{chosenCount === 1 ? "1 Vorschlag" : `${chosenCount} Vorschläge`} · {facesLabel(chosenFaces)}</p>
            <div>
              <button className="review-inbox-bulk__select" type="button" disabled={busy} onClick={() => toggleBatch(batch)}>
                Auswahl beenden
              </button>
              {readyBatchKeys.length > 1 && <button className="review-inbox-bulk__select" type="button"
                disabled={blocked || readyBatchKeys.every((key) => marks.has(key))}
                onClick={() => setMarks(new Set(readyBatchKeys))}>Geladene Empfehlungen markieren</button>}
              <button type="button" disabled={blocked || chosenCount === 0} onClick={() => {
                const result = batch === "person" ? onAcceptSelectedPeople(chosenPeople) : onAcceptReview(chosenArchive);
                void result.then((accepted) => {
                  if (accepted) {
                    setBatch(null);
                    setMarks(new Set());
                    requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
                  }
                });
              }}>
                {batch === "person" ? `${facesLabel(chosenFaces)} zuordnen` : chosenCount === 1 ? "1 Gruppe ablegen" : `${chosenCount} Gruppen ablegen`}
              </button>
            </div>
          </section>
        )}
        {shownPeople.length > 0 && (
          <section className="review-queue__section">
            <header className="review-queue__heading">
              <h2 ref={headingRef} tabIndex={-1}>Personenvorschläge</h2>
              {!batch && shownPeople.some((item) => batchEligible({ kind: "person", item })) && (
                <button type="button" className="secondary-button" disabled={blocked} onClick={() => toggleBatch("person")}>
                  Mehrere auswählen
                </button>
              )}
            </header>
            <div className="review-queue__items">{shownPeople.map((item) => row({ kind: "person", item }))}</div>
            {visiblePersonCount < personSuggestions.length && (
              <button className="review-inbox-load-more" type="button"
                onClick={() => setVisiblePersonCount((count) => count + CARD_BATCH_SIZE)}>Weitere Personenvorschläge anzeigen</button>
            )}
          </section>
        )}
        {shownArchive.length > 0 && (
          <section className="review-queue__section">
            <header className="review-queue__heading">
              <h2 ref={shownPeople.length ? undefined : headingRef} tabIndex={-1}>Vorschläge zum Aussortieren</h2>
              {!batch && shownArchive.some((item) => batchEligible({ kind: "archive", item })) && (
                <button type="button" className="secondary-button" disabled={blocked} onClick={() => toggleBatch("archive")}>
                  Mehrere Ablagevorschläge auswählen
                </button>
              )}
            </header>
            <div className="review-queue__items">{shownArchive.map((item) => row({ kind: "archive", item }))}</div>
            {visibleReviewCount < reviewSuggestions.length && (
              <button className="review-inbox-load-more" type="button"
                onClick={() => setVisibleReviewCount((count) => count + CARD_BATCH_SIZE)}>Weitere Ablagevorschläge anzeigen</button>
            )}
          </section>
        )}
      </div>
      {openProposal && (detail ? (
        <ProposalDetail key={itemKey(detail)} proposal={detail} current={current} blocked={blocked}
          conflicting={conflictingIds.has(detail.item.cluster_id)}
          onClose={() => onNavigateProposal()}
          onRefresh={() => current && itemKey(current) !== itemKey(detail) ? setDetailSnapshot(current) : onRetry()}
          onOpenCluster={() => onOpenCluster(detail.item.cluster_id)}
          onOpenFace={detail.kind === "person" && onOpenFace
            ? (id, opener) => onOpenFace(id, detail.item.proposal_token, opener) : undefined}
          onAccept={onAcceptPerson}
          onAcceptReview={onAcceptReview}
          onDismiss={() => detail.kind === "person" ? onDismissPerson(detail.item) : onDismissReview(detail.item)} />
      ) : (
        <section className="review-person-detail" aria-label="Vorschlag prüfen">
          <button type="button" className="review-person-detail__back" onClick={() => onNavigateProposal()}>
            <span aria-hidden="true">← </span>Zu den Vorschlägen
          </button>
          <p role={error ? "alert" : "status"}>{error ?? (loading ? "Vorschlag wird geladen …" : "Dieser Vorschlag ist nicht mehr offen.")}</p>
          {error && <button type="button" className="secondary-button" onClick={onRetry}>Erneut laden</button>}
        </section>
      ))}
    </div>
  );
};

export default ReviewInbox;
