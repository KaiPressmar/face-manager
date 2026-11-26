import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import Masonry from "react-masonry-css";
import FaceOverlay, { type NavigateToFaceGroup } from "./FaceOverlay";
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
import { imageAspectRatiosMatch } from "../../utils/imageGeometry";
import "./ImageGrid.css";

/** Which face markers to draw on a picture. */
export type FaceOverlayMode = "all" | "assigned" | "none";
/** Visual density of the responsive masonry grid. */
export type ImageGridSize = "xsmall" | "small" | "medium" | "large";
type PreviewState = { w: number; h: number } | "error";

function needsImageGeometry(image: FaceImage) {
  return image.faces.length > 0 && (!image.image_width || !image.image_height);
}

interface ImageGridProps {
  images: FaceImage[];
  isLoading: boolean;
  interactionsDisabled: boolean;
  isUpdating: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  faceOverlayMode: FaceOverlayMode;
  gridSize: ImageGridSize;
  onNavigateToCluster: NavigateToFaceGroup;
  onLoadMore: () => void;
  onImageGeometryNeeded: (image: FaceImage) => void;
  hasActiveFilters: boolean;
  onResetFilters: () => void;
  onImageDeleted: (imageId: number) => void;
  onManualFaceSaved: (detail: FaceImage, faceId: number) => void;
  photoSelection?: { ids: ReadonlySet<number>; onToggle: (id: number) => void };
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
  interactionsDisabled,
  isUpdating,
  hasMore,
  isLoadingMore,
  faceOverlayMode,
  gridSize,
  onNavigateToCluster,
  onLoadMore,
  onImageGeometryNeeded,
  hasActiveFilters,
  onResetFilters,
  onImageDeleted,
  onManualFaceSaved,
  photoSelection,
}) => {
  const [galleryImageId, setGalleryImageId] = useState<number | null>(null);
  const galleryIndex = galleryImageId === null ? -1 : images.findIndex((image) => image.id === galleryImageId);
  const currentImagesRef = useRef(images);
  const [fileActionMessage, setFileActionMessage] = useState<string | null>(null);
  const fileActionTimerRef = useRef<number | null>(null);
  const [previewStates, setPreviewStates] = useState<Record<string, PreviewState>>({});
  const loadedPreviewsRef = useRef(new Set<string>());
  const [previewRetries, setPreviewRetries] = useState<Record<string, number>>({});
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
                  const lastCard = column.lastElementChild;
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
    if (galleryImageId !== null && (galleryIndex < 0 || interactionsDisabled)) setGalleryImageId(null);
  }, [images, galleryIndex, galleryImageId, interactionsDisabled]);

  useEffect(() => {
    images.forEach((image) => {
      if (needsImageGeometry(image) && (image.id === galleryImageId ||
          (faceOverlayMode !== "none" && loadedPreviewsRef.current.has(imageThumbnailUrl(image.id))))) {
        onImageGeometryNeeded(image);
      }
    });
  }, [faceOverlayMode, galleryImageId, images, onImageGeometryNeeded]);

  const removeImage = async (image: FaceImage) => {
    const filename = image.filename || pathBasename(image.image_path) || "Bild";
    const confirmed = window.confirm(
      `„${filename}“ aus Face Manager entfernen?\n\nDadurch werden auch seine Personenzuordnungen entfernt. Deine Originaldateien findest du weiterhin an ihren Speicherorten.`,
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
        <p>Ändere die aktiven Filter, um andere Bilder zu sehen.</p>
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
      <div ref={gridRef} aria-busy={isUpdating || isLoadingMore}>
        <Masonry
          breakpointCols={breakpointCols}
          className="masonry-grid"
          columnClassName="masonry-column"
        >
          {images.map((img) => {
            const previewUrl = imageThumbnailUrl(img.id);
            const previewSrc = imageThumbnailUrl(img.id, previewRetries[previewUrl]);
            const preview = previewStates[previewUrl];
            const previewSize = preview === "error" ? null : preview;
            const imageSize = img.image_width && img.image_height
              ? { w: img.image_width, h: img.image_height }
              : null;
            const sourceSize = imageSize && (!previewSize || img.content_hash ||
              imageAspectRatiosMatch(imageSize.w, imageSize.h, previewSize.w, previewSize.h))
              ? imageSize
              : null;
            const size = sourceSize ?? previewSize;
            const aspectRatio = size ? `${size.w} / ${size.h}` : "1/1";

            return (
              <div
                key={img.id}
                data-image-id={img.id}
                className={photoSelection?.ids.has(img.id) ? "image-gallery-card image-gallery-card--selected" : "image-gallery-card"}
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
                    key={previewSrc}
                    src={previewSrc}
                    loading="lazy"
                    decoding="async"
                    onLoad={(event) => {
                      const { naturalWidth, naturalHeight } = event.currentTarget;
                      loadedPreviewsRef.current.add(previewUrl);
                      setPreviewStates((current) => ({
                        ...current,
                        [previewUrl]: { w: naturalWidth, h: naturalHeight },
                      }));
                      if (faceOverlayMode !== "none" && needsImageGeometry(img)) onImageGeometryNeeded(img);
                    }}
                    onError={() => {
                      loadedPreviewsRef.current.delete(previewUrl);
                      setPreviewStates((current) => ({ ...current, [previewUrl]: "error" }));
                    }}
                    style={{
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                      display: "block",
                    }}
                    alt={interactionsDisabled ? img.filename || "Foto" : ""}
                  />
                )}

                <button
                  type="button"
                  className="image-gallery-card__open"
                  aria-label={photoSelection ? `${img.filename || "Bild"} auswählen` : `${img.filename || "Bild"} in Galerie öffnen`}
                  aria-pressed={photoSelection?.ids.has(img.id)}
                  disabled={interactionsDisabled}
                  onClick={() => photoSelection ? photoSelection.onToggle(img.id) : setGalleryImageId(img.id)}
                >{photoSelection && <span className="image-gallery-card__selection" aria-hidden="true">
                  {photoSelection.ids.has(img.id) ? "✓" : "+"}
                </span>}</button>
                {preview === "error" && !photoSelection && (
                  <button
                    type="button"
                    className="secondary-button image-gallery-card__retry"
                    disabled={interactionsDisabled}
                    aria-label={`Vorschau von ${img.filename || "Bild"} erneut laden`}
                    onClick={(event) => {
                      event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(".image-gallery-card__open")?.focus();
                      setPreviewRetries((current) => ({
                        ...current,
                        [previewUrl]: Math.max(Date.now(), (current[previewUrl] ?? 0) + 1),
                      }));
                      setPreviewStates((current) => {
                        const next = { ...current };
                        delete next[previewUrl];
                        return next;
                      });
                    }}
                  >
                    Vorschau erneut laden
                  </button>
                )}

                {!photoSelection && <div className="image-file-actions">
                  <button
                    type="button"
                    disabled={interactionsDisabled}
                    title="Vollständigen Dateipfad kopieren"
                    aria-label={`Dateipfad von ${img.filename || "Bild"} kopieren`}
                    onClick={(event) => void copyImagePath(event, img)}
                    onKeyDown={(event) => event.stopPropagation()}
                  >
                    Pfad
                  </button>
                  <button
                    type="button"
                    disabled={interactionsDisabled}
                    title="Datei im Explorer oder Dateimanager anzeigen"
                    aria-label={`Speicherort von ${img.filename || "Bild"} öffnen`}
                    onClick={(event) => void revealImage(event, img)}
                    onKeyDown={(event) => event.stopPropagation()}
                  >
                    Ordner
                  </button>
                </div>}

                {img.location_count > 1 && (
                  <span
                    className="image-location-count"
                    title={img.locations.map((location) => location.path).join("\n")}
                  >
                    {img.location_count} Speicherorte
                  </span>
                )}

                {preview !== "error" && !photoSelection && <span className="image-open-cue" aria-hidden="true">
                  <span className="image-open-cue__icon">↗</span>
                  <span>Vollbild öffnen</span>
                </span>}

                {!photoSelection && faceOverlayMode !== "none" &&
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
                        disabled={interactionsDisabled}
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

      {isLoadingMore && <div className="image-grid-status" role="status">Weitere Bilder werden geladen …</div>}

      {galleryIndex >= 0 && !interactionsDisabled && (
        <FullscreenImageGallery
          images={images}
          activeIndex={galleryIndex}
          onNavigateToCluster={onNavigateToCluster}
          onChange={(index) => setGalleryImageId(images[index]?.id ?? null)}
          onClose={() => setGalleryImageId(null)}
          onDelete={(image) => removeImage(image as FaceImage)}
          onManualFaceSaved={onManualFaceSaved}
        />
      )}
    </>
  );
};

export default ImageGrid;
