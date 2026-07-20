import React, { useEffect, useMemo, useState } from "react";
import {
  ImportJob,
  ImportStation,
  ImportQueueState,
  removeImportJob,
} from "../../utils/api";
import { subscribeToConnectionStatus, subscribeToTopic } from "../../utils/events";

const statusLabels: Record<ImportJob["status"], string> = {
  queued: "Wartet",
  running: "Läuft",
  cancelling: "Wird abgebrochen",
  completed: "Fertig",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
};

const stageLabels: Record<NonNullable<ImportJob["stage"]>, string> = {
  scanning: "Ordner wird durchsucht",
  hashing: "Dateien werden geprüft",
  loading_model: "Bilderkennung wird vorbereitet",
  loading_index: "Bekannte Gesichter werden abgeglichen",
  processing: "Gesichter werden erkannt",
  finalizing: "Ergebnisse werden gespeichert",
  completed: "Bilder wurden hinzugefügt",
};

const stationStateLabels: Record<ImportStation["state"], string> = {
  queued: "Wartet",
  active: "Aktiv",
  done: "Fertig",
  failed: "Fehler",
  cancelled: "Abgebrochen",
};

function folderName(path: string) {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.at(-1) ?? path;
}

function progressPercent(job: ImportJob) {
  if (job.stage_total === 0) return 0;
  return Math.min(100, (job.stage_current / job.stage_total) * 100);
}

function stationProgressPercent(station: ImportStation) {
  if (station.progress_total <= 0) {
    return station.state === "done" ? 100 : 0;
  }
  return Math.min(
    100,
    (station.progress_current / station.progress_total) * 100,
  );
}

function formatDuration(seconds: number | null) {
  if (seconds === null || !Number.isFinite(seconds)) return null;
  const rounded = Math.max(0, Math.round(seconds));
  if (rounded < 60) return `${rounded} Sek.`;
  const minutes = Math.floor(rounded / 60);
  const remainingSeconds = rounded % 60;
  if (minutes < 60) {
    return remainingSeconds > 0
      ? `${minutes} Min. ${remainingSeconds} Sek.`
      : `${minutes} Min.`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours} Std. ${remainingMinutes} Min.`;
}

function currentItemName(path: string) {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).at(-1) ?? path;
}

function stationKeyLabel(station: ImportStation) {
  return `${stationStateLabels[station.state]} · ${station.label}`;
}

function stationEtaLabel(station: ImportStation) {
  if (station.eta_seconds == null) return null;
  const formatted = formatDuration(station.eta_seconds);
  if (!formatted) return null;
  if (station.state === "queued") return `Start in ca. ${formatted}`;
  if (station.state === "active") return `Noch ca. ${formatted}`;
  return formatted;
}

const ImportStationRail: React.FC<{ stations: ImportStation[] }> = ({
  stations,
}) => {
  if (stations.length === 0) return null;

  return (
    <div className="import-job__stations">
      {stations.map((station, index) => (
        <article
          key={`${station.job_id}-${station.key}`}
          className={`import-station import-station--${station.state}`}
        >
          <div className="import-station__header">
            <div className="import-station__title-wrap">
              <span className="import-station__index">0{index + 1}</span>
              <div>
                <strong>{station.label}</strong>
                <span>{stationKeyLabel(station)}</span>
              </div>
            </div>
            {stationEtaLabel(station) && <b>{stationEtaLabel(station)}</b>}
          </div>
          <div className="import-station__progress">
            <div style={{ width: `${stationProgressPercent(station)}%` }} />
          </div>
          <div className="import-station__meta">
            <span>
              {station.progress_current}
              {station.progress_total > 0 ? ` / ${station.progress_total}` : ""}
            </span>
            {station.detail && (
              <span title={station.detail}>{station.detail}</span>
            )}
          </div>
        </article>
      ))}
    </div>
  );
};

const ImportJobCard: React.FC<{
  job: ImportJob;
  onRemove: (jobId: string) => Promise<void>;
  collapsed: boolean;
  onToggleCollapse: () => void;
}> = ({ job, onRemove, collapsed, onToggleCollapse }) => {
  const isActive = job.status === "running" || job.status === "cancelling";
  const actionLabel = isActive ? "Abbrechen" : "Entfernen";
  const stageLabel = job.stage ? stageLabels[job.stage] : null;
  const eta = formatDuration(job.eta_seconds);
  const elapsed = formatDuration(job.elapsed_seconds);
  const stations = job.stations ?? [];
  const activeStation = stations.find((station) => station.state === "active");
  const activeStations = stations.filter((station) => station.state === "active");
  const stationEta = activeStation
    ? formatDuration(activeStation.eta_seconds)
    : null;
  const parallelTasksLabel =
    activeStations.length > 1
      ? `Parallel aktiv: ${activeStations.map((station) => station.label).join(" + ")}`
      : null;
  const collapsedSummary =
    activeStations.length > 0
      ? `Aktiv: ${activeStations.map((station) => station.label).join(" + ")}`
      : stageLabel ?? statusLabels[job.status];

  return (
    <article className={`import-job import-job--${job.status}`}>
      <div className="import-job__header">
        <strong title={job.folder_path}>{folderName(job.folder_path)}</strong>
        <div className="import-job__header-controls">
          <span>
            {statusLabels[job.status]}
            {job.queue_position && job.status === "queued"
              ? ` · #${job.queue_position}`
              : ""}
          </span>
          <button
            type="button"
            className="import-job__toggle"
            onClick={onToggleCollapse}
            aria-expanded={!collapsed}
          >
            {collapsed ? "Ausklappen" : "Einklappen"}
          </button>
        </div>
      </div>

      {collapsed && (
        <div className="import-job__collapsed">
          <span>{collapsedSummary}</span>
          <div>
            {elapsed && <span>Vergangen: {elapsed}</span>}
            {eta && <span>Noch ca. {eta}</span>}
          </div>
        </div>
      )}

      {!collapsed && (
        <>
          {job.status === "queued" && job.queue_position && (
            <>
              <div className="import-job__meta">
                Position {job.queue_position} in der Warteschlange
              </div>
              {eta && (
                <div className="import-job__timing">
                  Voraussichtlich fertig in {eta}
                </div>
              )}
            </>
          )}

          {stations.length > 0 && <ImportStationRail stations={stations} />}

          {isActive && (
            <>
              <div className="import-job__stage">
                <span>{stageLabel ?? "Bilder werden vorbereitet"}</span>
                {stationEta ? (
                  <b>{stationEta}</b>
                ) : (
                  <b>{Math.round(progressPercent(job))}%</b>
                )}
              </div>
              {parallelTasksLabel && (
                <div className="import-job__meta import-job__meta--parallel">
                  {parallelTasksLabel}
                </div>
              )}
              <div className="import-job__progress">
                <div style={{ width: `${progressPercent(job)}%` }} />
              </div>
              {job.stage === "scanning" && (
                <div className="import-job__meta">
                  {job.stage_current} Bilder gefunden
                </div>
              )}
              {job.stage === "hashing" && (
                <div className="import-job__meta">
                  Dateien geprüft: {job.stage_current} / {job.stage_total}
                </div>
              )}
              {(job.stage === "processing" || job.stage === "finalizing") && (
                <div className="import-job__meta">
                  Bilder: {job.processed_images} / {job.total_images}
                </div>
              )}
              <div className="import-job__meta">
                Gesichter: {job.processed_faces} / {job.total_faces}
              </div>
              {job.current_file && (
                <div className="import-job__current" title={job.current_file}>
                  Aktuell: {currentItemName(job.current_file)}
                </div>
              )}
              <div className="import-job__timing">
                {elapsed && <span>Vergangen: {elapsed}</span>}
                {eta && <span>Restzeit: ca. {eta}</span>}
              </div>
            </>
          )}

          {!isActive && elapsed && (
            <div className="import-job__timing">
              <span>Laufzeit: {elapsed}</span>
              {eta && <span>Geschätzte Restzeit: {eta}</span>}
            </div>
          )}
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
  const [collapsedByJob, setCollapsedByJob] = useState<Record<string, boolean>>(
    {},
  );

  useEffect(() => {
    const unsubscribeQueue = subscribeToTopic<ImportQueueState>(
      "imports",
      (next) => {
        setQueue(next);
        setError(null);
      },
    );
    const unsubscribeStatus = subscribeToConnectionStatus((status) => {
      setError(status === "closed" ? "Status der Bildimporte nicht erreichbar" : null);
    });
    return () => {
      unsubscribeQueue();
      unsubscribeStatus();
    };
  }, []);

  const visibleJobs = useMemo(() => {
    if (!queue) return [];
    return [...queue.jobs].sort((left, right) => {
      const priority = { running: 0, cancelling: 0, queued: 1 };
      const leftPriority = priority[left.status as keyof typeof priority] ?? 2;
      const rightPriority =
        priority[right.status as keyof typeof priority] ?? 2;
      if (leftPriority !== rightPriority) return leftPriority - rightPriority;
      if (left.status === "queued" && right.status === "queued") {
        return (left.queue_position ?? 0) - (right.queue_position ?? 0);
      }
      return right.created_at.localeCompare(left.created_at);
    });
  }, [queue]);

  const handleRemove = async (jobId: string) => {
    try {
      // The queue snapshot refreshes itself via the imports subscription.
      await removeImportJob(jobId);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Die Aufgabe konnte nicht geändert werden",
      );
    }
  };

  if (!error && visibleJobs.length === 0) return null;

  const isCollapsed = (job: ImportJob) => {
    if (job.id in collapsedByJob) return collapsedByJob[job.id];
    return !(job.status === "running" || job.status === "cancelling");
  };

  const toggleCollapsed = (job: ImportJob) => {
    setCollapsedByJob((current) => {
      const currentValue =
        job.id in current
          ? current[job.id]
          : !(job.status === "running" || job.status === "cancelling");
      return { ...current, [job.id]: !currentValue };
    });
  };

  const runningCount = queue?.running_count ?? (queue?.active_job_ids?.length ?? 0);
  const slotCount = queue?.max_concurrent_jobs ?? runningCount;

  return (
    <section className="import-queue">
      <div className="import-queue__title">
        <span>Bilder hinzufügen</span>
        <div>
          {queue?.overall_eta_seconds != null && (
            <span>Gesamt: ca. {formatDuration(queue.overall_eta_seconds)}</span>
          )}
          {queue && queue.queued_count > 0 && <b>{queue.queued_count}</b>}
        </div>
      </div>
      {queue && (
        <div className="import-queue__summary">
          <span>Aktiv: {runningCount}</span>
          <span>Gleichzeitig möglich: {slotCount}</span>
          <span>Wartend: {queue.queued_count}</span>
          <span>Aufgaben insgesamt: {queue.jobs.length}</span>
        </div>
      )}
      {error && <div className="import-queue__error">{error}</div>}
      <div className="import-queue__jobs">
        {visibleJobs.map((job) => (
          <ImportJobCard
            key={job.id}
            job={job}
            onRemove={handleRemove}
            collapsed={isCollapsed(job)}
            onToggleCollapse={() => toggleCollapsed(job)}
          />
        ))}
      </div>
    </section>
  );
};

export default ImportProgress;
