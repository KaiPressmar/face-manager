import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { ClusterFace, faceCropUrl } from "../../utils/api";

interface ClusterFacesGridProps {
  faces: ClusterFace[];
  selectedFaceIds: number[];
  onToggleFace: (faceId: number) => void;
  /** Open the full picture a crop was cut from, to judge it in context. */
  onOpenImage?: (face: ClusterFace, groupFaces: ClusterFace[]) => void;
  /** When false, render skeleton cells reserving `reservedCount` rows. */
  loaded?: boolean;
  /** Item count used to reserve height while faces are not yet loaded. */
  reservedCount?: number;
}

const TILE_SIZE = 120;
const GAP = 16;
const ROW_HEIGHT = TILE_SIZE + GAP;
const OVERSCAN_PX = 400;

function columnsForWidth(width: number): number {
  return Math.max(1, Math.floor((width + GAP) / (TILE_SIZE + GAP)));
}

const FaceTile: React.FC<{
  face: ClusterFace;
  isSelected: boolean;
  onToggle: (faceId: number) => void;
  onOpenImage?: (face: ClusterFace) => void;
}> = ({ face, isSelected, onToggle, onOpenImage }) => (
  <label
    className={isSelected ? "face-grid-tile face-grid-tile--selected" : "face-grid-tile"}
    style={{
      width: TILE_SIZE,
      height: TILE_SIZE,
    }}
  >
    <img
      src={faceCropUrl(face.id)}
      loading="lazy"
      className="face-grid-tile__image"
      style={{ opacity: isSelected ? 0.9 : 1 }}
      alt=""
    />

    <span className="face-grid-tile__overlay" />
    <span className="face-grid-tile__id-badge">{`#${face.id}`}</span>

    {onOpenImage && (
      <button
        type="button"
        className="face-grid-tile__expand"
        title="Ganzes Bild ansehen"
        aria-label={`Ganzes Bild zu Gesicht ${face.id} ansehen`}
        onClick={(event) => {
          // The tile is a label around a checkbox, so keep the click from
          // toggling the selection as well.
          event.preventDefault();
          event.stopPropagation();
          onOpenImage(face);
        }}
      >
        ⤢
      </button>
    )}

    <span className="face-grid-tile__selector">
      <input
        type="checkbox"
        checked={isSelected}
        onChange={() => onToggle(face.id)}
        className="face-grid-tile__checkbox"
        aria-label={`Gesicht ${face.id} auswählen`}
      />
    </span>
  </label>
);

const ClusterFacesGrid: React.FC<ClusterFacesGridProps> = ({
  faces,
  selectedFaceIds,
  onToggleFace,
  onOpenImage,
  loaded = true,
  reservedCount,
}) => {
  const safeFaces = Array.isArray(faces) ? faces : [];
  const selectedSet = new Set(selectedFaceIds);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [columns, setColumns] = useState(1);
  const [range, setRange] = useState<{ start: number; end: number }>({
    start: 0,
    end: 0,
  });

  const itemCount = loaded
    ? safeFaces.length
    : Math.max(reservedCount ?? safeFaces.length, 0);
  const totalRows = Math.ceil(itemCount / columns);
  const totalHeight = totalRows * ROW_HEIGHT;
  const arrangedFaces = useMemo(() => {
    if (columns <= 1) return safeFaces;
    const arranged: ClusterFace[] = [];
    for (let start = 0, row = 0; start < safeFaces.length; start += columns, row += 1) {
      const rowFaces = safeFaces.slice(start, start + columns);
      arranged.push(...(row % 2 === 0 ? rowFaces : rowFaces.reverse()));
    }
    return arranged;
  }, [safeFaces, columns]);

  // Track how many columns fit so tile slicing matches the visual wrapping.
  useLayoutEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const measure = () => {
      const next = columnsForWidth(element.clientWidth);
      setColumns((prev) => (prev !== next ? next : prev));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Window the rendered rows to what is near the viewport of the page scroller.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const scroller = element.closest("[data-cluster-scroll-container='true']") as HTMLElement | null;

    const recompute = () => {
      const container = containerRef.current;
      if (!container) return;
      const containerRect = container.getBoundingClientRect();
      const viewportHeight = scroller ? scroller.clientHeight : window.innerHeight;
      const scrollerTop = scroller ? scroller.getBoundingClientRect().top : 0;
      // How far the grid's top sits above the viewport top (positive = scrolled past).
      const scrolledPast = scrollerTop - containerRect.top;

      let startRow = Math.floor((scrolledPast - OVERSCAN_PX) / ROW_HEIGHT);
      let endRow = Math.ceil((scrolledPast + viewportHeight + OVERSCAN_PX) / ROW_HEIGHT);
      startRow = Math.min(Math.max(startRow, 0), totalRows);
      endRow = Math.min(Math.max(endRow, startRow), totalRows);

      const start = startRow * columns;
      const end = Math.min(itemCount, endRow * columns);
      setRange((prev) => (prev.start !== start || prev.end !== end ? { start, end } : prev));
    };

    let scheduled = false;
    const onScrollOrResize = () => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => {
        scheduled = false;
        recompute();
      });
    };

    recompute();
    const scrollTarget: HTMLElement | Window = scroller ?? window;
    scrollTarget.addEventListener("scroll", onScrollOrResize, { passive: true });
    window.addEventListener("resize", onScrollOrResize, { passive: true });
    return () => {
      scrollTarget.removeEventListener("scroll", onScrollOrResize);
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [columns, itemCount, totalRows]);

  const startRow = Math.floor(range.start / columns);
  const visibleFaces = loaded ? arrangedFaces.slice(range.start, range.end) : [];
  const skeletonCount = loaded ? 0 : range.end - range.start;

  return (
    <div
      ref={containerRef}
      style={{ position: "relative", width: "100%", height: totalHeight }}
    >
      <div
        style={{
          position: "absolute",
          top: startRow * ROW_HEIGHT,
          left: 0,
          right: 0,
          display: "grid",
          gridTemplateColumns: `repeat(${columns}, ${TILE_SIZE}px)`,
          columnGap: GAP,
          gridAutoRows: `${ROW_HEIGHT}px`,
          justifyContent: "start",
        }}
      >
        {loaded
          ? visibleFaces.map((face) => (
              <FaceTile
                key={face.id}
                face={face}
                isSelected={selectedSet.has(face.id)}
                onToggle={onToggleFace}
                onOpenImage={
                  onOpenImage
                    ? (openedFace) => onOpenImage(openedFace, arrangedFaces)
                    : undefined
                }
              />
            ))
          : Array.from({ length: skeletonCount }).map((_, index) => (
              <div
                key={`skeleton-${range.start + index}`}
                className="cluster-face-placeholder"
                style={{ alignSelf: "start" }}
              />
            ))}
      </div>
    </div>
  );
};

export default React.memo(ClusterFacesGrid);
