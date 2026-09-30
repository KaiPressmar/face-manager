import React from "react";
import { identityColor } from "../../utils/colors";
import { faceLabelText } from "../../utils/faceLabels";

interface Face {
  id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  cluster_id: number | null;
  person_name: string | null;
  review_status: "active" | "unknown_person" | "not_face";
}

interface FaceOverlayProps {
  face: Face;
  naturalWidth: number;
  naturalHeight: number;
  /** Position among the picture's faces, ordered left to right. Neighbouring
   *  faces sit at similar heights, so alternating lanes keeps their badges
   *  from stacking on top of each other. */
  stackIndex?: number;
  /** Emphasize the face whose crop currently owns the gallery position. */
  highlighted?: boolean;
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
}

const FaceOverlay: React.FC<FaceOverlayProps> = ({
  face,
  naturalWidth,
  naturalHeight,
  stackIndex = 0,
  highlighted = false,
  onNavigateToCluster,
}) => {
  if (!naturalWidth || !naturalHeight) return null;

  // Umrechnung der Pixelwerte aus dem Backend in CSS-Prozente
  const top = (face.bbox_y / naturalHeight) * 100;
  const left = (face.bbox_x / naturalWidth) * 100;
  const width = (face.bbox_w / naturalWidth) * 100;
  const height = (face.bbox_h / naturalHeight) * 100;
  const canNavigate = face.cluster_id !== null;
  // "Unbekannt" is the archived review status; a face that simply has no person
  // yet must not borrow that word — it reads as a different thing entirely.
  const personLabel = faceLabelText(face);
  // Same colour for box and badge, so it is obvious which label belongs to
  // which face. Keyed by person where known, so the colour also matches that
  // person's filter chip; otherwise faces of one cluster still share a colour.
  const colorKey =
    face.person_name ||
    (face.cluster_id !== null ? `cluster:${face.cluster_id}` : `face:${face.id}`);
  const faceColor = identityColor(colorKey);
  // The badge answers "who is this". The cluster number would roughly double
  // its width and is what makes badges collide in group photos, so it moves
  // into the tooltip and the accessible name instead.
  const labelText = personLabel;
  const detailText =
    face.cluster_id === null ? personLabel : `${personLabel} · Gesichtsgruppe ${face.cluster_id}`;

  // Anchor the badge on the side that keeps it inside the picture, and flip it
  // below the box when the face sits at the very top.
  const anchorRight = left + width / 2 > 50;
  const flipBelow = top < 12;
  const laneOffset = Math.max(0, stackIndex) * 21;
  // Gap between the box edge and the badge, i.e. the leader line's length.
  const leaderLength = (flipBelow ? 6 : 25) + laneOffset;

  const horizontalAnchor = anchorRight
    ? { right: `${Math.max(0, 100 - (left + width))}%` }
    : { left: `${Math.max(0, left)}%` };
  // Hard guarantee that the badge stays inside the picture: it may only use the
  // space between its anchor and the opposite edge.
  const availableWidth = anchorRight ? left + width : 100 - left;

  const labelPosition: React.CSSProperties = {
    ...horizontalAnchor,
    maxWidth: `${Math.max(18, Math.min(100, availableWidth))}%`,
    ...(flipBelow
      ? { top: `calc(${top + height}% + ${leaderLength}px)` }
      : { top: `calc(${top}% - ${leaderLength}px)` }),
  };

  const leaderPosition: React.CSSProperties = {
    ...horizontalAnchor,
    height: `${leaderLength}px`,
    ...(flipBelow
      ? { top: `${top + height}%` }
      : { top: `calc(${top}% - ${leaderLength}px)` }),
  };

  const handleNavigate = (event: React.MouseEvent | React.KeyboardEvent) => {
    event.stopPropagation();
    if (face.cluster_id === null) return;
    // Pass the person along so the review page can open the right work
    // area immediately instead of briefly showing the suggestions inbox.
    onNavigateToCluster(face.cluster_id, face.person_name);
  };

  return (
    <div
      className={`hologram-overlay${canNavigate ? " hologram-overlay--interactive" : ""}${highlighted ? " hologram-overlay--highlighted" : ""}`}
      style={{ "--face-color": faceColor } as React.CSSProperties}
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
        title={canNavigate ? `Gesichtsgruppe ${face.cluster_id} ansehen` : undefined}
        aria-label={
          canNavigate
            ? `${detailText} unter „Gesichter prüfen“ anzeigen`
            : `${detailText} ist keiner Gesichtsgruppe zugeordnet`
        }
      />

      <span className="hologram-leader" style={leaderPosition} aria-hidden="true" />

      <button
        type="button"
        className={`hologram-label${canNavigate ? " hologram-label--interactive" : ""}`}
        style={labelPosition}
        onClick={handleNavigate}
        onKeyDown={(event) => event.stopPropagation()}
        disabled={!canNavigate}
        title={
          canNavigate
            ? `${detailText} – unter „Gesichter prüfen“ öffnen`
            : `${detailText} – noch keiner Gesichtsgruppe zugeordnet`
        }
      >
        <span>{labelText}</span>
      </button>
    </div>
  );
};

export default FaceOverlay;
