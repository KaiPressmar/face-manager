import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { imageFileUrl, imageThumbnailUrl, openImageLocation } from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import FaceOverlay from "./FaceOverlay";
import { imageAspectRatiosMatch } from "../../utils/imageGeometry";
import { assignLabelLanes } from "../../utils/faceLabels";
import { copyTextToClipboard } from "../../utils/clipboard";
import "./FullscreenImageGallery.css";

interface GalleryFace {
  id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  cluster_id: number | null;
  person_name: string | null;
  review_status: "active" | "unknown_person" | "not_face";
}

interface GalleryLocation {
  path: string;
  filename: string;
  directory: string;
}

interface GalleryMediaState {
  url: string;
  originalUnavailable?: boolean;
  retryingOriginal?: boolean;
  originalRetry?: number;
  previewStatus?: "loaded" | "error";
  previewRetry?: number;
}

export interface GalleryImage {
  id: number;
  content_hash?: string | null;
  image_path: string;
  filename?: string;
  directory?: string;
  image_width?: number | null;
  image_height?: number | null;
  location_count: number;
  locations: GalleryLocation[];
  faces: GalleryFace[];
}

interface FullscreenImageGalleryProps {
  images: GalleryImage[];
  activeIndex: number;
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
  onChange: (index: number) => void;
  onClose: () => void;
  onDelete?: (image: GalleryImage) => Promise<boolean>;
  /** Optional external sequence, e.g. all face crops in one group. */
  sequence?: {
    activeIndex: number;
    length: number;
    onChange: (index: number) => void;
    highlightedFaceId?: number;
    itemLabel?: string;
    contextLabel?: string;
    groupLabel?: string;
    loading?: boolean;
    error?: string | null;
  };
}

async function imageBlobAsPng(source: Blob) {
  const bitmap = await createImageBitmap(source);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext("2d");
  if (!context) {
    bitmap.close();
    throw new Error("Canvas wird nicht unterstützt.");
  }
  context.drawImage(bitmap, 0, 0);
  bitmap.close();

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) =>
        blob
          ? resolve(blob)
          : reject(new Error("PNG-Konvertierung fehlgeschlagen.")),
      "image/png"
    );
  });
}

const FullscreenImageGallery: React.FC<FullscreenImageGalleryProps> = ({
  images,
  activeIndex,
  onNavigateToCluster,
  onChange,
  onClose,
  onDelete,
  sequence,
}) => {
  const [message, setMessage] = useState("");
  const [showFaces, setShowFaces] = useState(true);
  const [media, setMedia] = useState<GalleryMediaState | null>(null);
  const [measuredImage, setMeasuredImage] = useState<{ url: string; width: number; height: number } | null>(null);
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
  const [isDeleting, setIsDeleting] = useState(false);
  const stageRef = useRef<HTMLElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalFocus(dialogRef, onClose, !isDeleting);
  const image = images[activeIndex];
  const { image_width: imageWidth, image_height: imageHeight } = image;
  const originalUrl = imageFileUrl(image.id);
  const activeMedia = media?.url === originalUrl ? media : null;
  const originalUnavailable = activeMedia?.originalUnavailable ?? false;
  const originalSrc = imageFileUrl(image.id, activeMedia?.originalRetry);
  const previewSrc = imageThumbnailUrl(image.id, activeMedia?.previewRetry);
  const naturalSize = measuredImage?.url === originalUrl ? measuredImage : { width: 0, height: 0 };
  const updateMedia = (change: Omit<GalleryMediaState, "url">) => {
    setMedia((current) => ({
      ...(current?.url === originalUrl ? current : { url: originalUrl }),
      ...change,
    }));
  };
  const measureImage = (element: HTMLImageElement) => {
    setMeasuredImage({ url: originalUrl, width: element.naturalWidth, height: element.naturalHeight });
  };
  const navigationIndex = sequence?.activeIndex ?? activeIndex;
  const navigationLength = sequence?.length ?? images.length;

  const move = useCallback(
    (direction: number) => {
      if (isDeleting || navigationLength < 2 || sequence?.loading) return;
      const nextIndex =
        (navigationIndex + direction + navigationLength) % navigationLength;
      if (sequence) {
        sequence.onChange(nextIndex);
      } else {
        onChange(nextIndex);
      }
      setMessage("");
    },
    [isDeleting, navigationIndex, navigationLength, onChange, sequence]
  );

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    const updateStageSize = () => {
      const styles = window.getComputedStyle(stage);
      const horizontalPadding =
        parseFloat(styles.paddingLeft) + parseFloat(styles.paddingRight);
      const verticalPadding =
        parseFloat(styles.paddingTop) + parseFloat(styles.paddingBottom);
      setStageSize({
        width: Math.max(0, stage.clientWidth - horizontalPadding),
        height: Math.max(0, stage.clientHeight - verticalPadding),
      });
    };

    updateStageSize();
    const observer = new ResizeObserver(updateStageSize);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  const scale =
    naturalSize.width && naturalSize.height
      ? Math.min(
          stageSize.width / naturalSize.width,
          stageSize.height / naturalSize.height
        )
      : 0;
  const renderedSize = {
    width: naturalSize.width * scale,
    height: naturalSize.height * scale,
  };

  const copyImage = async () => {
    try {
      if (!navigator.clipboard || typeof ClipboardItem === "undefined") {
        throw new Error("Die Bild-Zwischenablage wird von diesem Browser nicht unterstützt.");
      }
      const response = await fetch(imageFileUrl(image.id));
      if (!response.ok) throw new Error("Das Bild konnte nicht geladen werden.");
      const png = await imageBlobAsPng(await response.blob());
      await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
      setMessage("Bild kopiert");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Kopieren fehlgeschlagen");
    }
  };

  const revealImage = async () => {
    try {
      await openImageLocation(image.id, image.image_path);
      setMessage("Dateispeicherort geöffnet");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Öffnen fehlgeschlagen");
    }
  };

  const copyImagePath = async (path = image.image_path) => {
    try {
      await copyTextToClipboard(path);
      setMessage("Dateipfad kopiert");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Dateipfad konnte nicht kopiert werden",
      );
    }
  };

  const removeCurrentImage = async () => {
    if (!onDelete || isDeleting) return;
    setIsDeleting(true);
    try {
      await onDelete(image);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      className="fullscreen-gallery"
      role="dialog"
      aria-modal="true"
      aria-label="Bildansicht"
      aria-busy={isDeleting}
      onKeyDown={(event) => {
        if (event.defaultPrevented || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
        event.preventDefault();
        move(event.key === "ArrowLeft" ? -1 : 1);
      }}
      onMouseDown={(event) => {
        if (!isDeleting && event.target === event.currentTarget) onClose();
      }}
    >
      <div className="gallery-ambient" aria-hidden="true" />

      <header className="gallery-header">
        <div className="gallery-title">
          <strong>{image.filename || pathBasename(image.image_path)}</strong>
          {sequence?.contextLabel && sequence?.groupLabel && (
            <div className="gallery-sequence-context">
              <b>{sequence.contextLabel}</b>
              <span>Gesichtsgruppe: {sequence.groupLabel}</span>
            </div>
          )}
          <span>
            {sequence
              ? `${sequence.itemLabel || "Eintrag"} ${navigationIndex + 1} von ${navigationLength}`
              : `${navigationIndex + 1} / ${navigationLength}`}
            {image.location_count > 1 && ` · ${image.location_count} Speicherorte`}
          </span>
        </div>
        <div className="gallery-actions">
          <button
            type="button"
            className={showFaces ? "gallery-action--active" : ""}
            onClick={() => setShowFaces((current) => !current)}
            aria-pressed={showFaces}
            title={
              showFaces
                ? "Gesichtsmarkierungen ausblenden"
                : "Gesichtsmarkierungen anzeigen"
            }
          >
            <span aria-hidden="true">⌗</span>
            Gesichter
          </button>
          <button type="button" onClick={copyImage} disabled={originalUnavailable} title="Bildinhalt in die Zwischenablage kopieren">
            <span aria-hidden="true">⧉</span>
            Bild kopieren
          </button>
          <button type="button" className={image.locations.length ? "gallery-action--in-locations" : undefined} onClick={() => void copyImagePath()} title="Vollständigen Dateipfad kopieren">
            <span aria-hidden="true">⌘</span>
            Pfad kopieren
          </button>
          <button type="button" className={image.locations.length ? "gallery-action--in-locations" : undefined} onClick={revealImage} title="Datei im Explorer oder Dateimanager anzeigen">
            <span aria-hidden="true">↗</span>
            Speicherort öffnen
          </button>
          {onDelete && !sequence && (
            <button
              type="button"
              className="gallery-delete-action"
              onClick={removeCurrentImage}
              disabled={isDeleting}
              title="Bild aus Face Manager entfernen"
            >
              <span aria-hidden="true">×</span>
              {isDeleting ? "Wird entfernt…" : "Entfernen"}
            </button>
          )}
          <button
            type="button"
            className="gallery-close"
            onClick={onClose}
            disabled={isDeleting}
            aria-label="Galerie schließen"
            autoFocus
          >
            ×
          </button>
        </div>
      </header>

      <main
        ref={stageRef}
        className="gallery-stage"
        onMouseDown={(event) => {
          if (!isDeleting && event.target === event.currentTarget) onClose();
        }}
      >
        {navigationLength > 1 && (
          <button
            type="button"
            className="gallery-nav gallery-nav--previous"
            onClick={() => move(-1)}
            disabled={isDeleting || sequence?.loading}
            aria-label={sequence?.itemLabel === "Foto" ? "Vorheriges Bild" : sequence ? "Vorheriges Gesicht der Gruppe" : "Vorheriges Bild"}
          >
            ‹
          </button>
        )}
        <div
          key={image.id}
          className="gallery-image-frame"
          style={{
            width: renderedSize.width || 1,
            height: renderedSize.height || 1,
            opacity: scale ? 1 : 0,
          }}
        >
          <img
            key={originalSrc}
            className="gallery-image"
            src={originalSrc}
            style={originalUnavailable ? { display: "none" } : undefined}
            alt={image.filename || ""}
            draggable={false}
            onLoad={(event) => {
              updateMedia({ originalUnavailable: false, retryingOriginal: false });
              measureImage(event.currentTarget);
            }}
            onError={() => updateMedia({ originalUnavailable: true, retryingOriginal: false })}
          />
          {originalUnavailable && activeMedia?.previewStatus !== "error" && (
            <img
              key={previewSrc}
              className="gallery-image gallery-image--preview"
              src={previewSrc}
              alt={`Vorschau von ${image.filename || "Foto"}`}
              draggable={false}
              onLoad={(event) => {
                updateMedia({ previewStatus: "loaded" });
                measureImage(event.currentTarget);
              }}
              onError={() => updateMedia({ previewStatus: "error" })}
            />
          )}
          {showFaces && !isDeleting && naturalSize.width > 0 &&
            imageWidth && imageHeight &&
            (image.content_hash || imageAspectRatiosMatch(imageWidth, imageHeight, naturalSize.width, naturalSize.height)) &&
            (() => {
              const visibleFaces = sequence?.highlightedFaceId
                ? image.faces.filter(
                    (face) => face.id === sequence.highlightedFaceId,
                  )
                : image.faces;
              const lanes = assignLabelLanes(visibleFaces, imageWidth);
              return visibleFaces.map((face) => (
                <FaceOverlay
                  key={face.id}
                  face={face}
                  naturalWidth={imageWidth}
                  naturalHeight={imageHeight}
                  stackIndex={lanes.get(face.id) ?? 0}
                  highlighted={face.id === sequence?.highlightedFaceId}
                  onNavigateToCluster={onNavigateToCluster}
                />
              ));
            })()}
        </div>
        {sequence?.loading && (
          <div className="gallery-sequence-loading" role="status">
            Nächstes Bild wird geladen…
          </div>
        )}
        {navigationLength > 1 && (
          <button
            type="button"
            className="gallery-nav gallery-nav--next"
            onClick={() => move(1)}
            disabled={isDeleting || sequence?.loading}
            aria-label={sequence?.itemLabel === "Foto" ? "Nächstes Bild" : sequence ? "Nächstes Gesicht der Gruppe" : "Nächstes Bild"}
          >
            ›
          </button>
        )}
      </main>

      <footer className={`gallery-footer${originalUnavailable ? " gallery-footer--source-error" : ""}`}>
        {originalUnavailable && (
          <div className="gallery-source-feedback">
            <p role={activeMedia?.retryingOriginal ? "status" : "alert"}>
              {activeMedia?.retryingOriginal ? "Original wird erneut geladen …" : "Die Bilddatei konnte nicht geöffnet werden."}
              {" "}
              {activeMedia?.previewStatus === "loaded"
                ? "Die lokale Vorschau wird angezeigt."
                : activeMedia?.previewStatus === "error" ? "Auch die Vorschau ist nicht verfügbar." : "Vorschau wird geladen …"}
            </p>
            <button
              type="button"
              className="secondary-button"
              aria-disabled={activeMedia?.retryingOriginal}
              onClick={() => {
                if (activeMedia?.retryingOriginal) return;
                updateMedia({
                  originalRetry: Math.max(Date.now(), (activeMedia?.originalRetry ?? 0) + 1),
                  retryingOriginal: true,
                });
              }}
            >Original erneut laden</button>
            {activeMedia?.previewStatus === "error" && (
              <button type="button" className="secondary-button" onClick={() => {
                updateMedia({ previewRetry: Math.max(Date.now(), (activeMedia?.previewRetry ?? 0) + 1), previewStatus: undefined });
              }}>Vorschau erneut laden</button>
            )}
          </div>
        )}
        <div className="gallery-locations">
          {image.locations.map((location) => (
            <div className="gallery-location-row" key={location.path}>
              <code title={location.path}>{location.path}</code>
              <div>
                <button
                  type="button"
                  title="Diesen Dateipfad kopieren"
                  onClick={() => void copyImagePath(location.path)}
                >
                  Pfad kopieren
                </button>
                <button
                  type="button"
                  title="Diese Datei im Explorer oder Dateimanager anzeigen"
                  onClick={async () => {
                    try {
                      await openImageLocation(image.id, location.path);
                      setMessage("Dateispeicherort geöffnet");
                    } catch (error) {
                      setMessage(
                        error instanceof Error ? error.message : "Öffnen fehlgeschlagen",
                      );
                    }
                  }}
                >
                  Speicherort öffnen
                </button>
              </div>
            </div>
          ))}
        </div>
        <small>
          ← → {sequence && sequence.itemLabel !== "Foto" ? "durch die Gruppe navigieren" : "navigieren"} · Esc schließen
        </small>
      </footer>

      {message && (
        <div className="gallery-toast" role="status">
          {message}
        </div>
      )}
      {sequence?.error && (
        <div className="gallery-toast gallery-toast--error" role="alert">
          {sequence.error}
        </div>
      )}
    </div>
  );
};

export default FullscreenImageGallery;
