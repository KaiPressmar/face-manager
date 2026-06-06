import React from "react";

interface Face {
  id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  cluster_id: number | null;
  person_name: string | null;
}

interface FaceOverlayProps {
  face: Face;
  naturalWidth: number;
  naturalHeight: number;
}

const FaceOverlay: React.FC<FaceOverlayProps> = ({
  face,
  naturalWidth,
  naturalHeight,
}) => {
  if (!naturalWidth || !naturalHeight) return null;

  // Umrechnung der Pixelwerte aus dem Backend in CSS-Prozente
  const top = (face.bbox_y / naturalHeight) * 100;
  const left = (face.bbox_x / naturalWidth) * 100;
  const width = (face.bbox_w / naturalWidth) * 100;
  const height = (face.bbox_h / naturalHeight) * 100;

  return (
    <>
      {/* Bounding Box mit deiner pulsierenden Hologramm-Klasse */}
      <div
        className="hologram-box"
        style={{
          top: `${top}%`,
          left: `${left}%`,
          width: `${width}%`,
          height: `${height}%`,
          pointerEvents: "none", // Erlaubt Rechtsklick/Interaktion mit dem Bild darunter
        }}
      />

      {/* Label mit deinem Farbverlauf-Style */}
      <div
        className="hologram-label"
        style={{
          top: `calc(${top}% - 22px)`, // Schwebt präzise über dem Gesicht
          left: `${left}%`,
          pointerEvents: "none",
        }}
      >
        {(face.person_name || "Unbekannt") +
          " · Cluster " +
          (face.cluster_id ?? "?")}
      </div>
    </>
  );
};

export default FaceOverlay;