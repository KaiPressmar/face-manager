import { eventsUrl, invalidateLibraryMediaUrls } from "./api";

/**
 * Live backend event channel over Server-Sent Events.
 *
 * A single shared `EventSource` is opened lazily on the first subscription and
 * closed when the last subscriber leaves. This replaces the per-component
 * polling loops: the server pushes the latest snapshot for every topic on
 * connect and again whenever state changes, and `EventSource` reconnects
 * automatically if the connection drops.
 */

export type EventTopic =
  | "imports"
  | "autocluster"
  | "thumbnail-warmup"
  | "background-activities"
  | "image-path-cleanup"
  | "update-download"
  | "person-suggestions"
  | "clusters";

export type ConnectionStatus = "connecting" | "open" | "closed";

type TopicHandler = (data: unknown) => void;
type StatusHandler = (status: ConnectionStatus) => void;

const topicHandlers = new Map<EventTopic, Set<TopicHandler>>();
const statusHandlers = new Set<StatusHandler>();
const libraryHintHandlers = new Set<(id: string) => void>();
const verifiedLibraryHandlers = new Set<(id: string) => void>();

let source: EventSource | null = null;
let status: ConnectionStatus = "closed";
let currentLibraryId: string | undefined;
let verifiedLibraryId: string | undefined;

const ALL_TOPICS: EventTopic[] = [
  "imports",
  "autocluster",
  "thumbnail-warmup",
  "background-activities",
  "image-path-cleanup",
  "update-download",
  "person-suggestions",
  "clusters",
];

function setStatus(next: ConnectionStatus) {
  if (status === next) return;
  status = next;
  for (const handler of statusHandlers) {
    handler(next);
  }
}

function notifyTopic(topic: EventTopic, payload: unknown) {
  if (topic === "clusters" && (payload as { reason?: string } | null)?.reason === "database_import") {
    invalidateLibraryMediaUrls();
  }
  for (const handler of topicHandlers.get(topic) ?? []) {
    handler(payload);
  }
}

function dispatch(topic: EventTopic, event: MessageEvent) {
  let parsed: unknown;
  try { parsed = JSON.parse(event.data); } catch { return; }
  const update = parsed as { library_id?: unknown; reason?: string } | null;
  if (topic === "clusters" && typeof update?.library_id === "string" && update.library_id) {
    if (update.library_id !== verifiedLibraryId) {
      // A retained or queued event may describe a previously unseen old library.
      for (const hint of libraryHintHandlers) hint(update.library_id);
      return;
    }
    if (update.reason === "database_import") return; // Already announced by authoritative verification.
  }
  notifyTopic(topic, parsed);
}

export function seedWorkspaceLibraryId(id?: string) { currentLibraryId ??= id; }

export function publishWorkspaceLibraryId(id: string) {
  const changed = Boolean(currentLibraryId && currentLibraryId !== id);
  currentLibraryId = verifiedLibraryId = id;
  notifyTopic("clusters", { library_id: id, reason: changed ? "database_import" : "library_identity" });
  for (const handler of verifiedLibraryHandlers) handler(id);
}

export function subscribeToLibraryIdentityHints(handler: (id: string) => void) {
  libraryHintHandlers.add(handler);
  return () => { libraryHintHandlers.delete(handler); };
}

export function subscribeToVerifiedLibraryIdentity(handler: (id: string) => void) {
  verifiedLibraryHandlers.add(handler);
  return () => { verifiedLibraryHandlers.delete(handler); };
}

function ensureConnection() {
  if (source) return;
  setStatus("connecting");
  const connection = new EventSource(eventsUrl());
  source = connection;
  connection.onopen = () => { if (source === connection) setStatus("open"); };
  connection.onerror = () => {
    if (source !== connection) return;
    // EventSource retries on its own; surface the interim disconnected state.
    setStatus(connection.readyState === EventSource.CONNECTING ? "connecting" : "closed");
  };
  for (const topic of ALL_TOPICS) {
    connection.addEventListener(topic, (event) => {
      if (source === connection) dispatch(topic, event as MessageEvent);
    });
  }
}

function closeConnectionIfIdle() {
  const hasSubscribers = ALL_TOPICS.some(
    (topic) => (topicHandlers.get(topic)?.size ?? 0) > 0,
  );
  if (hasSubscribers || !source) return;
  source.close();
  source = null;
  setStatus("closed");
}

/**
 * Subscribe to live updates for one topic.
 *
 * @param topic - Channel to listen on.
 * @param handler - Called with the parsed payload on connect and each update.
 * @returns Unsubscribe function; closes the shared connection when the last
 *   subscriber across all topics leaves.
 */
export function subscribeToTopic<T = unknown>(
  topic: EventTopic,
  handler: (data: T) => void,
): () => void {
  let handlers = topicHandlers.get(topic);
  if (!handlers) {
    handlers = new Set();
    topicHandlers.set(topic, handlers);
  }
  const wrapped: TopicHandler = (data) => handler(data as T);
  handlers.add(wrapped);
  ensureConnection();

  return () => {
    const set = topicHandlers.get(topic);
    set?.delete(wrapped);
    closeConnectionIfIdle();
  };
}

/**
 * Observe the shared connection status, e.g. to show an "offline" hint.
 *
 * @param handler - Called immediately with the current status and on changes.
 * @returns Unsubscribe function.
 */
export function subscribeToConnectionStatus(
  handler: StatusHandler,
): () => void {
  statusHandlers.add(handler);
  handler(status);
  return () => {
    statusHandlers.delete(handler);
  };
}
