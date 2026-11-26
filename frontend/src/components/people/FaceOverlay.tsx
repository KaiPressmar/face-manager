import React from "react";
import { identityColor } from "../../utils/colors";
import { faceLabelText } from "../../utils/faceLabels";
import type { FaceReviewGroupKey } from "../../utils/api";

export type NavigateToFaceGroup = (clusterId: number, personName?: string | null, groupKey?: FaceReviewGroupKey) => void;

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
  disabled?: boolean;
  onNavigateToCluster: NavigateToFaceGroup;
}

const FaceOverlay: React.FC<FaceOverlayProps> = ({
  face,
  naturalWidth,
  naturalHeight,
  stackIndex = 0,
  highlighted = false,
  disabled = false,
  onNavigateToCluster,
}) => {
  if (!naturalWidth || !naturalHeight) return null;

  // Umrechnung der Pixelwerte aus dem Backend in CSS-Prozente
  const top = (face.bbox_y / naturalHeight) * 100;
  const left = (face.bbox_x / naturalWidth) * 100;
  const width = (face.bbox_w / naturalWidth) * 100;
  const height = (face.bbox_h / naturalHeight) * 100;
  const archive = face.review_status === "active" ? undefined : face.review_status;
  const canNavigate = face.cluster_id !== null && !disabled;
  // "Unbekannt" is the archived review status; a face that simply has no person
  // yet must not borrow that word — it reads as a different thing entirely.
  const personLabel = faceLabelText(face);
  // Same colour for box and badge, so it is obvious which label belongs to
  // which face. Keyed by person where known, so the colour also matches that
  // person's filter chip; otherwise faces of one cluster still share a colour.
  const colorKey =
    (!archive && face.person_name) ||
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
  const badgeTop = flipBelow
    ? `calc(${top + height}% + ${leaderLength}px)`
    : `calc(${top}% - ${leaderLength}px)`;

  const labelPosition: React.CSSProperties = {
    ...horizontalAnchor,
    maxWidth: `${Math.max(18, Math.min(100, availableWidth))}%`,
    top: `clamp(0px, ${badgeTop}, max(0px, calc(100% - 24px)))`,
  };

  const leaderPosition: React.CSSProperties = {
    ...horizontalAnchor,
    height: `${leaderLength}px`,
    ...(flipBelow
      ? { top: `${top + height}%` }
      : { top: `calc(${top}% - ${leaderLength}px)` }),
  };

  const handleNavigate = (event: React.MouseEvent) => {
    event.stopPropagation();
    if (face.cluster_id === null || disabled) return;
    // Pass the person along so the review page can open the right work
    // area immediately instead of briefly showing the suggestions inbox.
    onNavigateToCluster(face.cluster_id, face.person_name, archive);
  };

  return (
    <div
      data-face-id={face.id}
      className={`hologram-overlay${canNavigate ? " hologram-overlay--interactive" : ""}${highlighted ? " hologram-overlay--highlighted" : ""}`}
      style={{ "--face-color": faceColor } as React.CSSProperties}
    >
      <span
        className={`hologram-box${canNavigate ? " hologram-box--interactive" : ""}`}
        style={{
          top: `${top}%`,
          left: `${left}%`,
          width: `${width}%`,
          height: `${height}%`,
        }}
        onClick={canNavigate ? handleNavigate : undefined}
        title={canNavigate ? archive ? "Im Archiv öffnen" : `Gesichtsgruppe ${face.cluster_id} ansehen` : undefined}
        aria-hidden="true"
      />

      <span className="hologram-leader" style={leaderPosition} aria-hidden="true" />

      <button
        type="button"
        className={`hologram-label${canNavigate ? " hologram-label--interactive" : ""}`}
        style={labelPosition}
        onClick={handleNavigate}
        disabled={!canNavigate}
        aria-label={canNavigate ? `${labelText} ${archive ? "im Archiv" : "unter „Personen“"} öffnen` : labelText}
        title={
          canNavigate
            ? `${detailText} – ${archive ? "im Archiv" : "unter „Personen“"} öffnen`
            : detailText
        }
      >
        <span>{labelText}</span>
      </button>
    </div>
  );
};

export default FaceOverlay;
