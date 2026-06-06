import React from "react";

interface ClusterFacesGridProps {
  faces: any[];
  onRemoveFace: (id: number) => void;
}

const ClusterFacesGrid: React.FC<ClusterFacesGridProps> = ({ faces, onRemoveFace }) => {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, 120px)", gap: 16 }}>
      {faces.map((f) => {
        const cropUrl = `http://localhost:8000/api/face-crop?path=${encodeURIComponent(
          f.image_path
        )}&x=${f.bbox_x}&y=${f.bbox_y}&w=${f.bbox_w}&h=${f.bbox_h}`;

        return (
          <div
            key={f.id}
            className="face-grid-tile" // 🔥 Klasse für CSS-Hover-Effekte
            style={{
              position: "relative",
              width: 120,
              height: 120,
              borderRadius: 6,
              overflow: "hidden",
              background: "#101014",
              border: "1px solid #222",
            }}
          >
            <img
              src={cropUrl}
              loading="lazy"
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
              }}
              alt=""
            />
            
            {/* 🔥 VÖLLIG ÜBERARBEITETER X-BUTTON (Extrem sichtbar & Stylisch) */}
            <button
              onClick={() => onRemoveFace(f.id)}
              className="remove-face-btn"
              title="Gesicht aus Cluster entfernen"
              style={{
                position: "absolute",
                top: 6,
                right: 6,
                width: 24,
                height: 24,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                borderRadius: "50%", // Macht den Button kreisrund
                border: "1px solid #ff0055",
                background: "rgba(20, 4, 10, 0.85)", // Dunkler, semi-transparenter Grundkontrast
                color: "#ff3377",
                cursor: "pointer",
                fontWeight: "bold",
                fontSize: 12,
                boxShadow: "0 0 8px rgba(255, 0, 85, 0.4)",
                transition: "all 0.15s ease-in-out",
                zIndex: 10
              }}
            >
              ✕
            </button>
          </div>
        );
      })}
    </div>
  );
};

export default ClusterFacesGrid;