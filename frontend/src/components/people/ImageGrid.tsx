import React, { useState, useEffect, useMemo } from "react";
import Masonry from "react-masonry-css";
import FaceOverlay from "./FaceOverlay";

interface Face {
  id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  cluster_id: number | null;
  person_name: string | null;
}

interface ImageData {
  id: number;
  image_path: string;
  faces: Face[];
}

interface ImageGridProps {
  images: ImageData[];
  selectedPersons: string[];
  isLoading: boolean; // Neues Interface-Property
}

const ImageGrid: React.FC<ImageGridProps> = ({ images, selectedPersons, isLoading }) => {
  // 1. Bilder filtern
  const filtered = useMemo(() => {
    if (!Array.isArray(images)) return [];
    if (selectedPersons.length === 0) return images;

    return images.filter((img) => {
      const personsInImage = img.faces.map((f) => f.person_name || "Unbekannt");
      return selectedPersons.every((p) => personsInImage.includes(p));
    });
  }, [images, selectedPersons]);

  // 2. Pagination
  const [visibleCount, setVisibleCount] = useState(40);
  const visibleImages = filtered.slice(0, visibleCount);

  const [imageDimensions, setImageDimensions] = useState<Record<string, { w: number; h: number }>>({});

  // 3. Infinite Scroll
  useEffect(() => {
    if (isLoading) return; // Kein Scroll-Listener während des Ladens nötig

    const scrollContainer = document.querySelector(".page-content");
    if (!scrollContainer) return;

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
      if (scrollHeight - scrollTop <= clientHeight + 1000) {
        setVisibleCount((prev) => {
          if (prev >= filtered.length) return prev;
          return prev + 20;
        });
      }
    };

    scrollContainer.addEventListener("scroll", handleScroll);
    return () => scrollContainer.removeEventListener("scroll", handleScroll);
  }, [filtered.length, isLoading]);

  // Bildabmessungen ermitteln
  useEffect(() => {
    if (isLoading) return;

    visibleImages.forEach((img) => {
      if (imageDimensions[img.image_path]) return;

      const imgSrc = `http://localhost:8000/api/images/${img.id}/file`;
      const i = new Image();
      i.src = imgSrc;
      i.onload = () => {
        setImageDimensions((prev) => ({
          ...prev,
          [img.image_path]: { w: i.naturalWidth, h: i.naturalHeight },
        }));
      };
    });
  }, [visibleImages, imageDimensions, isLoading]);

  useEffect(() => {
    setVisibleCount(40);
  }, [selectedPersons, images]);

  const breakpointCols = {
    default: 4,
    1400: 3,
    900: 2,
    600: 1,
  };

  // 🔥 SKELETON RENDERER: Wenn die API noch lädt, zeigen wir sofort animierte Dummys an
  if (isLoading) {
    // Künstliche Höhen für den Masonry-Effekt (abwechselnd Hoch- und Querformat)
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

  if (filtered.length === 0) {
    return (
      <div className="empty-image-state">
        <span className="folder-icon" aria-hidden="true" />
        <h3>Keine Bilder in dieser Auswahl</h3>
        <p>Wähle andere Ordner oder passe den Personenfilter an.</p>
      </div>
    );
  }

  // Normaler Renderer für echte Bilder
  return (
    <Masonry
      breakpointCols={breakpointCols}
      className="masonry-grid"
      columnClassName="masonry-column"
    >
      {visibleImages.map((img) => {
        const dims = imageDimensions[img.image_path];
        const aspectRatio = dims ? `${dims.w} / ${dims.h}` : "1/1";

        return (
          <div
            key={img.id}
            className={!dims ? "shimmer-placeholder" : ""}
            style={{
              position: "relative",
              width: "100%",
              overflow: "hidden",
              borderRadius: 6,
              background: "#101014",
              border: "1px solid #222",
              aspectRatio: aspectRatio, 
              marginBottom: "16px",
              transition: "aspect-ratio 0.2s ease",
            }}
          >
            <img
              src={`http://localhost:8000/api/images/${img.id}/file`}
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
  );
};

export default ImageGrid;
