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
  onNavigateToCluster: (clusterId: number) => void;
}

const FaceOverlay: React.FC<FaceOverlayProps> = ({
  face,
  naturalWidth,
  naturalHeight,
  onNavigateToCluster,
}) => {
  if (!naturalWidth || !naturalHeight) return null;

  // Umrechnung der Pixelwerte aus dem Backend in CSS-Prozente
  const top = (face.bbox_y / naturalHeight) * 100;
  const left = (face.bbox_x / naturalWidth) * 100;
  const width = (face.bbox_w / naturalWidth) * 100;
  const height = (face.bbox_h / naturalHeight) * 100;
  const canNavigate = face.cluster_id !== null;
  const labelText =
    `${face.person_name || "Unbekannt"} · Cluster ${face.cluster_id ?? "?"}`;

  const handleNavigate = (event: React.MouseEvent | React.KeyboardEvent) => {
    event.stopPropagation();
    if (face.cluster_id === null) return;
    onNavigateToCluster(face.cluster_id);
  };

  return (
    <div
      className={`hologram-overlay${canNavigate ? " hologram-overlay--interactive" : ""}`}
    >
      <button
        type="button"
        className={`hologram-box${canNavigate ? " hologram-box--interactive" : ""}`}
        style={{
          top: `${top}%`,
          left: `${left}%`,
          width: `${width}%`,
          height: `${height}%`,
        }}
        onClick={handleNavigate}
        onKeyDown={(event) => event.stopPropagation()}
        disabled={!canNavigate}
        title={canNavigate ? `Cluster ${face.cluster_id} ansehen` : undefined}
        aria-label={
          canNavigate
            ? `${labelText} auf der Cluster-Seite anzeigen`
            : `${labelText} ist keinem Cluster zugeordnet`
        }
      />

      <button
        type="button"
        className={`hologram-label${canNavigate ? " hologram-label--interactive" : ""}`}
        style={{
          top: `calc(${top}% - 28px)`,
          left: `${left}%`,
        }}
        onClick={handleNavigate}
        onKeyDown={(event) => event.stopPropagation()}
        disabled={!canNavigate}
        title={
          canNavigate
            ? `Zu Cluster ${face.cluster_id} wechseln`
            : "Dieses Gesicht ist noch keinem Cluster zugeordnet"
        }
      >
        <span>{labelText}</span>
      </button>
    </div>
  );
};

export default FaceOverlay;
