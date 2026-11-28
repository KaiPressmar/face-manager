import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
  active: boolean;
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
const WINDOWED_GRID_THRESHOLD = 240;
const CARD_GAP = 16;

type CachedPreview = { preview?: PreviewState; retry?: number };

function galleryDimensions(image: FaceImage, preview?: PreviewState) {
  const previewSize = preview === "error" ? undefined : preview;
  const imageSize = image.image_width && image.image_height ? { w: image.image_width, h: image.image_height } : undefined;
  return imageSize && (!previewSize || image.content_hash ||
    imageAspectRatiosMatch(imageSize.w, imageSize.h, previewSize.w, previewSize.h)) ? imageSize : previewSize;
}

interface PositionedPhoto {
  image: FaceImage;
  index: number;
  column: number;
  top: number;
  bottom: number;
}

function positionPhotos(images: FaceImage[], width: number, columnCount: number, previews: Map<string, CachedPreview>) {
  const columnWidth = Math.max(1, (width - CARD_GAP * (columnCount - 1)) / columnCount);
  const columns: PositionedPhoto[][] = Array.from({ length: columnCount }, () => []);
  const ends = columns.map(() => 0);
  const byId = new Map<number, PositionedPhoto>();
  images.forEach((image, index) => {
    const column = index % columnCount;
    const top = ends[column];
    const size = galleryDimensions(image, previews.get(imageThumbnailUrl(image.id))?.preview);
    const bottom = top + Math.max(2, size ? columnWidth * size.h / size.w : columnWidth);
    const photo = { image, index, column, top, bottom };
    columns[column].push(photo);
    byId.set(image.id, photo);
    ends[column] = bottom + CARD_GAP;
  });
  return { columns, byId, columnWidth, height: Math.max(...ends), shortestColumn: Math.min(...ends) };
}

function photosInWindow(columns: PositionedPhoto[][], start: number, end: number) {
  return columns.flatMap((column) => {
    let first = 0;
    let remaining = column.length;
    while (first < remaining) {
      const middle = Math.floor((first + remaining) / 2);
      if (column[middle].bottom < start) first = middle + 1;
      else remaining = middle;
    }
    const visible: PositionedPhoto[] = [];
    for (let index = first; index < column.length && column[index].top <= end; index += 1) {
      visible.push(column[index]);
    }
    return visible;
  });
}

const ImageCard = React.memo(function ImageCard({
  img,
  previewUrl,
  active,
  interactionsDisabled,
  faceOverlayMode,
  onNavigateToCluster,
  onImageGeometryNeeded,
  onOpen,
  onTogglePhoto,
  selected,
  copyImagePath,
  revealImage,
  previews,
  onPreviewRatioChanged,
}: {
  img: FaceImage;
  previewUrl: string;
  active: boolean;
  interactionsDisabled: boolean;
  faceOverlayMode: FaceOverlayMode;
  onNavigateToCluster: NavigateToFaceGroup;
  onImageGeometryNeeded: (image: FaceImage) => void;
  onOpen: (imageId: number) => void;
  onTogglePhoto?: (imageId: number) => void;
  selected: boolean;
  copyImagePath: (event: React.MouseEvent<HTMLButtonElement>, image: FaceImage) => Promise<void>;
  revealImage: (event: React.MouseEvent<HTMLButtonElement>, image: FaceImage) => Promise<void>;
  previews: Map<string, CachedPreview>;
  onPreviewRatioChanged?: () => void;
}) {
  // Masonry moves cards between columns on resize. Retain their preview state
  // across those remounts, but render thumbnail load/retry updates in each card.
  const [{ preview, retry }, setCachedPreview] = useState<CachedPreview>(() => previews.get(previewUrl) ?? {});
  const previewSrc = imageThumbnailUrl(img.id, retry);
  const updatePreview = (next: CachedPreview) => {
    previews.set(previewUrl, next);
    const oldSize = galleryDimensions(img, preview);
    const newSize = galleryDimensions(img, next.preview);
    if ((oldSize?.h ?? 1) / (oldSize?.w ?? 1) !== (newSize?.h ?? 1) / (newSize?.w ?? 1)) onPreviewRatioChanged?.();
    setCachedPreview(next);
  };

  useEffect(() => {
    if (active && faceOverlayMode !== "none" && preview && preview !== "error" && needsImageGeometry(img)) {
      onImageGeometryNeeded(img);
    }
  }, [active, faceOverlayMode, img, onImageGeometryNeeded, preview]);

  const previewSize = preview === "error" ? null : preview;
  const imageSize = img.image_width && img.image_height ? { w: img.image_width, h: img.image_height } : null;
  const size = galleryDimensions(img, preview);
  const sourceSize = imageSize && size?.w === imageSize.w && size?.h === imageSize.h ? imageSize : null;
  const aspectRatio = size ? `${size.w} / ${size.h}` : "1/1";

  return (
    <div
      data-image-id={img.id}
      className={selected ? "image-gallery-card image-gallery-card--selected" : "image-gallery-card"}
      style={{
        position: "relative",
        width: "100%",
        overflow: "hidden",
        borderRadius: 6,
        background: "var(--surface-1)",
        border: "1px solid var(--border)",
        aspectRatio,
        marginBottom: "16px",
        transition: onPreviewRatioChanged ? undefined : "aspect-ratio 0.2s ease",
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
            if (preview?.w === naturalWidth && preview.h === naturalHeight) return;
            updatePreview({ retry, preview: { w: naturalWidth, h: naturalHeight } });
          }}
          onError={() => {
            updatePreview({ retry, preview: "error" });
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
        aria-label={onTogglePhoto ? `${img.filename || "Bild"} auswählen` : `${img.filename || "Bild"} in Galerie öffnen`}
        aria-pressed={onTogglePhoto ? selected : undefined}
        disabled={interactionsDisabled}
        onClick={() => onTogglePhoto ? onTogglePhoto(img.id) : onOpen(img.id)}
      >{onTogglePhoto && <span className="image-gallery-card__selection" aria-hidden="true">
        {selected ? "✓" : "+"}
      </span>}</button>
      {preview === "error" && !onTogglePhoto && (
        <button
          type="button"
          className="secondary-button image-gallery-card__retry"
          disabled={interactionsDisabled}
          aria-label={`Vorschau von ${img.filename || "Bild"} erneut laden`}
          onClick={(event) => {
            event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(".image-gallery-card__open")?.focus();
            updatePreview({ retry: Math.max(Date.now(), (retry ?? 0) + 1) });
          }}
        >
          Vorschau erneut laden
        </button>
      )}

      {!onTogglePhoto && <div className="image-file-actions">
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

      {preview !== "error" && !onTogglePhoto && <span className="image-open-cue" aria-hidden="true">
        <span className="image-open-cue__icon">↗</span>
        <span>Vollbild öffnen</span>
      </span>}

      {!onTogglePhoto && faceOverlayMode !== "none" &&
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
});

const ImageGrid: React.FC<ImageGridProps> = ({
  active,
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
  const galleryOpenerId = useRef<number | null>(null);
  const [focusedPhotoId, setFocusedPhotoId] = useState<number | null>(null);
  const galleryIndex = galleryImageId === null ? -1 : images.findIndex((image) => image.id === galleryImageId);
  const currentImagesRef = useRef(images);
  const [fileActionMessage, setFileActionMessage] = useState<string | null>(null);
  const fileActionTimerRef = useRef<number | null>(null);
  const previews = useRef(new Map<string, CachedPreview>());
  const onLoadMoreRef = useRef(onLoadMore);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const breakpointCols = BREAKPOINT_COLUMNS[gridSize];
  const windowed = images.length > WINDOWED_GRID_THRESHOLD;
  const previousWindowing = useRef(windowed);
  const [viewport, setViewport] = useState({ width: 0, top: 0, height: 0, columns: 1 });
  const [previewRevision, updatePreviewRevision] = useState(0);
  const onPreviewRatioChanged = useCallback(() => updatePreviewRevision((value) => value + 1), []);
  const positioned = useMemo(() => windowed && viewport.width > 0
    ? positionPhotos(images, viewport.width, viewport.columns, previews.current) : null,
  [images, previewRevision, viewport.columns, viewport.width, windowed]);
  const windowPhotos = useMemo(() => {
    if (!positioned) return [];
    const overscan = Math.max(650, viewport.height);
    const visible = photosInWindow(positioned.columns, viewport.top - overscan, viewport.top + viewport.height + overscan);
    const pinned = new Set([focusedPhotoId, galleryImageId === null ? null : galleryOpenerId.current]);
    pinned.forEach((id) => {
      const photo = id === null ? undefined : positioned.byId.get(id);
      if (photo && !visible.includes(photo)) visible.push(photo);
    });
    return visible.sort((left, right) => left.index - right.index);
  }, [focusedPhotoId, galleryImageId, positioned, viewport.height, viewport.top]);
  const openGallery = useCallback((id: number) => {
    galleryOpenerId.current = id;
    setGalleryImageId(id);
  }, []);

  useEffect(
    () => () => {
      if (fileActionTimerRef.current !== null) {
        window.clearTimeout(fileActionTimerRef.current);
      }
    },
    [],
  );

  useLayoutEffect(() => {
    currentImagesRef.current = images;
    onLoadMoreRef.current = onLoadMore;
    if (galleryImageId !== null && (galleryIndex < 0 || interactionsDisabled)) setGalleryImageId(null);
  }, [images, galleryIndex, galleryImageId, interactionsDisabled, onLoadMore]);

  useLayoutEffect(() => {
    if (windowed === previousWindowing.current || (windowed && !positioned)) return;
    previousWindowing.current = windowed;
    if (active && focusedPhotoId !== null && document.activeElement === document.body) {
      gridRef.current?.querySelector<HTMLButtonElement>(
        `[data-image-id="${focusedPhotoId}"] .image-gallery-card__open`,
      )?.focus({ preventScroll: true });
    }
  }, [active, focusedPhotoId, positioned, windowed]);

  useLayoutEffect(() => {
    if (!active) return;
    const grid = gridRef.current;
    const scrollContainer = grid?.closest<HTMLElement>(".page-content");
    if (!grid || !scrollContainer) return;
    let frame = 0;

    const check = () => {
      frame = 0;
      const containerRect = scrollContainer.getBoundingClientRect();
      const viewportHeight = scrollContainer.clientHeight;
      const gridRect = grid.getBoundingClientRect();
      const top = containerRect.top - gridRect.top;
      if (windowed) {
        const breakpoint = Object.keys(breakpointCols).map(Number)
          .filter((width) => window.innerWidth <= width).sort((left, right) => left - right)[0];
        const columns = breakpointCols[breakpoint] ?? breakpointCols.default;
        const width = grid.clientWidth;
        setViewport((current) => current.width === width && current.top === top &&
          current.height === viewportHeight && current.columns === columns ? current
          : { width, top, height: viewportHeight, columns });
      }

      if (isLoading || isLoadingMore || !hasMore || (windowed && !positioned)) return;
      const gridRemaining = gridRect.bottom - containerRect.bottom;
      const shortestColumnRemaining = positioned
        ? positioned.shortestColumn - top - viewportHeight
        : Math.min(...Array.from(grid.querySelectorAll(".masonry-column"), (column) =>
          column.lastElementChild?.getBoundingClientRect().bottom ?? Number.POSITIVE_INFINITY)) - containerRect.bottom;
      if (shortestColumnRemaining <= viewportHeight * COLUMN_PRELOAD_VIEWPORTS ||
        gridRemaining <= viewportHeight * GRID_PRELOAD_VIEWPORTS) {
        onLoadMoreRef.current();
      }
    };

    const scheduleCheck = () => { if (!frame) frame = window.requestAnimationFrame(check); };
    check();
    scrollContainer.addEventListener("scroll", scheduleCheck, { passive: true });
    window.addEventListener("resize", scheduleCheck);

    let resizeObserver: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(scheduleCheck);
      resizeObserver.observe(grid);
      resizeObserver.observe(scrollContainer);
    }

    return () => {
      scrollContainer.removeEventListener("scroll", scheduleCheck);
      window.removeEventListener("resize", scheduleCheck);
      resizeObserver?.disconnect();
      window.cancelAnimationFrame(frame);
    };
  }, [active, breakpointCols, hasMore, images.length, isLoading, isLoadingMore, positioned, windowed]);

  useEffect(() => {
    const image = images[galleryIndex];
    if (active && image && needsImageGeometry(image)) onImageGeometryNeeded(image);
  }, [active, galleryIndex, images, onImageGeometryNeeded]);

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

  const showFileActionMessage = useCallback((message: string) => {
    setFileActionMessage(message);
    if (fileActionTimerRef.current !== null) {
      window.clearTimeout(fileActionTimerRef.current);
    }
    fileActionTimerRef.current = window.setTimeout(
      () => setFileActionMessage(null),
      2400,
    );
  }, []);

  const copyImagePath = useCallback(async (
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
  }, [showFileActionMessage]);

  const revealImage = useCallback(async (
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
  }, [showFileActionMessage]);

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

  const renderCard = (img: FaceImage) => {
    const previewUrl = imageThumbnailUrl(img.id);
    return <ImageCard
      key={previewUrl}
      img={img}
      previewUrl={previewUrl}
      active={active}
      interactionsDisabled={interactionsDisabled}
      faceOverlayMode={faceOverlayMode}
      onNavigateToCluster={onNavigateToCluster}
      onImageGeometryNeeded={onImageGeometryNeeded}
      onOpen={openGallery}
      onTogglePhoto={photoSelection?.onToggle}
      selected={photoSelection?.ids.has(img.id) ?? false}
      copyImagePath={copyImagePath}
      revealImage={revealImage}
      previews={previews.current}
      onPreviewRatioChanged={windowed ? onPreviewRatioChanged : undefined}
    />;
  };

  return (
    <>
      <div ref={gridRef} aria-busy={isUpdating || isLoadingMore}
        onFocusCapture={(event) => {
          const photo = (event.target as HTMLElement).closest<HTMLElement>("[data-image-id]");
          if (photo) setFocusedPhotoId(Number(photo.dataset.imageId));
        }}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setFocusedPhotoId(null);
        }}>
        {windowed ? <div className="image-window" style={{ height: positioned?.height }}>
          {positioned && windowPhotos.map(({ image, column, top }) => (
            <div className="image-window__card" key={image.id} style={{
              top, left: column * (positioned.columnWidth + CARD_GAP), width: positioned.columnWidth,
            }}>
              {renderCard(image)}
            </div>
          ))}
        </div> : <Masonry
          breakpointCols={breakpointCols}
          className="masonry-grid"
          columnClassName="masonry-column"
        >
          {images.map(renderCard)}
        </Masonry>}
      </div>

      {fileActionMessage && (
        <div className="image-grid-file-toast" role="status">
          {fileActionMessage}
        </div>
      )}

      {hasMore && <div className="image-grid-status">
        <button type="button" className="secondary-button" aria-disabled={isLoadingMore || interactionsDisabled}
          onClick={() => { if (!isLoadingMore && !interactionsDisabled) onLoadMore(); }}>
          {isLoadingMore ? "Weitere Bilder werden geladen …" : "Weitere Bilder laden"}
        </button>
      </div>}

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
