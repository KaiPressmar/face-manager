interface LabelledFace {
  id: number;
  bbox_x: number;
  bbox_w: number;
  person_name: string | null;
}

/** Text shown on a face badge. Kept here so width estimation matches it. */
export function faceLabelText(face: { person_name: string | null }): string {
  return face.person_name || "Nicht zugewiesen";
}

/**
 * Rough rendered badge width as a share of the picture width.
 *
 * Badge width is in px (fixed font) while positions are percentages, so the
 * true share depends on how wide the picture happens to render. We assume a
 * typical card width — good enough to decide which badges would collide, which
 * is all the lane assignment needs.
 */
const ASSUMED_RENDER_WIDTH = 420;
const CHAR_WIDTH = 6.2;
const BADGE_CHROME = 34; // padding, dot and gap

function estimatedWidthPercent(face: LabelledFace): number {
  const px = faceLabelText(face).length * CHAR_WIDTH + BADGE_CHROME;
  return Math.min(62, (px / ASSUMED_RENDER_WIDTH) * 100);
}

/**
 * Spread badges over as few stacked lanes as possible so they do not cover each
 * other. Faces are processed left to right and each badge takes the topmost
 * lane that is still free at its horizontal position.
 *
 * Returns the lane index per face id; 0 is closest to the box.
 */
export function assignLabelLanes(
  faces: LabelledFace[],
  naturalWidth: number,
  maxLanes = 3,
): Map<number, number> {
  const lanes = new Map<number, number>();
  if (!naturalWidth) {
    faces.forEach((face) => lanes.set(face.id, 0));
    return lanes;
  }

  // Right edge already occupied per lane, in percent.
  const laneEnds: number[] = [];
  const ordered = [...faces].sort((a, b) => a.bbox_x - b.bbox_x);

  ordered.forEach((face) => {
    const startPercent = (face.bbox_x / naturalWidth) * 100;
    const widthPercent = estimatedWidthPercent(face);
    const endPercent = startPercent + widthPercent;

    let lane = laneEnds.findIndex((end) => end <= startPercent);
    if (lane === -1) {
      if (laneEnds.length < maxLanes) {
        lane = laneEnds.length;
        laneEnds.push(0);
      } else {
        // All lanes busy: use the one that frees up earliest, so badges stay
        // spread out instead of piling back onto the first lane.
        lane = laneEnds.reduce(
          (best, end, index) => (end < laneEnds[best] ? index : best),
          0,
        );
      }
    }
    // Never shrink a lane's occupancy — that would make it look free again.
    laneEnds[lane] = Math.max(laneEnds[lane], endPercent);
    lanes.set(face.id, lane);
  });

  return lanes;
}
