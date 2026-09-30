import React, { useEffect, useState } from "react";
import { AutoClusterTask, AutoClusterTaskState } from "../../utils/api";
import { subscribeToConnectionStatus, subscribeToTopic } from "../../utils/events";

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

function progressPercent(task: AutoClusterTask) {
  if (task.total_faces <= 0) return task.status === "completed" ? 100 : 0;
  return Math.min(100, (task.processed_faces / task.total_faces) * 100);
}

function reasonLabel(reason: string) {
  switch (reason) {
    case "startup":
      return "Beim Start";
    case "database_import":
      return "Nach dem Wiederherstellen einer Sicherung";
    case "database_import_recovery":
      return "Nach der Datenbank-Reparatur";
    case "threshold_change":
      return "Nach einer Änderung der Gruppierung";
    default:
      return "Hintergrundaufgabe";
  }
}

function taskTitle(task: AutoClusterTask) {
  if (task.kind === "full_recluster") {
    return "Alle Gesichtsgruppen werden neu geordnet";
  }
  if (task.kind === "unassigned_recluster") {
    return "Offene Gesichtsgruppen werden neu aufgebaut";
  }
  return "Neue Gesichter werden geordnet";
}

function taskStageLabel(task: AutoClusterTask) {
  const isRecluster =
    task.kind === "unassigned_recluster" || task.kind === "full_recluster";
  if (task.stage === "preparing") {
    return isRecluster
      ? task.kind === "full_recluster"
        ? "Bereite die Neuordnung aller Gesichtsgruppen vor"
        : "Bereite offene Gesichtsgruppen neu vor"
      : "Bereite die automatische Gruppierung vor";
  }
  if (task.stage === "processing") {
    return isRecluster
      ? task.kind === "full_recluster"
        ? "Untergruppen von Personen und nicht zugewiesene Gesichtsgruppen werden neu geordnet"
        : "Offene Gesichtsgruppen werden neu aufgebaut"
      : "Neue Gesichter werden zu Gruppen geordnet";
  }
  if (task.stage === "failed") {
    return "Automatische Gruppierung fehlgeschlagen";
  }
  return "Automatische Gruppierung abgeschlossen";
}

const AutoClusterProgress = () => {
  const [task, setTask] = useState<AutoClusterTask | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const unsubscribeTask = subscribeToTopic<AutoClusterTaskState>(
      "autocluster",
      (next) => {
        setTask(next.task);
        setError(null);
      },
    );
    const unsubscribeStatus = subscribeToConnectionStatus((status) => {
      setError(status === "closed" ? "Status der automatischen Gruppierung nicht erreichbar" : null);
    });
    return () => {
      unsubscribeTask();
      unsubscribeStatus();
    };
  }, []);

  if (!task && !error) return null;

  const isActive = task?.status === "queued" || task?.status === "running";
  const elapsed = formatDuration(task?.elapsed_seconds ?? null);

  return (
    <section className="import-queue">
      <div className="import-queue__title">
        <span>Automatische Gruppierung</span>
        {task && <div>{reasonLabel(task.reason)}</div>}
      </div>
      {task && (
        <>
          <div className="import-queue__summary">
            <span>Status: {isActive ? "Läuft" : task.status === "failed" ? "Fehler" : "Fertig"}</span>
            <span>Gesichter: {task.processed_faces} / {task.total_faces}</span>
          </div>
          <article className={`import-job import-job--${task.status === "failed" ? "failed" : isActive ? "running" : "completed"}`}>
            <div className="import-job__header">
              <strong>{taskTitle(task)}</strong>
              <span>{reasonLabel(task.reason)}</span>
            </div>
            <div className="import-job__stage">
              <span>{taskStageLabel(task)}</span>
              <b>{Math.round(progressPercent(task))}%</b>
            </div>
            <div className="import-job__progress">
              <div style={{ width: `${progressPercent(task)}%` }} />
            </div>
            <div className="import-job__meta">
              Gesichter geprüft: {task.processed_faces} / {task.total_faces}
            </div>
            {!isActive && task.status === "completed" && (
              <div className="import-job__meta">
                Korrigiert: {task.repaired_faces}
              </div>
            )}
            {elapsed && (
              <div className="import-job__timing">
                <span>Laufzeit: {elapsed}</span>
              </div>
            )}
            {task.last_error && (
              <div className="import-job__error" title={task.last_error}>
                {task.last_error}
              </div>
            )}
          </article>
        </>
      )}
      {error && <div className="import-queue__error">{error}</div>}
    </section>
  );
};

export default AutoClusterProgress;
