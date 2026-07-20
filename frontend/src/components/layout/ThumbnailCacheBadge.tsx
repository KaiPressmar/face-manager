import React, { useEffect, useState } from "react";

import { ThumbnailWarmupState, ThumbnailWarmupTask } from "../../utils/api";
import { subscribeToTopic } from "../../utils/events";

export type CacheBadge = {
  tone: "complete" | "building";
  text: string;
  title: string;
};

/** Derive the preview-cache badge state from the warmup task snapshot. */
export function buildCacheBadge(task: ThumbnailWarmupTask | null): CacheBadge | null {
  if (!task || task.status === "stopped") {
    return null;
  }
  if (task.cache_complete) {
    return {
      tone: "complete",
      text: "Vorschauen ✓",
      title: "Alle Gesichtsvorschauen sind vorbereitet.",
    };
  }
  const percent =
    task.total_faces > 0
      ? Math.min(100, Math.round((task.cycle_scanned_faces / task.total_faces) * 100))
      : null;
  return {
    tone: "building",
    text: percent != null ? `Vorschauen ${percent}%` : "Vorschauen …",
    title: "Fehlende Gesichtsvorschauen werden vorbereitet.",
  };
}

/** Live preview-cache badge, e.g. for the top bar. */
const ThumbnailCacheBadge: React.FC = () => {
  const [task, setTask] = useState<ThumbnailWarmupTask | null>(null);

  useEffect(() => {
    return subscribeToTopic<ThumbnailWarmupState>(
      "thumbnail-warmup",
      (next) => setTask(next.task),
    );
  }, []);

  const badge = buildCacheBadge(task);
  if (!badge) {
    return null;
  }

  return (
    <span className={`cache-badge cache-badge--${badge.tone}`} title={badge.title}>
      <span className="cache-badge__dot" aria-hidden="true" />
      <span>{badge.text}</span>
    </span>
  );
};

export default ThumbnailCacheBadge;
