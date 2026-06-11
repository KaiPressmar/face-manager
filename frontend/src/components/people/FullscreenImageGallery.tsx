import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { imageFileUrl, openImageLocation } from "../../utils/api";
import FaceOverlay from "./FaceOverlay";

interface GalleryFace {
  id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  cluster_id: number | null;
  person_name: string | null;
}

export interface GalleryImage {
  id: number;
  image_path: string;
  filename?: string;
  directory?: string;
  location_count: number;
  faces: GalleryFace[];
}

interface FullscreenImageGalleryProps {
  images: GalleryImage[];
  activeIndex: number;
  onChange: (index: number) => void;
  onClose: () => void;
  onDelete: (image: GalleryImage) => Promise<boolean>;
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
  onChange,
  onClose,
  onDelete,
}) => {
  const [message, setMessage] = useState("");
  const [showFaces, setShowFaces] = useState(true);
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
  const [isDeleting, setIsDeleting] = useState(false);
  const stageRef = useRef<HTMLElement>(null);
  const image = images[activeIndex];

  const move = useCallback(
    (direction: number) => {
      if (images.length < 2) return;
      onChange((activeIndex + direction + images.length) % images.length);
      setMessage("");
    },
    [activeIndex, images.length, onChange]
  );

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft") move(-1);
      if (event.key === "ArrowRight") move(1);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [move, onClose]);

  useEffect(() => {
    const next = images[(activeIndex + 1) % images.length];
    const previous = images[(activeIndex - 1 + images.length) % images.length];
    [next, previous].forEach((item) => {
      if (item && item.id !== image.id) {
        const preload = new Image();
        preload.src = imageFileUrl(item.id);
      }
    });
  }, [activeIndex, image.id, images]);

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

  useEffect(() => {
    setNaturalSize({ width: 0, height: 0 });
  }, [image.id]);

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

  const removeCurrentImage = async () => {
    setIsDeleting(true);
    await onDelete(image);
    setIsDeleting(false);
  };

  return (
    <div
      className="fullscreen-gallery"
      role="dialog"
      aria-modal="true"
      aria-label="Bildansicht"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="gallery-ambient" aria-hidden="true" />

      <header className="gallery-header">
        <div className="gallery-title">
          <strong>{image.filename || image.image_path.split("/").pop()}</strong>
          <span>
            {activeIndex + 1} / {images.length}
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
                ? "Gesichtserkennung ausblenden"
                : "Gesichtserkennung anzeigen"
            }
          >
            <span aria-hidden="true">⌗</span>
            Gesichter
          </button>
          <button type="button" onClick={copyImage} title="Bild in die Zwischenablage kopieren">
            <span aria-hidden="true">⧉</span>
            Kopieren
          </button>
          <button type="button" onClick={revealImage} title="Datei im System anzeigen">
            <span aria-hidden="true">↗</span>
            Speicherort
          </button>
          <button
            type="button"
            className="gallery-delete-action"
            onClick={removeCurrentImage}
            disabled={isDeleting}
            title="Bild aus der Datenbank entfernen"
          >
            <span aria-hidden="true">×</span>
            {isDeleting ? "Entferne..." : "Entfernen"}
          </button>
          <button
            type="button"
            className="gallery-close"
            onClick={onClose}
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
          if (event.target === event.currentTarget) onClose();
        }}
      >
        {images.length > 1 && (
          <button
            type="button"
            className="gallery-nav gallery-nav--previous"
            onClick={() => move(-1)}
            aria-label="Vorheriges Bild"
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
            key={image.id}
            className="gallery-image"
            src={imageFileUrl(image.id)}
            alt={image.filename || ""}
            draggable={false}
            onLoad={(event) =>
              setNaturalSize({
                width: event.currentTarget.naturalWidth,
                height: event.currentTarget.naturalHeight,
              })
            }
          />
          {showFaces &&
            naturalSize.width > 0 &&
            image.faces.map((face) => (
              <FaceOverlay
                key={face.id}
                face={face}
                naturalWidth={naturalSize.width}
                naturalHeight={naturalSize.height}
              />
            ))}
        </div>
        {images.length > 1 && (
          <button
            type="button"
            className="gallery-nav gallery-nav--next"
            onClick={() => move(1)}
            aria-label="Nächstes Bild"
          >
            ›
          </button>
        )}
      </main>

      <footer className="gallery-footer">
        <span title={image.image_path}>{image.image_path}</span>
        <small>← → navigieren · Esc schließen</small>
      </footer>

      {message && (
        <div className="gallery-toast" role="status">
          {message}
        </div>
      )}
    </div>
  );
};

export default FullscreenImageGallery;
