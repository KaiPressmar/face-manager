import React, { useEffect, useRef, useState } from "react";

import {
  AutoClusterTaskState,
  ImportQueueState,
  OPERATION_BLOCKED_EVENT,
} from "../../utils/api";
import { subscribeToTopic } from "../../utils/events";

type Notice = { id: number; title: string; message: string };

const BackgroundActivityNotice: React.FC = () => {
  const [importsBusy, setImportsBusy] = useState(false);
  const [clusteringBusy, setClusteringBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const importsWereBusy = useRef(false);
  const clusteringWasBusy = useRef(false);
  const receivedImportsSnapshot = useRef(false);
  const receivedClusteringSnapshot = useRef(false);

  useEffect(() => {
    const unsubscribeImports = subscribeToTopic<ImportQueueState>("imports", (state) => {
      const busy = Boolean((state.running_count || 0) > 0 || state.queued_count > 0);
      if (receivedImportsSnapshot.current && importsWereBusy.current && !busy) {
        setNotice({
          id: Date.now(),
          title: "Bilder wurden hinzugefügt",
          message:
            "Neue Gesichter und Gesichtsgruppen wurden geladen. Geöffnete Ansichten werden automatisch aktualisiert.",
        });
      }
      receivedImportsSnapshot.current = true;
      importsWereBusy.current = busy;
      setImportsBusy(busy);
    });

    const unsubscribeClustering = subscribeToTopic<AutoClusterTaskState>(
      "autocluster",
      (state) => {
        const task = state.task;
        const busy = Boolean(task && (task.status === "queued" || task.status === "running"));
        if (
          receivedClusteringSnapshot.current &&
          clusteringWasBusy.current &&
          !busy &&
          task?.status === "completed"
        ) {
          setNotice({
            id: Date.now(),
            title: "Gesichtsgruppen wurden aktualisiert",
            message:
              "Gesichter können jetzt anders gruppiert oder sortiert sein. Die geöffneten Ansichten wurden automatisch neu geladen.",
          });
        }
        if (task?.status === "failed" && clusteringWasBusy.current) {
          setNotice({
            id: Date.now(),
            title: "Gruppierung nicht abgeschlossen",
            message:
              task.last_error ||
              "Die bisherigen Zuordnungen bleiben erhalten. Du kannst den Vorgang später erneut starten.",
          });
        }
        receivedClusteringSnapshot.current = true;
        clusteringWasBusy.current = busy;
        setClusteringBusy(busy);
      },
    );

    const handleBlockedOperation = (event: Event) => {
      const message = (event as CustomEvent<string>).detail;
      setNotice({
        id: Date.now(),
        title: "Aktion sicher angehalten",
        message,
      });
    };
    window.addEventListener(OPERATION_BLOCKED_EVENT, handleBlockedOperation);

    return () => {
      unsubscribeImports();
      unsubscribeClustering();
      window.removeEventListener(OPERATION_BLOCKED_EVENT, handleBlockedOperation);
    };
  }, []);

  // Background work no longer blocks anything: an interactive change simply
  // asks a running clustering pass to step aside. So this is pure information.
  const activityMessage = clusteringBusy
    ? "Die Gruppen werden im Hintergrund aktualisiert. Du kannst ganz normal weiterarbeiten – deine Änderungen haben immer Vorrang."
    : importsBusy
      ? "Bilder werden im Hintergrund hinzugefügt. Du kannst ganz normal weiterarbeiten."
      : null;

  if (!activityMessage && !notice) return null;

  return (
    <div className="activity-safety-stack" aria-live="polite">
      {activityMessage && (
        <section className="activity-safety-banner activity-safety-banner--busy">
          <span className="activity-safety-banner__pulse" aria-hidden="true" />
          <div>
            <strong>Hintergrundarbeit läuft</strong>
            <p>{activityMessage}</p>
          </div>
        </section>
      )}
      {notice && (
        <section key={notice.id} className="activity-safety-banner activity-safety-banner--notice">
          <div>
            <strong>{notice.title}</strong>
            <p>{notice.message}</p>
          </div>
          <button type="button" onClick={() => setNotice(null)} aria-label="Hinweis schließen">
            ×
          </button>
        </section>
      )}
    </div>
  );
};

export default BackgroundActivityNotice;
