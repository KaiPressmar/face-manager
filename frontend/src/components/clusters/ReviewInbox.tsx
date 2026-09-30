import React, { useEffect, useMemo, useState } from "react";

import { faceCropUrl, PersonSuggestion, ReviewSuggestion } from "../../utils/api";

interface Props {
  personSuggestions: PersonSuggestion[];
  reviewSuggestions: ReviewSuggestion[];
  busy: boolean;
  onOpenCluster: (clusterId: number) => void;
  onAcceptPerson: (personId: number, clusterIds: number[]) => void;
  onAcceptAllPeople: (assignments: { person_id: number; cluster_ids: number[] }[]) => void;
  onDismissPerson: (clusterId: number) => void;
  onAcceptReview: (clusterIds: number[]) => void;
  onDismissReview: (clusterId: number) => void;
  onOpenNewFaces: () => void;
}

const FaceStrip: React.FC<{ ids: number[] }> = ({ ids }) => (
  <div className="review-inbox-card__faces">
    {ids.slice(0, 6).map((id) => <img alt="" key={id} loading="lazy" src={faceCropUrl(id)} />)}
  </div>
);

const CARD_BATCH_SIZE = 18;

const ReviewInbox: React.FC<Props> = ({
  personSuggestions,
  reviewSuggestions,
  busy,
  onOpenCluster,
  onAcceptPerson,
  onAcceptAllPeople,
  onDismissPerson,
  onAcceptReview,
  onDismissReview,
  onOpenNewFaces,
}) => {
  const personGroups = useMemo(() => {
    const groups = new Map<number, { name: string; items: PersonSuggestion[] }>();
    personSuggestions.forEach((item) => {
      const group = groups.get(item.person_id) ?? { name: item.person_name, items: [] };
      group.items.push(item);
      groups.set(item.person_id, group);
    });
    return Array.from(groups.entries());
  }, [personSuggestions]);
  const safePeople = personSuggestions.filter((item) => item.recommended);
  const safeReviews = reviewSuggestions.filter((item) => item.recommended);
  const personCards = useMemo(
    () => personGroups.flatMap(([personId, group]) =>
      group.items.map((item) => ({ personId, personName: group.name, item })),
    ),
    [personGroups],
  );
  const [visiblePersonCount, setVisiblePersonCount] = useState(CARD_BATCH_SIZE);
  const [visibleReviewCount, setVisibleReviewCount] = useState(CARD_BATCH_SIZE);

  useEffect(() => setVisiblePersonCount(CARD_BATCH_SIZE), [personSuggestions.length]);
  useEffect(() => setVisibleReviewCount(CARD_BATCH_SIZE), [reviewSuggestions.length]);

  if (personSuggestions.length === 0 && reviewSuggestions.length === 0) {
    return (
      <section className="review-inbox-empty">
        <span aria-hidden="true">✓</span>
        <h2>Keine Vorschläge offen</h2>
        <p>Alle Vorschläge sind bearbeitet. Neue Vorschläge erscheinen hier, nachdem weitere Bilder hinzugefügt wurden.</p>
        <button className="neon-button" onClick={onOpenNewFaces} type="button">Neue Gesichter prüfen</button>
      </section>
    );
  }

  return (
    <div className="review-inbox">
      {(safePeople.length > 1 || safeReviews.length > 1) && (
        <section className="review-inbox-bulk">
          <div><strong>Eindeutige Vorschläge gemeinsam übernehmen</strong><p>Nur sehr wahrscheinliche Treffer werden übernommen. Alle anderen bleiben zur Einzelprüfung.</p></div>
          <div>
            {safePeople.length > 1 && <button disabled={busy} onClick={() => onAcceptAllPeople(personGroups.map(([person_id, group]) => ({ person_id, cluster_ids: group.items.filter((item) => item.recommended).map((item) => item.cluster_id) })).filter((item) => item.cluster_ids.length))} type="button">{safePeople.length} Zuordnungen bestätigen</button>}
            {safeReviews.length > 1 && <button disabled={busy} onClick={() => onAcceptReview(safeReviews.map((item) => item.cluster_id))} type="button">{safeReviews.length} Gruppen aussortieren</button>}
          </div>
        </section>
      )}

      {personGroups.length > 0 && (
        <section className="review-inbox-section">
          <header><div><span>Bekannte Personen</span><h2>Neue Treffer bestätigen</h2><p>Diese Gruppen ähneln bereits bestätigten Personen.</p></div><b>{personSuggestions.length}</b></header>
          <div className="review-inbox-grid">
            {personCards.slice(0, visiblePersonCount).map(({ personId, personName, item }) => (
              <article className="review-inbox-card" key={item.cluster_id}>
                <button className="review-inbox-card__preview" onClick={() => onOpenCluster(item.cluster_id)} type="button"><FaceStrip ids={item.preview_face_ids} /></button>
                <div className="review-inbox-card__body">
                  <div className="review-inbox-card__identity"><div><span>Vorschlag</span><strong>{personName}</strong></div><b className={item.recommended ? "review-confidence review-confidence--high" : "review-confidence"}>{Math.round(item.confidence * 100)} %</b></div>
                  <p>{item.face_count} Gesichter in dieser Gruppe · noch nicht zugeordnet</p>
                  <div className="review-inbox-card__actions"><button disabled={busy} onClick={() => onAcceptPerson(personId, [item.cluster_id])} type="button">Als {personName} bestätigen</button><button onClick={() => onOpenCluster(item.cluster_id)} type="button">Genau prüfen</button><button disabled={busy} onClick={() => onDismissPerson(item.cluster_id)} type="button">Nicht passend</button></div>
                </div>
              </article>
            ))}
          </div>
          {visiblePersonCount < personCards.length && <button className="review-inbox-load-more" onClick={() => setVisiblePersonCount((count) => count + CARD_BATCH_SIZE)} type="button">Weitere {Math.min(CARD_BATCH_SIZE, personCards.length - visiblePersonCount)} Vorschläge anzeigen</button>}
        </section>
      )}

      {reviewSuggestions.length > 0 && (
        <section className="review-inbox-section">
          <header><div><span>Wiederkehrende Entscheidungen</span><h2>Bekanntes aussortieren</h2><p>Diese Gruppen ähneln Gesichtern, die du früher bewusst aussortiert hast.</p></div><b>{reviewSuggestions.length}</b></header>
          <div className="review-inbox-grid">
            {reviewSuggestions.slice(0, visibleReviewCount).map((item) => {
              const label = item.review_status === "not_face" ? "Kein Gesicht" : "Unbekannte Person";
              return <article className="review-inbox-card" key={item.cluster_id}>
                <button className="review-inbox-card__preview" onClick={() => onOpenCluster(item.cluster_id)} type="button"><FaceStrip ids={item.preview_face_ids} /></button>
                <div className="review-inbox-card__body">
                  <div className="review-inbox-card__identity"><div><span>Vorschlag</span><strong>{label}</strong></div><b className={item.recommended ? "review-confidence review-confidence--high" : "review-confidence"}>{Math.round(item.confidence * 100)} %</b></div>
                  <p>{item.face_count} Gesichter · ähnelt früher aussortierten Gesichtern</p>
                  <div className="review-inbox-card__actions"><button disabled={busy} onClick={() => onAcceptReview([item.cluster_id])} type="button">Als „{label}“ ablegen</button><button onClick={() => onOpenCluster(item.cluster_id)} type="button">Genau prüfen</button><button disabled={busy} onClick={() => onDismissReview(item.cluster_id)} type="button">Nicht passend</button></div>
                </div>
              </article>;
            })}
          </div>
          {visibleReviewCount < reviewSuggestions.length && <button className="review-inbox-load-more" onClick={() => setVisibleReviewCount((count) => count + CARD_BATCH_SIZE)} type="button">Weitere {Math.min(CARD_BATCH_SIZE, reviewSuggestions.length - visibleReviewCount)} Vorschläge anzeigen</button>}
        </section>
      )}
    </div>
  );
};

export default ReviewInbox;
