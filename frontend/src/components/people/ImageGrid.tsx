import React, { useEffect, useRef, useState } from "react";
import Masonry from "react-masonry-css";
import FaceOverlay from "./FaceOverlay";
import FullscreenImageGallery from "./FullscreenImageGallery";
import { deleteImage, FaceImage, imageFileUrl } from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";

interface ImageGridProps {
  images: FaceImage[];
  isLoading: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  showFaceOverlays: boolean;
  onLoadMore: () => void;
  onImageDeleted: (imageId: number) => void;
}

const breakpointCols = {
  default: 4,
  1400: 3,
  900: 2,
  600: 1,
};

const COLUMN_PRELOAD_VIEWPORTS = 1.5;
const GRID_PRELOAD_VIEWPORTS = 2.5;

const ImageGrid: React.FC<ImageGridProps> = ({
  images,
  isLoading,
  hasMore,
  isLoadingMore,
  showFaceOverlays,
  onLoadMore,
  onImageDeleted,
}) => {
  const [galleryIndex, setGalleryIndex] = useState<number | null>(null);
  const [imageDimensions, setImageDimensions] = useState<
    Record<string, { w: number; h: number }>
  >({});
  const gridRef = useRef<HTMLDivElement | null>(null);
  const rafIdRef = useRef<number | null>(null);

  useEffect(() => {
    const scrollContainer = document.querySelector(".page-content");
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
  }, [hasMore, images.length, isLoading, isLoadingMore, onLoadMore]);

  useEffect(() => {
    if (isLoading) return;

    images.forEach((img) => {
      if (imageDimensions[img.image_path]) return;

      const imgSrc = imageFileUrl(img.id);
      const image = new Image();
      image.src = imgSrc;
      image.onload = () => {
        setImageDimensions((prev) => ({
          ...prev,
          [img.image_path]: { w: image.naturalWidth, h: image.naturalHeight },
        }));
      };
    });
  }, [images, imageDimensions, isLoading]);

  useEffect(() => {
    setGalleryIndex((current) => {
      if (current === null) return null;
      if (current >= images.length) return null;
      return current;
    });
  }, [images.length]);

  const removeImage = async (image: FaceImage) => {
    const filename = image.filename || pathBasename(image.image_path) || "Bild";
    const confirmed = window.confirm(
      `"${filename}" aus der Face-Manager-Datenbank entfernen?\n\nDie Originaldatei wird nicht gelöscht.`,
    );
    if (!confirmed) return false;

    try {
      await deleteImage(image.id);
      onImageDeleted(image.id);
      setGalleryIndex((current) => {
        if (current === null) return null;
        if (images.length <= 1) return null;
        return Math.min(current, images.length - 2);
      });
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
        <p>Wähle andere Ordner oder passe den Personenfilter an.</p>
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
            const dims = imageDimensions[img.image_path];
            const aspectRatio = dims ? `${dims.w} / ${dims.h}` : "1/1";

            return (
              <div
                key={img.id}
                className={`image-gallery-card${!dims ? " shimmer-placeholder" : ""}`}
                role="button"
                tabIndex={0}
                aria-label={`${img.filename || "Bild"} in Galerie öffnen`}
                onClick={() => setGalleryIndex(images.findIndex((item) => item.id === img.id))}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setGalleryIndex(images.findIndex((item) => item.id === img.id));
                  }
                }}
                style={{
                  position: "relative",
                  width: "100%",
                  overflow: "hidden",
                  borderRadius: 6,
                  background: "#101014",
                  border: "1px solid #222",
                  aspectRatio,
                  marginBottom: "16px",
                  transition: "aspect-ratio 0.2s ease",
                }}
              >
                <img
                  src={imageFileUrl(img.id)}
                  loading="lazy"
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    display: "block",
                    opacity: dims ? 1 : 0,
                    transition: "opacity 0.3s ease",
                  }}
                  alt=""
                />

                <button
                  type="button"
                  className="image-delete-button"
                  title="Bild aus der Datenbank entfernen"
                  aria-label={`${img.filename || "Bild"} aus der Datenbank entfernen`}
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

                {showFaceOverlays &&
                  dims &&
                  img.faces.map((face) => (
                    <FaceOverlay
                      key={face.id}
                      face={face}
                      naturalWidth={dims.w}
                      naturalHeight={dims.h}
                    />
                  ))}
              </div>
            );
          })}
        </Masonry>
      </div>

      {isLoadingMore && <div className="image-grid-status">Weitere Bilder werden geladen…</div>}

      {galleryIndex !== null && images[galleryIndex] && (
        <FullscreenImageGallery
          images={images}
          activeIndex={galleryIndex}
          onChange={setGalleryIndex}
          onClose={() => setGalleryIndex(null)}
          onDelete={(image) => removeImage(image as FaceImage)}
        />
      )}
    </>
  );
};

export default ImageGrid;
