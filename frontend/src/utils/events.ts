import { eventsUrl } from "./api";

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
  | "clusters";

export type ConnectionStatus = "connecting" | "open" | "closed";

type TopicHandler = (data: unknown) => void;
type StatusHandler = (status: ConnectionStatus) => void;

const topicHandlers = new Map<EventTopic, Set<TopicHandler>>();
const statusHandlers = new Set<StatusHandler>();

let source: EventSource | null = null;
let status: ConnectionStatus = "closed";

const ALL_TOPICS: EventTopic[] = [
  "imports",
  "autocluster",
  "thumbnail-warmup",
  "clusters",
];

function setStatus(next: ConnectionStatus) {
  if (status === next) return;
  status = next;
  for (const handler of statusHandlers) {
    handler(next);
  }
}

function dispatch(topic: EventTopic, event: MessageEvent) {
  const handlers = topicHandlers.get(topic);
  if (!handlers || handlers.size === 0) return;
  let payload: unknown;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return;
  }
  for (const handler of handlers) {
    handler(payload);
  }
}

function ensureConnection() {
  if (source) return;
  setStatus("connecting");
  source = new EventSource(eventsUrl());
  source.onopen = () => setStatus("open");
  source.onerror = () => {
    // EventSource retries on its own; surface the interim disconnected state.
    setStatus(source?.readyState === EventSource.CONNECTING ? "connecting" : "closed");
  };
  for (const topic of ALL_TOPICS) {
    source.addEventListener(topic, (event) =>
      dispatch(topic, event as MessageEvent),
    );
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
