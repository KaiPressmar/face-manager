import React, { useEffect, useMemo, useRef, useState } from "react";

import {
  AutoClusterTask,
  AutoClusterTaskState,
  cancelAutoClusterTask,
  cancelImportJob,
  cancelThumbnailWarmup,
  clearImportHistory,
  deleteAutoClusterHistoryEntry,
  deleteImportHistoryEntry,
  deleteThumbnailWarmupHistory,
  ImportJob,
  ImportQueueState,
  pauseAutoClusterTask,
  pauseImportJob,
  pauseThumbnailWarmup,
  resumeAutoClusterTask,
  resumeImportJob,
  resumeThumbnailWarmup,
  ThumbnailWarmupState,
  ThumbnailWarmupTask,
} from "../../utils/api";
import { subscribeToConnectionStatus, subscribeToTopic } from "../../utils/events";
import TaskActionIcon from "./TaskActionIcon";

type TaskTone = "active" | "waiting" | "completed" | "failed" | "cancelled";
type TaskKind = "import" | "groups" | "previews";
type TaskControlState = "queued" | "running" | "paused" | "cancelling" | "terminal";

export type BackgroundTaskView = {
  id: string;
  sourceId: string;
  kind: TaskKind;
  controlState: TaskControlState;
  title: string;
  status: string;
  summary: string;
  tone: TaskTone;
  progress: number | null;
  etaSeconds: number | null;
  elapsedSeconds: number | null;
  finishedAt: string | null;
};

const TERMINAL_IMPORT_STATUSES = new Set<ImportJob["status"]>([
  "completed",
  "failed",
  "cancelled",
]);

function finiteDuration(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) ? Math.max(0, value) : null;
}

export function formatFriendlyDuration(seconds: number | null): string | null {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const rounded = Math.max(0, Math.round(seconds));
  if (rounded < 45) return "weniger als 1 Min.";
  if (rounded < 3600) return `${Math.max(1, Math.ceil(rounded / 60))} Min.`;
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.ceil((rounded % 3600) / 300) * 5;
  if (minutes >= 60) return `${hours + 1} Std.`;
  return minutes > 0 ? `${hours} Std. ${minutes} Min.` : `${hours} Std.`;
}

function folderName(path: string) {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.at(-1) ?? path;
}

function boundedProgress(current: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.max(0, Math.min(100, (current / total) * 100));
}

function importProgress(job: ImportJob): number | null {
  if (job.status === "completed") return 100;
  if (job.stage === "processing" || job.stage === "finalizing") {
    return boundedProgress(job.processed_images, job.total_images);
  }
  if (job.stage === "hashing") {
    return boundedProgress(job.stage_current, job.stage_total);
  }
  return null;
}

function importSummary(job: ImportJob): string {
  if (job.status === "queued") return "Wartet, bis eine andere Aufgabe fertig ist";
  if (job.status === "paused") return "Pausiert und kann jederzeit fortgesetzt werden";
  if (job.status === "cancelling") return "Wird sicher beendet";
  if (job.status === "completed") {
    return `${job.processed_images} Bilder wurden geprüft`;
  }
  if (job.status === "failed") {
    return "Konnte nicht vollständig abgeschlossen werden";
  }
  if (job.status === "cancelled") return "Wurde vorzeitig beendet";
  switch (job.stage) {
    case "scanning":
      return "Bilder im Ordner werden gesucht";
    case "hashing":
      return "Neue und bereits bekannte Bilder werden unterschieden";
    case "processing":
      return job.total_images > 0
        ? `${job.processed_images} von ${job.total_images} Bildern geprüft`
        : "Gesichter in den Bildern werden erkannt";
    case "finalizing":
      return "Ergebnisse werden für die Ansichten vorbereitet";
    default:
      return "Die Bilderkennung wird vorbereitet";
  }
}

function importTask(job: ImportJob): BackgroundTaskView {
  const status: Record<ImportJob["status"], string> = {
    queued: "Wartet",
    running: "In Arbeit",
    paused: "Pausiert",
    cancelling: "Wird beendet",
    completed: "Abgeschlossen",
    failed: "Nicht abgeschlossen",
    cancelled: "Abgebrochen",
  };
  const tone: Record<ImportJob["status"], TaskTone> = {
    queued: "waiting",
    running: "active",
    paused: "waiting",
    cancelling: "waiting",
    completed: "completed",
    failed: "failed",
    cancelled: "cancelled",
  };
  return {
    id: `import:${job.id}`,
    sourceId: job.id,
    kind: "import",
    controlState: ["completed", "failed", "cancelled"].includes(job.status)
      ? "terminal"
      : job.status as "queued" | "running" | "paused" | "cancelling",
    title: `Bilder aus „${folderName(job.folder_path)}“ hinzufügen`,
    status: status[job.status],
    summary: importSummary(job),
    tone: tone[job.status],
    progress: importProgress(job),
    etaSeconds: finiteDuration(job.eta_seconds),
    elapsedSeconds: finiteDuration(job.elapsed_seconds),
    finishedAt: job.finished_at,
  };
}

function estimateAutoClusterEta(task: AutoClusterTask | null): number | null {
  if (
    !task ||
    task.processed_faces <= 0 ||
    task.total_faces <= 0 ||
    task.elapsed_seconds == null ||
    !Number.isFinite(task.elapsed_seconds)
  ) {
    return null;
  }
  if (task.processed_faces >= task.total_faces) return 0;
  const rate = task.processed_faces / Math.max(task.elapsed_seconds, 1);
  return rate > 0 ? (task.total_faces - task.processed_faces) / rate : null;
}

function autoClusterTask(task: AutoClusterTask): BackgroundTaskView {
  const isTerminal = task.status === "completed" || task.status === "failed" || task.status === "cancelled";
  const title =
    task.kind === "full_recluster"
      ? "Gesichtsgruppen aktualisieren"
      : task.kind === "unassigned_recluster"
        ? "Neue Gesichtsgruppen ordnen"
        : "Gesichtsgruppen aufräumen";
  const tone: TaskTone =
    task.status === "failed"
      ? "failed"
      : task.status === "cancelled"
        ? "cancelled"
        : task.status === "completed"
          ? "completed"
            : task.status === "queued" || task.status === "paused" || task.status === "cancelling"
            ? "waiting"
            : "active";
  return {
    id: `groups:${task.id}`,
    sourceId: task.id,
    kind: "groups",
    controlState: isTerminal
      ? "terminal"
      : task.status as "queued" | "running" | "paused" | "cancelling",
    title,
    status:
      task.status === "failed"
        ? "Nicht abgeschlossen"
        : task.status === "cancelled"
          ? "Abgebrochen"
          : task.status === "completed"
            ? "Abgeschlossen"
            : task.status === "queued"
              ? "Wartet"
              : task.status === "paused"
                ? "Pausiert"
                : task.status === "cancelling"
                  ? "Wird beendet"
              : "In Arbeit",
    summary:
      task.status === "failed"
        ? "Die bisherigen Zuordnungen bleiben erhalten"
        : task.status === "cancelled"
          ? "Wurde beendet und kann später fortgesetzt werden"
          : task.status === "completed"
            ? "Die Gesichtsgruppen sind wieder auf dem aktuellen Stand"
            : task.status === "queued"
              ? "Beginnt, sobald andere wichtige Aufgaben fertig sind"
              : task.status === "paused"
                ? "Pausiert und kann jederzeit fortgesetzt werden"
                : task.status === "cancelling"
                  ? "Wird an einer sicheren Stelle beendet"
              : task.total_faces > 0
                ? `${task.processed_faces} von ${task.total_faces} Gesichtern geprüft`
                : "Gesichter werden neu geordnet",
    tone,
    progress: isTerminal
      ? task.status === "completed"
        ? 100
        : boundedProgress(task.processed_faces, task.total_faces)
      : boundedProgress(task.processed_faces, task.total_faces),
    etaSeconds: isTerminal ? null : estimateAutoClusterEta(task),
    elapsedSeconds: finiteDuration(task.elapsed_seconds),
    finishedAt: task.finished_at,
  };
}

function thumbnailTask(task: ThumbnailWarmupTask): BackgroundTaskView {
  const failed = task.status === "failed";
  const cancelled = task.status === "cancelled";
  const completed = task.cache_complete;
  return {
    id: `previews:${task.started_at ?? task.last_run_at ?? "current"}`,
    sourceId: "thumbnail-warmup",
    kind: "previews",
    controlState: failed || cancelled || completed
      ? "terminal"
      : task.user_paused
        ? "paused"
        : "running",
    title: "Bildvorschauen vorbereiten",
    status: failed
      ? "Nicht abgeschlossen"
      : cancelled
        ? "Abgebrochen"
      : completed
        ? "Abgeschlossen"
        : task.status === "paused"
          ? task.user_paused ? "Pausiert" : "Wartet kurz"
          : "In Arbeit",
    summary: failed
      ? "Vorschauen werden bei Bedarf weiterhin direkt erstellt"
      : cancelled
        ? "Die Vorbereitung wurde beendet"
      : completed
        ? "Alle Gesichtsansichten können schneller angezeigt werden"
        : task.status === "paused"
          ? task.user_paused
            ? "Pausiert und kann jederzeit fortgesetzt werden"
            : "Andere Aufgaben haben gerade Vorrang"
          : task.total_faces > 0
            ? `${task.cycle_scanned_faces} von ${task.total_faces} Vorschauen geprüft`
            : "Bildvorschauen werden vorbereitet",
    tone: failed ? "failed" : cancelled ? "cancelled" : completed ? "completed" : task.status === "paused" ? "waiting" : "active",
    progress: completed ? 100 : boundedProgress(task.cycle_scanned_faces, task.total_faces),
    etaSeconds: completed || failed || cancelled ? null : finiteDuration(task.eta_seconds),
    elapsedSeconds: null,
    finishedAt: completed || failed || cancelled ? task.last_run_at : null,
  };
}

/**
 * Calculate time until all coordinated work is done.
 *
 * Import request ETAs must never be added: the backend's overall ETA is the
 * critical path across all parallel import workers. Other task families yield
 * to one another, so their remaining durations are added after that makespan.
 */
export function calculateOverallEta(
  queue: ImportQueueState | null,
  clustering: AutoClusterTask | null,
  thumbnails: ThumbnailWarmupTask | null,
): number | null {
  const durations: number[] = [];
  const importsPending = Boolean(
    queue &&
      ((queue.running_count ?? queue.active_job_ids?.length ?? 0) > 0 ||
        queue.queued_count > 0 ||
        (queue.paused_count ?? 0) > 0),
  );
  if (importsPending) {
    const eta = finiteDuration(queue?.overall_eta_seconds);
    if (eta == null) return null;
    durations.push(eta);
  }

  if (clustering && ["queued", "running", "paused", "cancelling"].includes(clustering.status)) {
    const eta = estimateAutoClusterEta(clustering);
    if (eta == null) return null;
    durations.push(eta);
  }

  if (
    thumbnails &&
    !thumbnails.cache_complete &&
    (thumbnails.status === "running" || thumbnails.status === "paused")
  ) {
    const eta = finiteDuration(thumbnails.eta_seconds);
    if (eta == null) return null;
    durations.push(eta);
  }

  return durations.length > 0 ? durations.reduce((sum, duration) => sum + duration, 0) : null;
}

function finishedLabel(timestamp: string | null): string {
  if (!timestamp) return "Kürzlich";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "Kürzlich";
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const time = date.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return `Heute, ${time}`;
  if (date.toDateString() === yesterday.toDateString()) return `Gestern, ${time}`;
  return date.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}

const BackgroundTasksStatus: React.FC = () => {
  const [queue, setQueue] = useState<ImportQueueState | null>(null);
  const [clustering, setClustering] = useState<AutoClusterTask | null>(null);
  const [thumbnails, setThumbnails] = useState<ThumbnailWarmupTask | null>(null);
  const [sessionHistory, setSessionHistory] = useState<BackgroundTaskView[]>([]);
  const [connectionError, setConnectionError] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [hasFocus, setHasFocus] = useState(false);
  const [isPinned, setIsPinned] = useState(false);
  const [unseenFinished, setUnseenFinished] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const knownImportTerminalIds = useRef<Set<string> | null>(null);
  const previousClusterState = useRef<string | null>(null);
  const previousThumbnailState = useRef<string | null>(null);

  useEffect(() => {
    const unsubscribeImports = subscribeToTopic<ImportQueueState>("imports", (next) => {
      const terminalIds = new Set(
        next.jobs.filter((job) => TERMINAL_IMPORT_STATUSES.has(job.status)).map((job) => job.id),
      );
      if (knownImportTerminalIds.current) {
        const newlyFinished = [...terminalIds].filter((id) => !knownImportTerminalIds.current?.has(id));
        if (newlyFinished.length > 0) setUnseenFinished((count) => count + newlyFinished.length);
      }
      knownImportTerminalIds.current = terminalIds;
      setQueue(next);
      setConnectionError(false);
    });
    const unsubscribeClustering = subscribeToTopic<AutoClusterTaskState>("autocluster", (next) => {
      const task = next.task;
      const stateKey = task ? `${task.id}:${task.status}` : "none";
      const terminal = task && ["completed", "failed", "cancelled"].includes(task.status);
      if (terminal) {
        const view = autoClusterTask(task);
        setSessionHistory((current) => [view, ...current.filter((entry) => entry.id !== view.id)].slice(0, 8));
        if (previousClusterState.current && previousClusterState.current !== stateKey) {
          setUnseenFinished((count) => count + 1);
        }
      }
      previousClusterState.current = stateKey;
      setClustering(task);
      setConnectionError(false);
    });
    const unsubscribeThumbnails = subscribeToTopic<ThumbnailWarmupState>("thumbnail-warmup", (next) => {
      const task = next.task;
      if (task?.cache_complete || task?.status === "failed" || task?.status === "cancelled") {
        const view = thumbnailTask(task);
        setSessionHistory((current) => [view, ...current.filter((entry) => entry.id !== view.id)].slice(0, 8));
      }
      const stateKey = task ? `${task.status}:${task.cache_complete}` : "none";
      const justFinished = Boolean(
        previousThumbnailState.current &&
          previousThumbnailState.current !== stateKey &&
          (task?.cache_complete || task?.status === "failed" || task?.status === "cancelled"),
      );
      if (justFinished) {
        setUnseenFinished((count) => count + 1);
      }
      previousThumbnailState.current = stateKey;
      setThumbnails(task);
      setConnectionError(false);
    });
    const unsubscribeStatus = subscribeToConnectionStatus((status) => {
      setConnectionError(status === "closed");
    });
    return () => {
      unsubscribeImports();
      unsubscribeClustering();
      unsubscribeThumbnails();
      unsubscribeStatus();
    };
  }, []);

  const activeTasks = useMemo(() => {
    const tasks = queue?.jobs
      .filter((job) => !TERMINAL_IMPORT_STATUSES.has(job.status))
      .map(importTask) ?? [];
    if (clustering && ["queued", "running", "paused", "cancelling"].includes(clustering.status)) {
      tasks.push(autoClusterTask(clustering));
    }
    if (
      thumbnails &&
      !thumbnails.cache_complete &&
      (thumbnails.status === "running" || thumbnails.status === "paused")
    ) {
      tasks.push(thumbnailTask(thumbnails));
    }
    return tasks;
  }, [clustering, queue, thumbnails]);

  const history = useMemo(() => {
    const imported = queue?.jobs
      .filter((job) => TERMINAL_IMPORT_STATUSES.has(job.status))
      .map(importTask) ?? [];
    const byId = new Map<string, BackgroundTaskView>();
    [...imported, ...sessionHistory].forEach((entry) => byId.set(entry.id, entry));
    return [...byId.values()]
      .sort((left, right) => (right.finishedAt ?? "").localeCompare(left.finishedAt ?? ""))
      .slice(0, 8);
  }, [queue, sessionHistory]);

  const totalEta = calculateOverallEta(queue, clustering, thumbnails);
  const runningCount = activeTasks.filter((task) => task.tone === "active").length;
  const waitingCount = activeTasks.length - runningCount;
  const parallelImports = queue?.running_count ?? queue?.active_job_ids?.length ?? 0;
  const open = isHovered || hasFocus || isPinned;
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const controlTask = async (
    task: BackgroundTaskView,
    action: "pause" | "resume" | "cancel",
  ) => {
    setBusyTaskId(task.id);
    setActionError(null);
    try {
      if (task.kind === "import") {
        await (action === "pause"
          ? pauseImportJob(task.sourceId)
          : action === "resume"
            ? resumeImportJob(task.sourceId)
            : cancelImportJob(task.sourceId));
      } else if (task.kind === "groups") {
        await (action === "pause"
          ? pauseAutoClusterTask(task.sourceId)
          : action === "resume"
            ? resumeAutoClusterTask(task.sourceId)
            : cancelAutoClusterTask(task.sourceId));
      } else {
        await (action === "pause"
          ? pauseThumbnailWarmup()
          : action === "resume"
            ? resumeThumbnailWarmup()
            : cancelThumbnailWarmup());
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Die Aufgabe konnte nicht geändert werden.");
    } finally {
      setBusyTaskId(null);
    }
  };

  const deleteHistoryTask = async (task: BackgroundTaskView) => {
    if (!window.confirm("Diesen Eintrag dauerhaft aus der Historie löschen?")) return;
    setBusyTaskId(task.id);
    setActionError(null);
    try {
      if (task.kind === "import") {
        await deleteImportHistoryEntry(task.sourceId);
      } else if (task.kind === "groups") {
        if (clustering?.id === task.sourceId) {
          await deleteAutoClusterHistoryEntry(task.sourceId);
        }
        setSessionHistory((current) => current.filter((entry) => entry.id !== task.id));
      } else {
        await deleteThumbnailWarmupHistory();
        setSessionHistory((current) => current.filter((entry) => entry.id !== task.id));
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Der Eintrag konnte nicht gelöscht werden.");
    } finally {
      setBusyTaskId(null);
    }
  };

  const clearHistory = async () => {
    if (!window.confirm("Die gesamte Aufgabenhistorie dauerhaft löschen?")) return;
    setBusyTaskId("history");
    setActionError(null);
    try {
      await clearImportHistory();
      const cleanup: Promise<unknown>[] = [];
      if (clustering && ["completed", "failed", "cancelled"].includes(clustering.status)) {
        cleanup.push(deleteAutoClusterHistoryEntry(clustering.id));
      }
      if (thumbnails && (thumbnails.cache_complete || ["failed", "cancelled"].includes(thumbnails.status))) {
        cleanup.push(deleteThumbnailWarmupHistory());
      }
      await Promise.allSettled(cleanup);
      setSessionHistory([]);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Die Historie konnte nicht gelöscht werden.");
    } finally {
      setBusyTaskId(null);
    }
  };

  useEffect(() => {
    if (open) setUnseenFinished(0);
  }, [history.length, open]);

  useEffect(() => {
    if (!isPinned) return undefined;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node | null)) setIsPinned(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsPinned(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [isPinned]);

  if (!connectionError && activeTasks.length === 0 && history.length === 0) return null;

  const compactLabel =
    activeTasks.length > 0
      ? `${activeTasks.length} ${activeTasks.length === 1 ? "Aufgabe" : "Aufgaben"}`
      : unseenFinished > 0
        ? `${unseenFinished} abgeschlossen`
        : "Aktivitäten";
  const compactEta =
    activeTasks.length > 0
      ? formatFriendlyDuration(totalEta) ?? "Zeit wird berechnet"
      : "Bereit";

  return (
    <div
      ref={rootRef}
      className={`activity-center${open ? " activity-center--open" : ""}`}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocusCapture={() => setHasFocus(true)}
      onBlurCapture={(event) => {
        if (!rootRef.current?.contains(event.relatedTarget as Node | null)) setHasFocus(false);
      }}
    >
      <button
        type="button"
        className={`activity-center__trigger${activeTasks.length > 0 ? " activity-center__trigger--busy" : ""}`}
        aria-expanded={open}
        aria-controls="activity-center-popover"
        onClick={() => setIsPinned((current) => !current)}
        title="Details beim Darüberfahren oder per Klick anzeigen"
      >
        <span className="activity-center__indicator" aria-hidden="true" />
        <span className="activity-center__trigger-copy">
          <strong>{compactLabel}</strong>
          <small>{compactEta}</small>
        </span>
        {unseenFinished > 0 && <b aria-label={`${unseenFinished} neue Abschlüsse`}>{unseenFinished}</b>}
      </button>

      {open && (
        <section id="activity-center-popover" className="activity-center__popover" aria-label="Aktivitäten">
          <header className="activity-center__header">
            <div>
              <strong>Aktivitäten</strong>
              <p>
                {activeTasks.length > 0
                  ? "Während dieser Arbeiten kann Face Manager vorübergehend langsamer reagieren."
                  : "Aktuell ist keine Aufgabe offen."}
              </p>
            </div>
            {activeTasks.length > 0 && (
              <span>{formatFriendlyDuration(totalEta) ? `Noch ca. ${formatFriendlyDuration(totalEta)}` : "Restzeit wird berechnet"}</span>
            )}
          </header>

          {activeTasks.length > 0 && (
            <div className="activity-center__section">
              <div className="activity-center__section-title">
                <span>Aktuell</span>
                <span>
                  {runningCount > 0 ? `${runningCount} in Arbeit` : ""}
                  {runningCount > 0 && waitingCount > 0 ? " · " : ""}
                  {waitingCount > 0 ? `${waitingCount} warten` : ""}
                </span>
              </div>
              <div className="activity-center__tasks">
                {activeTasks.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    busy={busyTaskId === task.id}
                    onPause={() => void controlTask(task, "pause")}
                    onResume={() => void controlTask(task, "resume")}
                    onCancel={() => void controlTask(task, "cancel")}
                  />
                ))}
              </div>
              {parallelImports > 1 && (
                <p className="activity-center__parallel-note">
                  {parallelImports} Bilderordner werden gleichzeitig verarbeitet. Die angezeigte Gesamtzeit berücksichtigt das bereits.
                </p>
              )}
            </div>
          )}

          <div className="activity-center__section activity-center__section--history">
            <div className="activity-center__section-title">
              <span>Zuletzt beendet</span>
              <div>
                <span>{history.length}</span>
                {history.length > 0 && (
                  <button
                    type="button"
                    className="task-icon-button task-icon-button--danger"
                    disabled={busyTaskId !== null}
                    onClick={() => void clearHistory()}
                    aria-label="Gesamte Aktivitäten-Historie löschen"
                    title="Gesamte Historie löschen"
                  >
                    <TaskActionIcon name="delete" />
                  </button>
                )}
              </div>
            </div>
            {history.length > 0 ? (
              <div className="activity-center__history">
                {history.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    history
                    busy={busyTaskId === task.id}
                    onDelete={() => void deleteHistoryTask(task)}
                  />
                ))}
              </div>
            ) : (
              <p className="activity-center__empty">In dieser Sitzung wurde noch nichts abgeschlossen.</p>
            )}
          </div>

          {connectionError && (
            <p className="activity-center__connection">Der Aufgabenstatus wird gerade neu verbunden.</p>
          )}
          {actionError && <p className="activity-center__connection">{actionError}</p>}
        </section>
      )}
    </div>
  );
};

const TaskRow: React.FC<{
  task: BackgroundTaskView;
  history?: boolean;
  busy?: boolean;
  onPause?: () => void;
  onResume?: () => void;
  onCancel?: () => void;
  onDelete?: () => void;
}> = ({
  task,
  history = false,
  busy = false,
  onPause,
  onResume,
  onCancel,
  onDelete,
}) => {
  const duration = formatFriendlyDuration(history ? task.elapsedSeconds : task.etaSeconds);
  return (
    <article className={`activity-task activity-task--${task.tone}`}>
      <div className="activity-task__topline">
        <strong>{task.title}</strong>
        <span>{task.status}</span>
      </div>
      <p>{task.summary}</p>
      {!history && task.progress != null && (
        <div
          className="activity-task__progress"
          role="progressbar"
          aria-label={`Fortschritt: ${Math.round(task.progress)} Prozent`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(task.progress)}
        >
          <div style={{ width: `${task.progress}%` }} />
        </div>
      )}
      <div className="activity-task__meta">
        <span>{history ? finishedLabel(task.finishedAt) : duration ? `Fertig in ca. ${duration}` : "Zeit wird berechnet"}</span>
        {history && duration && <span>Dauer: {duration}</span>}
      </div>
      <div className="activity-task__actions">
        {!history && task.controlState === "paused" && onResume && (
          <button
            type="button"
            className="task-icon-button"
            disabled={busy}
            onClick={onResume}
            aria-label={`${task.title} fortsetzen`}
            title="Fortsetzen"
          >
            <TaskActionIcon name="resume" />
          </button>
        )}
        {!history && ["queued", "running"].includes(task.controlState) && onPause && (
          <button
            type="button"
            className="task-icon-button"
            disabled={busy}
            onClick={onPause}
            aria-label={`${task.title} pausieren`}
            title="Pausieren"
          >
            <TaskActionIcon name="pause" />
          </button>
        )}
        {!history && task.controlState !== "cancelling" && onCancel && (
          <button
            type="button"
            className="task-icon-button task-icon-button--danger"
            disabled={busy}
            onClick={onCancel}
            aria-label={`${task.title} abbrechen`}
            title="Abbrechen"
          >
            <TaskActionIcon name="cancel" />
          </button>
        )}
        {history && onDelete && (
          <button
            type="button"
            className="task-icon-button task-icon-button--danger"
            disabled={busy}
            onClick={onDelete}
            aria-label={`${task.title} aus der Historie löschen`}
            title="Aus Historie löschen"
          >
            <TaskActionIcon name="delete" />
          </button>
        )}
      </div>
    </article>
  );
};

export default BackgroundTasksStatus;
