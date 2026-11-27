import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  fetchFaceActionHistory, replayFaceAction, type FaceActionHistory as History, type FaceHistoryAction,
} from "../../utils/api";
import { useModalFocus } from "../../hooks/useModalFocus";
import { subscribeToTopic } from "../../utils/events";
import { countWithLabel } from "../../utils/germanFormatting";

const dateLabel = new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" });

export function describeFaceAction(action: FaceHistoryAction): string {
  const count = countWithLabel(action.face_count, "Gesicht", "Gesichter");
  if (action.action === "assign_person") return `${count} „${action.person_name ?? "Person"}“ zugeordnet`;
  return action.action === "mark_unknown_person"
    ? `${count} als unbekannt aussortiert`
    : `${count} als „Kein Gesicht“ aussortiert`;
}

export default function FaceActionHistory({ active, busy, compact = false, revision, onBusy, onChanged }: {
  active: boolean;
  busy: boolean;
  compact?: boolean;
  revision: number;
  onBusy: (busy: boolean) => void;
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const dialog = useRef<HTMLElement>(null);
  useModalFocus(dialog, () => setOpen(false), !busy, open && active);
  useEffect(() => { if (!active) setOpen(false); }, [active]);
  const [history, setHistory] = useState<History>();
  const [loadError, setLoadError] = useState("");
  const [feedback, setFeedback] = useState<{ message: string; error?: boolean }>();
  const requestRef = useRef<AbortController>();
  const replaying = useRef(false);
  const undoButton = useRef<HTMLButtonElement>(null);
  const redoButton = useRef<HTMLButtonElement>(null);
  const reload = useCallback(async () => {
    const request = new AbortController();
    requestRef.current?.abort();
    requestRef.current = request;
    try {
      const current = await fetchFaceActionHistory(request.signal);
      if (request.signal.aborted) return;
      setHistory(current);
      setLoadError("");
    } catch (error) {
      if (!request.signal.aborted) {
        setHistory(undefined);
        setLoadError(error instanceof Error ? error.message : "Änderungen konnten nicht geladen werden.");
      }
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    void reload();
    const unsubscribe = subscribeToTopic<{ reason?: string }>("clusters", (event) => {
      if (event?.reason === "database_import") {
        setHistory(undefined);
        setFeedback(undefined);
      }
      if (event?.reason === "database_import" || event?.reason?.startsWith("faces_batch:") ||
          event?.reason === "undo_face_action" || event?.reason === "redo_face_action") {
        void reload();
      }
    });
    return () => {
      unsubscribe();
      requestRef.current?.abort();
    };
  }, [active, reload, revision]);

  const replay = async (mode: "undo" | "redo") => {
    const action = history?.[mode];
    if (!history || !action || busy || replaying.current) return;
    replaying.current = true;
    const triggeredBy = mode === "undo" ? undoButton.current : redoButton.current;
    const hadFocus = document.activeElement === triggeredBy;
    onBusy(true);
    setFeedback(undefined);
    try {
      const result = await replayFaceAction(mode, history, action);
      requestRef.current?.abort();
      setHistory(result.history);
      await onChanged();
      setFeedback({ message: `${mode === "undo" ? "Rückgängig gemacht" : "Wiederholt"}: ${describeFaceAction(result.action)}.` });
      if (hadFocus) window.requestAnimationFrame(() => {
        if (!document.activeElement || document.activeElement === triggeredBy || document.activeElement === document.body) {
          (mode === "undo" ? redoButton.current : undoButton.current)?.focus({ preventScroll: true });
        }
      });
    } catch (error) {
      setFeedback({ message: error instanceof Error ? error.message : "Die Änderung konnte nicht übernommen werden.", error: true });
      await reload();
    } finally {
      replaying.current = false;
      onBusy(false);
    }
  };

  if (!history && !loadError && !feedback) return null;
  const controls = (history?.undo || history?.redo) && <>
    <button ref={undoButton} type="button" className="secondary-button" disabled={busy || !history.undo}
      title={history.undo ? describeFaceAction(history.undo) : undefined} onClick={() => void replay("undo")}>Rückgängig</button>
    <button ref={redoButton} type="button" className="secondary-button" disabled={busy || !history.redo}
      title={history.redo ? describeFaceAction(history.redo) : undefined} onClick={() => void replay("redo")}>Wiederholen</button>
  </>;
  return <section className="face-action-history" aria-label="Änderungsverlauf">
    <div className="face-action-history__tools">
      {!compact && controls}
      {history && <button type="button" className="secondary-button face-action-history__open" onClick={() => setOpen(true)}>
        Änderungsverlauf
      </button>}
    </div>
    {history && open && <div className="modal-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) setOpen(false);
    }}>
      <section className="face-action-history__dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialog}>
        <header>
          <h2 id={titleId}>Änderungsverlauf</h2>
          <button className="secondary-button" type="button" onClick={() => setOpen(false)} disabled={busy}>Schließen</button>
        </header>
        {compact && controls && <div className="face-action-history__tools">{controls}</div>}
        {history.blocked_reason ? <p>{history.blocked_reason}</p> : history.entries.length > 0 ? <ol>
          {history.entries.map((entry) => <li key={entry.id}>
            <span>{describeFaceAction(entry)}{entry.undone ? " · rückgängig gemacht" : ""}</span>
            <small>{dateLabel.format(new Date(entry.created_at))}</small>
          </li>)}
        </ol> : <p>Noch keine umkehrbaren Änderungen.</p>}
        {feedback && <p role={feedback.error ? "alert" : "status"}>{feedback.message}</p>}
      </section>
    </div>}
    {loadError && <div className="review-workflow-feedback" role="alert">{loadError}{" "}
      <button type="button" className="secondary-button" onClick={() => void reload()}>Erneut laden</button>
    </div>}
    {feedback && !open && <p className={`review-workflow-feedback${feedback.error ? " review-workflow-feedback--error" : ""}`}
      role={feedback.error ? "alert" : "status"}>{feedback.message}</p>}
  </section>;
}
