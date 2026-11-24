import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import Masonry from "react-masonry-css";
import FaceOverlay from "./FaceOverlay";
import { assignLabelLanes } from "../../utils/faceLabels";
import FullscreenImageGallery from "./FullscreenImageGallery";
import {
  deleteImage,
  FaceImage,
  imageThumbnailUrl,
  openImageLocation,
} from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import { copyTextToClipboard } from "../../utils/clipboard";

/** Which face markers to draw on a picture. */
export type FaceOverlayMode = "all" | "assigned" | "none";
/** Visual density of the responsive masonry grid. */
export type ImageGridSize = "xsmall" | "small" | "medium" | "large";
type PreviewState = { w: number; h: number } | "error";

interface ImageGridProps {
  images: FaceImage[];
  isLoading: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  faceOverlayMode: FaceOverlayMode;
  gridSize: ImageGridSize;
  galleryRevision: number;
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
  onLoadMore: () => void;
  hasActiveFilters: boolean;
  onResetFilters: () => void;
  onImageDeleted: (imageId: number) => void;
}

const BREAKPOINT_COLUMNS: Record<ImageGridSize, Record<string, number>> = {
  xsmall: {
    default: 8,
    1900: 7,
    1650: 6,
    1400: 5,
    1100: 4,
    850: 3,
    620: 2,
    440: 1,
  },
  small: {
    default: 6,
    1800: 5,
    1400: 4,
    1000: 3,
    700: 2,
    480: 1,
  },
  medium: {
    default: 4,
    1400: 3,
    900: 2,
    600: 1,
  },
  large: {
    default: 3,
    1400: 2,
    800: 1,
  },
};

const COLUMN_PRELOAD_VIEWPORTS = 1.5;
const GRID_PRELOAD_VIEWPORTS = 2.5;

const ImageGrid: React.FC<ImageGridProps> = ({
  images,
  isLoading,
  hasMore,
  isLoadingMore,
  faceOverlayMode,
  gridSize,
  galleryRevision,
  onNavigateToCluster,
  onLoadMore,
  hasActiveFilters,
  onResetFilters,
  onImageDeleted,
}) => {
  const [galleryImageId, setGalleryImageId] = useState<number | null>(null);
  const galleryIndex = galleryImageId === null ? -1 : images.findIndex((image) => image.id === galleryImageId);
  const currentImagesRef = useRef(images);
  const [fileActionMessage, setFileActionMessage] = useState<string | null>(null);
  const fileActionTimerRef = useRef<number | null>(null);
  const [previewStates, setPreviewStates] = useState<Record<string, PreviewState>>({});
  const gridRef = useRef<HTMLDivElement | null>(null);
  const rafIdRef = useRef<number | null>(null);
  const breakpointCols = BREAKPOINT_COLUMNS[gridSize];

  useEffect(
    () => () => {
      if (fileActionTimerRef.current !== null) {
        window.clearTimeout(fileActionTimerRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    const scrollContainer = gridRef.current?.closest(".page-content");
    if (!(scrollContainer instanceof HTMLElement)) return;

    const scheduleCheck = () => {
      if (rafIdRef.current !== null) return;
      rafIdRef.current = window.requestAnimationFrame(() => {
        rafIdRef.current = null;

        if (isLoading || isLoadingMore || !hasMore || !gridRef.current) return;

        const containerRect = scrollContainer.getBoundingClientRect();
        const viewportBottom = containerRect.bottom;
        const viewportHeight = scrollContainer.clientHeight;
        const gridRect = gridRef.current.getBoundingClientRect();
        const gridRemaining = gridRect.bottom - viewportBottom;
        const gridThreshold = viewportHeight * GRID_PRELOAD_VIEWPORTS;

        const columnElements = Array.from(
          gridRef.current.querySelectorAll(".masonry-column"),
        ) as HTMLElement[];
        const shortestColumnRemaining =
          columnElements.length > 0
            ? Math.min(
                ...columnElements.map((column) => {
                  const cards = Array.from(column.children) as HTMLElement[];
                  const lastCard = cards.at(-1);
                  if (!lastCard) return Number.POSITIVE_INFINITY;
                  return lastCard.getBoundingClientRect().bottom - viewportBottom;
                }),
              )
            : Number.POSITIVE_INFINITY;
        const columnThreshold = viewportHeight * COLUMN_PRELOAD_VIEWPORTS;

        if (
          shortestColumnRemaining <= columnThreshold ||
          gridRemaining <= gridThreshold
        ) {
          onLoadMore();
        }
      });
    };

    scheduleCheck();
    scrollContainer.addEventListener("scroll", scheduleCheck, { passive: true });
    window.addEventListener("resize", scheduleCheck);

    let resizeObserver: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined" && gridRef.current) {
      resizeObserver = new ResizeObserver(() => {
        scheduleCheck();
      });
      resizeObserver.observe(gridRef.current);
      const columnElements = gridRef.current.querySelectorAll(".masonry-column");
      columnElements.forEach((column) => resizeObserver?.observe(column));
    }

    return () => {
      scrollContainer.removeEventListener("scroll", scheduleCheck);
      window.removeEventListener("resize", scheduleCheck);
      resizeObserver?.disconnect();
      if (rafIdRef.current !== null) {
        window.cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
    };
  }, [gridSize, hasMore, images.length, isLoading, isLoadingMore, onLoadMore]);

  useLayoutEffect(() => {
    currentImagesRef.current = images;
    if (galleryImageId !== null && galleryIndex < 0) setGalleryImageId(null);
  }, [images, galleryIndex, galleryImageId]);

  useLayoutEffect(() => {
    setGalleryImageId(null);
    setPreviewStates({});
  }, [galleryRevision]);

  const removeImage = async (image: FaceImage) => {
    const filename = image.filename || pathBasename(image.image_path) || "Bild";
    const confirmed = window.confirm(
      `„${filename}“ aus Face Manager entfernen?\n\nDie Originaldatei bleibt auf deinem Gerät erhalten.`,
    );
    if (!confirmed) return false;

    try {
      await deleteImage(image.id);
      const currentImages = currentImagesRef.current;
      const deletedIndex = currentImages.findIndex((item) => item.id === image.id);
      const adjacent = deletedIndex < 0 ? null : currentImages[deletedIndex + 1] ?? currentImages[deletedIndex - 1];
      setGalleryImageId((current) => current === image.id ? adjacent?.id ?? null : current);
      onImageDeleted(image.id);
      return true;
    } catch (error) {
      window.alert(
        error instanceof Error
          ? error.message
          : "Das Bild konnte nicht entfernt werden.",
      );
      return false;
    }
  };

  const showFileActionMessage = (message: string) => {
    setFileActionMessage(message);
    if (fileActionTimerRef.current !== null) {
      window.clearTimeout(fileActionTimerRef.current);
    }
    fileActionTimerRef.current = window.setTimeout(
      () => setFileActionMessage(null),
      2400,
    );
  };

  const copyImagePath = async (
    event: React.MouseEvent<HTMLButtonElement>,
    image: FaceImage,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await copyTextToClipboard(image.image_path);
      showFileActionMessage("Dateipfad kopiert");
    } catch (error) {
      showFileActionMessage(
        error instanceof Error ? error.message : "Dateipfad konnte nicht kopiert werden",
      );
    }
  };

  const revealImage = async (
    event: React.MouseEvent<HTMLButtonElement>,
    image: FaceImage,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await openImageLocation(image.id, image.image_path);
      showFileActionMessage("Dateispeicherort geöffnet");
    } catch (error) {
      showFileActionMessage(
        error instanceof Error ? error.message : "Dateispeicherort konnte nicht geöffnet werden",
      );
    }
  };

  if (isLoading) {
    const dummyHeights = ["350px", "200px", "400px", "250px", "300px", "220px"];

    return (
      <Masonry
        breakpointCols={breakpointCols}
        className="masonry-grid"
        columnClassName="masonry-column"
      >
        {Array.from({ length: 12 }).map((_, index) => (
          <div
            key={index}
            className="skeleton-card"
            style={{
              height: dummyHeights[index % dummyHeights.length],
              marginBottom: "16px",
            }}
          />
        ))}
      </Masonry>
    );
  }

  if (images.length === 0) {
    return (
      <div className="empty-image-state">
        <span className="folder-icon" aria-hidden="true" />
        <h3>Keine Bilder in dieser Auswahl</h3>
        <p>Für die gewählten Personen, Status oder Ordner gibt es keine Bilder.</p>
        {hasActiveFilters && (
          <button className="neon-card" type="button" onClick={onResetFilters}>
            Alle Filter zurücksetzen
          </button>
        )}
      </div>
    );
  }

  return (
    <>
      <div ref={gridRef}>
        <Masonry
          breakpointCols={breakpointCols}
          className="masonry-grid"
          columnClassName="masonry-column"
        >
          {images.map((img) => {
            const previewUrl = imageThumbnailUrl(img.id);
            const preview = previewStates[previewUrl];
            const previewSize = preview === "error" ? null : preview;
            const sourceSize = img.image_width && img.image_height
              ? { w: img.image_width, h: img.image_height }
              : null;
            const size = sourceSize ?? previewSize;
            const aspectRatio = size ? `${size.w} / ${size.h}` : "1/1";

            return (
              <div
                key={img.id}
                data-image-id={img.id}
                className="image-gallery-card"
                role="button"
                tabIndex={0}
                aria-label={`${img.filename || "Bild"} in Galerie öffnen`}
                onClick={() => setGalleryImageId(img.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setGalleryImageId(img.id);
                  }
                }}
                style={{
                  position: "relative",
                  width: "100%",
                  overflow: "hidden",
                  borderRadius: 6,
                  background: "var(--surface-1)",
                  border: "1px solid var(--border)",
                  aspectRatio,
                  marginBottom: "16px",
                  transition: "aspect-ratio 0.2s ease",
                }}
              >
                {preview === "error" ? (
                  <span className="image-gallery-card__error">Vorschau nicht verfügbar</span>
                ) : (
                  <img
                    src={previewUrl}
                    loading="lazy"
                    decoding="async"
                    onLoad={(event) => {
                      const { naturalWidth, naturalHeight } = event.currentTarget;
                      setPreviewStates((current) => ({
                        ...current,
                        [previewUrl]: { w: naturalWidth, h: naturalHeight },
                      }));
                    }}
                    onError={() => setPreviewStates((current) => ({ ...current, [previewUrl]: "error" }))}
                    style={{
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                      display: "block",
                    }}
                    alt=""
                  />
                )}

                <div className="image-file-actions">
                  <button
                    type="button"
                    title="Vollständigen Dateipfad kopieren"
                    aria-label={`Dateipfad von ${img.filename || "Bild"} kopieren`}
                    onClick={(event) => void copyImagePath(event, img)}
                    onKeyDown={(event) => event.stopPropagation()}
                  >
                    Pfad
                  </button>
                  <button
                    type="button"
                    title="Datei im Explorer oder Dateimanager anzeigen"
                    aria-label={`Speicherort von ${img.filename || "Bild"} öffnen`}
                    onClick={(event) => void revealImage(event, img)}
                    onKeyDown={(event) => event.stopPropagation()}
                  >
                    Ordner
                  </button>
                </div>

                <button
                  type="button"
                  className="image-delete-button"
                  title="Bild aus Face Manager entfernen"
                  aria-label={`${img.filename || "Bild"} aus Face Manager entfernen`}
                  onClick={(event) => {
                    event.stopPropagation();
                    void removeImage(img);
                  }}
                  onKeyDown={(event) => event.stopPropagation()}
                >
                  <span aria-hidden="true">×</span>
                  Entfernen
                </button>

                {img.location_count > 1 && (
                  <span
                    className="image-location-count"
                    title={img.locations.map((location) => location.path).join("\n")}
                  >
                    {img.location_count} Speicherorte
                  </span>
                )}

                <span className="image-open-cue" aria-hidden="true">
                  <span className="image-open-cue__icon">↗</span>
                  <span>Vollbild öffnen</span>
                </span>

                {faceOverlayMode !== "none" &&
                  sourceSize && previewSize &&
                  (() => {
                    const visibleFaces = img.faces.filter(
                      (face) =>
                        faceOverlayMode === "all" || Boolean(face.person_name),
                    );
                    const lanes = assignLabelLanes(visibleFaces, sourceSize.w);
                    return visibleFaces.map((face) => (
                      <FaceOverlay
                        key={face.id}
                        face={face}
                        naturalWidth={sourceSize.w}
                        naturalHeight={sourceSize.h}
                        stackIndex={lanes.get(face.id) ?? 0}
                        onNavigateToCluster={onNavigateToCluster}
                      />
                    ));
                  })()}
              </div>
            );
          })}
        </Masonry>
      </div>

      {fileActionMessage && (
        <div className="image-grid-file-toast" role="status">
          {fileActionMessage}
        </div>
      )}

      {isLoadingMore && <div className="image-grid-status">Weitere Bilder werden geladen…</div>}

      {galleryIndex >= 0 && (
        <FullscreenImageGallery
          images={images}
          activeIndex={galleryIndex}
          onNavigateToCluster={onNavigateToCluster}
          onChange={(index) => setGalleryImageId(images[index]?.id ?? null)}
          onClose={() => setGalleryImageId(null)}
          onDelete={(image) => removeImage(image as FaceImage)}
        />
      )}
    </>
  );
};

export default ImageGrid;
