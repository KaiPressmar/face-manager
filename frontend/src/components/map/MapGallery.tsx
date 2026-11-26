import React, { useCallback, useEffect, useRef, useState } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import {
  fetchImageDetail, type FaceImage, type MapImage, type MapImageCursor,
} from "../../utils/api";
import type { GeoPhotoSelection } from "../../utils/geoPhotoSelection";
import { countWithLabel } from "../../utils/germanFormatting";
import FullscreenImageGallery from "../people/FullscreenImageGallery";
import { fetchMapSelection } from "./mapData";
import MapPhotoTile from "./MapPhotoTile";

export interface MapGalleryOpening {
  selection: GeoPhotoSelection;
  items: MapImage[];
  total: number;
  cursor: MapImageCursor | null;
  index: number | null;
  label: string;
  inferredImageIds?: readonly number[];
}

interface Props {
  opening: MapGalleryOpening;
  onClose: () => void;
  onOpenImages?: (selection: GeoPhotoSelection) => void;
  onAddToAlbum?: (opener: HTMLElement, selection: GeoPhotoSelection, photoCount: number) => void;
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
}

export default function MapGallery({ opening, onClose, onOpenImages, onAddToAlbum, onNavigateToCluster }: Props) {
  const [page, setPage] = useState(opening);
  const [index, setIndex] = useState(opening.index);
  const [pending, setPending] = useState(false);
  const [pageError, setPageError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [retry, setRetry] = useState(0);
  const [detail, setDetail] = useState<{ index: number; image: FaceImage } | null>(null);
  const more = useRef<AbortController | null>(null);
  const gridRef = useRef<HTMLElement>(null);
  const statusRef = useRef<HTMLDivElement>(null);
  const openGrid = opening.index === null;
  const photoId = index === null ? undefined : page.items[index]?.id;
  const closePhoto = () => {
    if (!openGrid) {
      onClose();
      return;
    }
    const target = index;
    setIndex(null);
    setDetail(null);
    setDetailError("");
    window.requestAnimationFrame(() => {
      gridRef.current?.querySelector<HTMLElement>(`[data-gallery-index="${target}"]`)?.focus();
    });
  };
  useModalFocus(gridRef, onClose, true, openGrid && index === null);
  useModalFocus(statusRef, closePhoto, true, index !== null && !detail);
  useEffect(() => () => more.current?.abort(), []);

  const loadMore = useCallback(async () => {
    if (more.current || !page.cursor) return;
    const controller = new AbortController();
    more.current = controller;
    setPending(true);
    setPageError("");
    try {
      const next = await fetchMapSelection(opening.selection, 36, page.cursor, controller.signal);
      if (!controller.signal.aborted) {
        const seen = new Set(page.items.map((image) => image.id));
        const items = [...page.items, ...next.items.filter((image) => !seen.has(image.id))];
        const cursor = next.next_cursor?.before_id === page.cursor.before_id ? null : next.next_cursor;
        setPage({ ...page, items, total: next.total, cursor });
        if (!cursor) setIndex((current) => current === null || current < items.length ? current : Math.max(0, items.length - 1));
      }
    } catch (error) {
      if (!controller.signal.aborted) setPageError(error instanceof Error ? error.message : "Weitere Fotos konnten nicht geladen werden.");
    } finally {
      if (more.current === controller) {
        more.current = null;
        if (!controller.signal.aborted) setPending(false);
      }
    }
  }, [opening.selection, page]);

  useEffect(() => {
    if (index !== null && photoId === undefined && page.cursor && !pending && !pageError) void loadMore();
  }, [index, photoId, page.cursor, pending, pageError, loadMore]);

  useEffect(() => {
    if (photoId === undefined || index === null) return;
    const controller = new AbortController();
    void fetchImageDetail(photoId, controller.signal).then((image) => {
      if (controller.signal.aborted) return;
      setDetail({ index, image });
      if ((!image.image_width || !image.image_height) && image.faces.length) {
        void fetchImageDetail(photoId, controller.signal, { resolveGeometry: true }).then((resolved) => {
          if (!controller.signal.aborted && resolved.id === image.id && resolved.content_hash === image.content_hash &&
              resolved.image_width && resolved.image_height) {
            const boxes = new Map(resolved.faces.map((face) => [face.id, face]));
            if (image.faces.every((face) => boxes.has(face.id))) {
              setDetail({ index, image: { ...image, image_width: resolved.image_width, image_height: resolved.image_height,
                faces: image.faces.map((face) => {
                  const box = boxes.get(face.id)!;
                  return { ...face, bbox_x: box.bbox_x, bbox_y: box.bbox_y, bbox_w: box.bbox_w, bbox_h: box.bbox_h };
                }),
              } });
            }
          }
        }).catch(() => {});
      }
    }).catch((error) => {
      if (!controller.signal.aborted) setDetailError(error instanceof Error ? error.message : "Das Foto konnte nicht geöffnet werden.");
    });
    return () => controller.abort();
  }, [index, photoId, retry]);

  const openPhoto = (next: number) => {
    setDetailError("");
    if (next === index) {
      if (pageError && next >= page.items.length) void loadMore();
      else setRetry((value) => value + 1);
    } else {
      setPageError("");
      setIndex(next);
    }
  };
  const error = detailError || (index !== null && index >= page.items.length ? pageError : "");
  const total = page.cursor ? Math.max(page.total, page.items.length + 1) : page.items.length;

  return <>
    {openGrid && <section ref={gridRef} hidden={index !== null} className="gps-selection-gallery"
      role="dialog" aria-modal="true" aria-label="Galerie der Kartenauswahl">
      <header className="gps-selection-gallery__header">
        <div><h2>Galerie der Kartenauswahl</h2><p>{opening.label} · {countWithLabel(total, "Foto", "Fotos")}</p>
          {!!opening.inferredImageIds?.length && <p>Fotos ohne gespeicherten Standort sind nach Aufnahmezeit zugeordnet.</p>}
        </div>
        <div className="gps-selection-gallery__actions">
          {onOpenImages && <button type="button" onClick={() => onOpenImages(opening.selection)}>In Bilder öffnen</button>}
          {onAddToAlbum && total > 0 && <button type="button" onClick={(event) => onAddToAlbum(event.currentTarget, opening.selection, total)}>Zum Album hinzufügen</button>}
          <button type="button" data-initial-focus onClick={onClose}>Galerie schließen</button>
        </div>
      </header>
      <div className="gps-selection-gallery__content">
        <div className="gps-selection-gallery__grid">
          {page.items.map((image, position) => <MapPhotoTile key={image.id} image={image} position={position}
            inferred={opening.inferredImageIds?.length ? opening.inferredImageIds.includes(image.id) : undefined}
            onOpen={() => openPhoto(position)} />)}
        </div>
        {pageError && <p className="gps-page__error" role="alert">{pageError}</p>}
        {page.cursor && <button type="button" className="gps-page__more" disabled={pending} onClick={() => void loadMore()}>
          {pending ? "Weitere Fotos werden geladen …" : pageError ? "Weitere Fotos erneut laden" : "Weitere Fotos laden"}
        </button>}
      </div>
    </section>}
    {index !== null && detail && <FullscreenImageGallery images={[detail.image]} activeIndex={0}
      onNavigateToCluster={onNavigateToCluster} onChange={() => {}} onClose={closePhoto}
      closeLabel={openGrid ? "Zurück zur Galerie" : undefined}
      sequence={{
        activeIndex: detail.index, length: total, wrap: false, itemLabel: "Foto",
        onChange: openPhoto, loading: detail.index !== index && !error, error,
        onRetry: () => openPhoto(index),
      }} />}
    {index !== null && !detail && <div ref={statusRef} role="dialog" aria-modal="true" aria-label="Foto öffnen" className="gps-page__gallery-wait">
      <p role={error ? "alert" : "status"}>{error || "Foto wird geöffnet …"}</p>
      {error && <button type="button" onClick={() => openPhoto(index)}>Erneut versuchen</button>}
      <button type="button" onClick={closePhoto}>{error ? "Schließen" : "Abbrechen"}</button>
    </div>}
  </>;
}
