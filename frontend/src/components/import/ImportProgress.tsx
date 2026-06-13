import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchImportQueue,
  ImportJob,
  ImportQueueState,
  removeImportJob,
} from "../../utils/api";

const IDLE_POLL_INTERVAL_MS = 5000;
const ACTIVE_POLL_INTERVAL_MS = 1000;

const statusLabels: Record<ImportJob["status"], string> = {
  queued: "Wartet",
  running: "Läuft",
  cancelling: "Wird abgebrochen",
  completed: "Fertig",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
};

function folderName(path: string) {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.at(-1) ?? path;
}

function progressPercent(job: ImportJob) {
  if (job.total_images === 0) return 0;
  return Math.min(100, (job.processed_images / job.total_images) * 100);
}

const ImportJobCard: React.FC<{
  job: ImportJob;
  onRemove: (jobId: string) => Promise<void>;
}> = ({ job, onRemove }) => {
  const isActive = job.status === "running" || job.status === "cancelling";
  const actionLabel = isActive ? "Abbrechen" : "Entfernen";

  return (
    <article className={`import-job import-job--${job.status}`}>
      <div className="import-job__header">
        <strong title={job.folder_path}>{folderName(job.folder_path)}</strong>
        <span>{statusLabels[job.status]}</span>
      </div>

      {job.status === "queued" && job.queue_position && (
        <div className="import-job__meta">
          Position {job.queue_position} in der Warteschlange
        </div>
      )}

      {isActive && (
        <>
          <div className="import-job__meta">
            Bilder: {job.processed_images} / {job.total_images}
          </div>
          <div className="import-job__progress">
            <div style={{ width: `${progressPercent(job)}%` }} />
          </div>
          <div className="import-job__meta">
            Gesichter: {job.processed_faces} / {job.total_faces}
          </div>
        </>
      )}

      {job.last_error && (
        <div className="import-job__error" title={job.last_error}>
          {job.last_error}
        </div>
      )}

      <button
        type="button"
        className="import-job__action"
        disabled={job.status === "cancelling"}
        onClick={() => void onRemove(job.id)}
      >
        {actionLabel}
      </button>
    </article>
  );
};

const ImportProgress = () => {
  const [queue, setQueue] = useState<ImportQueueState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const poll = useCallback(async () => {
    const nextQueue = await fetchImportQueue();
    setQueue(nextQueue);
    setError(null);
    return nextQueue;
  }, []);

  useEffect(() => {
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const schedule = async () => {
      try {
        const nextQueue = await poll();
        if (cancelled) return;
        const isActive =
          nextQueue.active_job_id !== null || nextQueue.queued_count > 0;
        timeoutId = setTimeout(
          schedule,
          isActive ? ACTIVE_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS
        );
      } catch {
        if (cancelled) return;
        setError("Import-Warteschlange nicht erreichbar");
        timeoutId = setTimeout(schedule, IDLE_POLL_INTERVAL_MS);
      }
    };

    const refresh = () => {
      void poll();
    };

    window.addEventListener("face-manager:imports-changed", refresh);
    void schedule();
    return () => {
      cancelled = true;
      window.removeEventListener("face-manager:imports-changed", refresh);
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [poll]);

  const visibleJobs = useMemo(() => {
    if (!queue) return [];
    return [...queue.jobs].sort((left, right) => {
      const priority = { running: 0, cancelling: 0, queued: 1 };
      const leftPriority = priority[left.status as keyof typeof priority] ?? 2;
      const rightPriority = priority[right.status as keyof typeof priority] ?? 2;
      if (leftPriority !== rightPriority) return leftPriority - rightPriority;
      if (left.status === "queued" && right.status === "queued") {
        return (left.queue_position ?? 0) - (right.queue_position ?? 0);
      }
      return right.created_at.localeCompare(left.created_at);
    });
  }, [queue]);

  const handleRemove = async (jobId: string) => {
    try {
      await removeImportJob(jobId);
      await poll();
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Importauftrag konnte nicht geändert werden"
      );
    }
  };

  if (!error && visibleJobs.length === 0) return null;

  return (
    <section className="import-queue">
      <div className="import-queue__title">
        <span>Importe</span>
        {queue && queue.queued_count > 0 && <b>{queue.queued_count}</b>}
      </div>
      {error && <div className="import-queue__error">{error}</div>}
      <div className="import-queue__jobs">
        {visibleJobs.map((job) => (
          <ImportJobCard key={job.id} job={job} onRemove={handleRemove} />
        ))}
      </div>
    </section>
  );
};

export default ImportProgress;
