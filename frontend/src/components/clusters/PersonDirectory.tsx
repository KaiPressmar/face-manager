import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { faceCropUrl, imageThumbnailUrl, type FaceImage } from "../../utils/api";
import { subscribeToTopic } from "../../utils/events";
import { countWithLabel } from "../../utils/germanFormatting";
import { pathBasename } from "../../utils/pathDisplay";
import {
  fetchPersonDirectory, fetchPersonPhotos, fetchPersonProfile, type PersonDirectoryEntry,
} from "../../utils/personDirectoryApi";
import type { NavigateToFaceGroup } from "../people/FaceOverlay";
import FullscreenImageGallery from "../people/FullscreenImageGallery";
import PersonManagement from "./PersonManagement";
import "./person-directory.css";

const PAGE_SIZE = 32;

interface Page<T> {
  items: T[];
  offset: number;
  total: number;
  has_more: boolean;
}

type PagedResult<T> = {
  key: string;
  revision: number;
  page?: Page<T>;
  nextOffset: number;
  error?: string;
};

function usePagedResults<T extends { id: number }>(
  key: string, revision: number, active: boolean,
  fetchPage: (offset: number, signal: AbortSignal) => Promise<Page<T>>,
) {
  const [result, setResult] = useState<PagedResult<T>>();
  const [loading, setLoading] = useState(false);
  const completed = useRef<PagedResult<T>>();
  const request = useRef<AbortController>();
  const current = result?.key === key ? result : undefined;

  const load = useCallback(async (offset = 0) => {
    if (!active || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    try {
      const prior = completed.current;
      const items = new Map<number, T>();
      if (offset && prior?.key === key) prior.page?.items.forEach((item) => items.set(item.id, item));
      const loadedUntil = offset === 0 && prior?.key === key ? prior.page?.items.length ?? 0 : offset;
      let page = await fetchPage(offset, controller.signal);
      for (;;) {
        if (controller.signal.aborted) return;
        page.items.forEach((item) => items.set(item.id, item));
        const end = page.offset + page.items.length;
        if (!page.has_more || !page.items.length || end >= loadedUntil) break;
        page = await fetchPage(end, controller.signal);
      }
      const next: PagedResult<T> = {
        key, revision,
        page: { ...page, items: [...items.values()], has_more: page.has_more && page.items.length > 0 },
        nextOffset: page.offset + page.items.length,
      };
      completed.current = next;
      setResult(next);
    } catch (error) {
      if (controller.signal.aborted) return;
      const prior = completed.current?.key === key ? completed.current : undefined;
      const next = { key, revision, page: prior?.page, nextOffset: offset,
        error: error instanceof Error ? error.message : "Die Daten konnten nicht geladen werden." };
      completed.current = next;
      setResult(next);
    } finally {
      if (request.current === controller) {
        request.current = undefined;
        setLoading(false);
      }
    }
  }, [active, fetchPage, key, revision]);

  useEffect(() => {
    const last = completed.current;
    if (active && (last?.key !== key || last.revision !== revision)) void load();
    return () => {
      request.current?.abort();
      request.current = undefined;
      setLoading(false);
    };
  }, [active, key, revision, load]);

  return {
    page: current?.page,
    error: current?.error,
    loading: active && (loading || current?.revision !== revision),
    loadMore: () => void load(current?.nextOffset),
    replaceItem: (item: T) => {
      const latest = completed.current;
      if (latest?.key !== key || !latest.page?.items.some(({ id }) => id === item.id)) return;
      const next = { ...latest, page: { ...latest.page, items: latest.page.items.map((entry) => entry.id === item.id ? item : entry) } };
      completed.current = next;
      setResult(next);
    },
  };
}

function Preview({ src, fallback, eager = false }: { src?: string; fallback: ReactNode; eager?: boolean }) {
  const [failed, setFailed] = useState<string>();
  return (
    <span className="person-directory__preview">
      {src && src !== failed ? (
        <img src={src} alt="" loading={eager ? "eager" : "lazy"} decoding="async" onError={() => setFailed(src)} />
      ) : <span className="person-directory__preview-fallback" aria-hidden="true">{fallback}</span>}
    </span>
  );
}

function Portrait({ person, eager }: { person: PersonDirectoryEntry; eager?: boolean }) {
  const initials = person.name.trim().split(/\s+/).slice(0, 2)
    .map((word) => Array.from(word)[0]?.toLocaleUpperCase("de")).join("");
  return <Preview src={person.representative_face_id == null ? undefined : faceCropUrl(person.representative_face_id)}
    fallback={initials} eager={eager} />;
}

interface PersonDirectoryProps {
  personId?: number | null;
  active: boolean;
  onOpenPerson: (id: number) => void;
  onBack: () => void;
  onReview: () => void;
  reviewCount?: number;
  onCorrect: (person: PersonDirectoryEntry) => void;
  onNavigateToCluster: NavigateToFaceGroup;
  onManualFaceSaved?: (detail: FaceImage, faceId: number) => void;
  revision?: number;
}

export default function PersonDirectory({
  personId, active, onOpenPerson, onBack, onReview, reviewCount, onCorrect,
  onNavigateToCluster, onManualFaceSaved, revision = 0,
}: PersonDirectoryProps) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const searchId = useId();
  const root = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const searchField = useRef<HTMLInputElement>(null);
  const lastPerson = useRef<{ id: number; top: number; query: string }>();
  const previousPerson = useRef(personId);
  const [localRevision, setLocalRevision] = useState(0);
  const [manualRevision, setManualRevision] = useState(0);
  const [managing, setManaging] = useState(false);
  const [profile, setProfile] = useState<{ id: number; value?: PersonDirectoryEntry | null; error?: string }>();
  const [profileRetry, setProfileRetry] = useState(0);
  const [gallery, setGallery] = useState<{ personId: number; imageId: number; pendingIndex?: number }>();
  const profileOpen = personId != null;
  const version = revision + localRevision;

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 160);
    return () => window.clearTimeout(timer);
  }, [search]);

  const loadPeople = useCallback((offset: number, signal: AbortSignal) =>
    fetchPersonDirectory({ search: query, offset, limit: PAGE_SIZE, signal }), [query]);
  const loadPhotos = useCallback((offset: number, signal: AbortSignal) =>
    fetchPersonPhotos(personId!, { offset, limit: PAGE_SIZE, signal }), [personId]);
  const people = usePagedResults(query, version + manualRevision, active && !profileOpen, loadPeople);
  const photos = usePagedResults(String(personId), version, active && profileOpen, loadPhotos);

  useEffect(() => {
    let refreshTimer: number | undefined;
    const unsubscribe = subscribeToTopic<{ reason?: string }>("clusters", (event) => {
      window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(() => setLocalRevision((value) => value + 1),
        event?.reason === "background_progress" ? 1800 : 300);
    });
    return () => {
      unsubscribe();
      window.clearTimeout(refreshTimer);
    };
  }, []);

  useEffect(() => {
    if (!active || personId == null) return;
    const controller = new AbortController();
    void fetchPersonProfile(personId, controller.signal).then((value) => {
      if (!controller.signal.aborted) setProfile({ id: personId, value });
    }, (error) => {
      if (!controller.signal.aborted) setProfile({ id: personId, error: error instanceof Error
        ? error.message : "Das Personenprofil konnte nicht geladen werden." });
    });
    return () => controller.abort();
  }, [active, personId, profileRetry, version]);

  useLayoutEffect(() => {
    if (!active || previousPerson.current === personId) return;
    previousPerson.current = personId;
    const scroller = root.current?.closest<HTMLElement>(".page-content");
    if (personId != null) {
      if (scroller) scroller.scrollTop = 0;
      heading.current?.focus({ preventScroll: true });
      return;
    }
    const last = lastPerson.current;
    if (last?.query === query) {
      const opener = root.current?.querySelector<HTMLElement>(`[data-directory-person="${last.id}"]`);
      if (opener) {
        if (scroller) scroller.scrollTop = last.top;
        opener.focus({ preventScroll: true });
        return;
      }
    }
    heading.current?.focus({ preventScroll: true });
  }, [active, personId, query]);

  useEffect(() => {
    setManaging(false);
    setGallery(undefined);
  }, [active, personId]);

  const photoItems = photos.page?.items ?? [];
  const currentGallery = active && gallery?.personId === personId ? gallery : undefined;
  const galleryIndex = currentGallery ? photoItems.findIndex(({ id }) => id === currentGallery.imageId) : -1;
  const activeProfile = profile?.id === personId ? profile : undefined;
  const person = activeProfile?.value;

  const pendingIndex = currentGallery?.pendingIndex;
  const nextPhotoId = pendingIndex === undefined ? undefined : photoItems[pendingIndex]?.id;
  useEffect(() => {
    if (nextPhotoId !== undefined) {
      setGallery((current) => current && { personId: current.personId, imageId: nextPhotoId });
    }
  }, [nextPhotoId]);

  const review = (
    <button type="button" className="secondary-button person-directory__review" onClick={onReview}
      aria-label={reviewCount ? `Prüfen, ${countWithLabel(reviewCount, "offener Vorgang", "offene Vorgänge")}` : undefined}>
      Prüfen{Boolean(reviewCount) && <span aria-hidden="true">{reviewCount?.toLocaleString("de-DE")}</span>}
    </button>
  );

  const openPerson = (id: number) => {
    lastPerson.current = { id, query, top: root.current?.closest(".page-content")?.scrollTop ?? 0 };
    onOpenPerson(id);
  };
  const clearSearch = () => {
    setSearch("");
    setQuery("");
    searchField.current?.focus();
  };
  const openPhoto = (id: number) => {
    if (personId != null) setGallery({ personId, imageId: id });
  };
  const navigatePhoto = (index: number) => {
    const next = photoItems[index];
    if (next) openPhoto(next.id);
    else {
      setGallery((current) => current && { ...current, pendingIndex: index });
      photos.loadMore();
    }
  };

  return (
    <section className="person-directory" ref={root}>
      {!profileOpen ? (
        <>
          <header className="person-directory__heading">
            <h1 ref={heading} tabIndex={-1}>Personen</h1>
            {review}
          </header>
          <div className="person-directory__search">
            <label htmlFor={searchId}>Person suchen</label>
            <div>
              <input id={searchId} ref={searchField} type="search" value={search}
                onChange={(event) => setSearch(event.target.value)} />
              {search && <button type="button" className="secondary-button" onClick={clearSearch}>Suche leeren</button>}
            </div>
          </div>
          <div aria-busy={people.loading || query !== search.trim()}>
            {people.page?.items.length ? (
              <ul className="person-directory__grid">
                {people.page.items.map((entry) => (
                  <li key={entry.id}>
                    <button type="button" className="person-directory__tile" data-directory-person={entry.id}
                      onClick={() => openPerson(entry.id)}>
                      <Portrait person={entry} />
                      <strong>{entry.name}</strong>
                      <span>{countWithLabel(entry.photo_count, "Foto", "Fotos")}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : people.page && query === search.trim() && !people.loading ? (
              <p>{query ? `Keine Personen für „${query}“ gefunden.` : "Es sind noch keine Personen benannt."}</p>
            ) : null}
            <PageControls loading={people.loading} error={people.error} hasMore={people.page?.has_more}
              loadMore={people.loadMore} itemLabel="Personen" />
          </div>
        </>
      ) : (
        <>
          <div className="person-directory__heading">
            <button type="button" className="secondary-button" onClick={onBack}><span aria-hidden="true">← </span>Alle Personen</button>
            {review}
          </div>
          <header className="person-directory__profile">
            {person && <Portrait person={person} eager />}
            <h1 ref={heading} tabIndex={-1}>{person?.name ?? "Person"}</h1>
          </header>
          {activeProfile?.error ? (
            <div className="person-directory__paging">
              <p role="alert">{activeProfile.error}</p>
              <button type="button" className="secondary-button" onClick={() => {
                setProfile(undefined);
                setProfileRetry((value) => value + 1);
              }}>Erneut versuchen</button>
            </div>
          ) : activeProfile?.value === null ? <p>Diese Person ist in der Bibliothek nicht mehr vorhanden.</p>
            : !person ? <p role="status">Personenprofil wird geladen …</p> : (
            <>
              <div className="person-directory__actions">
                <button type="button" className="secondary-button" onClick={() => onCorrect(person)}>Zuordnungen korrigieren</button>
                <button type="button" className="secondary-button" onClick={() => setManaging(true)}>Person verwalten</button>
              </div>
              <div className="person-directory__photos" aria-busy={photos.loading}>
                <h2>{countWithLabel(photos.page?.total ?? person.photo_count, "Foto", "Fotos")}</h2>
                {photoItems.length > 0 && (
                  <ul className="person-directory__grid person-directory__grid--photos">
                    {photoItems.map((photo) => (
                      <li key={photo.id} data-image-id={photo.id}>
                        <button type="button" className="person-directory__tile"
                          aria-label={`Foto öffnen: ${photo.filename || pathBasename(photo.image_path) || "Foto"}`}
                          onClick={() => openPhoto(photo.id)}>
                          <Preview src={imageThumbnailUrl(photo.id)} fallback="Vorschau nicht verfügbar" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {photos.page && photoItems.length === 0 && <p>Für diese Person sind keine Fotos vorhanden.</p>}
                <PageControls loading={photos.loading} error={photos.error} hasMore={photos.page?.has_more}
                  loadMore={photos.loadMore} itemLabel="Fotos" />
              </div>
              {active && managing && (
                <PersonManagement person={person} onClose={() => setManaging(false)} onChanged={(nextId?: number) => {
                  setManaging(false);
                  setLocalRevision((value) => value + 1);
                  if (nextId === undefined) onBack();
                  else if (nextId !== person.id) onOpenPerson(nextId);
                }} />
              )}
              {galleryIndex >= 0 && (
                <FullscreenImageGallery images={photoItems} activeIndex={galleryIndex}
                  onNavigateToCluster={(...target) => {
                    setGallery(undefined);
                    onNavigateToCluster(...target);
                  }}
                  onChange={navigatePhoto} onClose={() => setGallery(undefined)}
                  onManualFaceSaved={(detail, faceId) => {
                    photos.replaceItem(detail);
                    setManualRevision((value) => value + 1);
                    onManualFaceSaved?.(detail, faceId);
                  }}
                  sequence={{
                    activeIndex: galleryIndex, length: photos.page?.has_more ? photos.page.total : photoItems.length,
                    onChange: navigatePhoto, itemLabel: "Foto", wrap: false,
                    loading: pendingIndex !== undefined && photos.loading,
                    error: pendingIndex !== undefined ? photos.error : undefined,
                    onRetry: photos.loadMore,
                  }} />
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function PageControls({ loading, error, hasMore, loadMore, itemLabel }: {
  loading: boolean; error?: string; hasMore?: boolean; loadMore: () => void; itemLabel: string;
}) {
  return (
    <div className="person-directory__paging">
      {loading && <p role="status">{itemLabel} werden geladen …</p>}
      {!loading && error && <p role="alert">{error}</p>}
      {(error || hasMore) && <button type="button" className="secondary-button" disabled={loading} onClick={loadMore}>
        {error ? "Erneut versuchen" : `Weitere ${itemLabel} laden`}
      </button>}
    </div>
  );
}
