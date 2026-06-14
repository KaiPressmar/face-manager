import React, { useEffect, useState } from "react";
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
  onLoadMore: () => void;
  onImageDeleted: (imageId: number) => void;
}

const breakpointCols = {
  default: 4,
  1400: 3,
  900: 2,
  600: 1,
};

const ImageGrid: React.FC<ImageGridProps> = ({
  images,
  isLoading,
  hasMore,
  isLoadingMore,
  onLoadMore,
  onImageDeleted,
}) => {
  const [galleryIndex, setGalleryIndex] = useState<number | null>(null);
  const [imageDimensions, setImageDimensions] = useState<
    Record<string, { w: number; h: number }>
  >({});
  const [loadMoreAnchor, setLoadMoreAnchor] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (isLoading || !hasMore || isLoadingMore || !loadMoreAnchor) return;

    const scrollContainer = document.querySelector(".page-content");
    if (!scrollContainer) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          onLoadMore();
        }
      },
      {
        root: scrollContainer,
        rootMargin: "1200px 0px 800px 0px",
      },
    );

    observer.observe(loadMoreAnchor);
    return () => observer.disconnect();
  }, [hasMore, isLoading, isLoadingMore, loadMoreAnchor, onLoadMore]);

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

              {dims &&
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

      {isLoadingMore && <div className="image-grid-status">Weitere Bilder werden geladen…</div>}
      {hasMore && <div ref={setLoadMoreAnchor} className="image-grid-anchor" aria-hidden="true" />}

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
