import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchLibraryIdentity } from "../utils/api";
import {
  publishWorkspaceLibraryId, seedWorkspaceLibraryId, subscribeToConnectionStatus, subscribeToLibraryIdentityHints,
  subscribeToTopic, subscribeToVerifiedLibraryIdentity,
} from "../utils/events";
import {
  MAX_NATIVE_FORWARD_ENTRIES, navigationHash, parseNavigationHash, workspaceHistoryMarker, workspaceHistoryState, workspaceSelectionLimited,
  type AppPage, type NavigationEntry, type WorkspaceHistoryMarker,
} from "../utils/navigation";
import { closeTopNavigationDialog, topNavigationDialog } from "../utils/navigationOverlays";
import type { WorkspaceBinding, WorkspaceNavigationContextValue } from "./useWorkspaceNavigation";

const OLD_LIBRARY_NOTICE = "Dieser Verlaufseintrag gehört zur vorherigen Bibliothek. Die Übersicht ist geöffnet.";
const LIMITED_SELECTION_NOTICE = "Die genaue Fotoauswahl war zu groß, um sie nach dem Neuladen wiederherzustellen.";

interface RecordEntry extends WorkspaceHistoryMarker { entry: NavigationEntry; hasForward: boolean }
interface FocusAddress { image?: string; id?: string; label?: string; name?: string; text?: string }
interface Snapshot { state: unknown; top: number; left: number; focus?: FocusAddress }
interface NavigateOptions { replace?: boolean; preserveWorkspace?: boolean }

function entryId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function paneContent(page: AppPage) {
  return document.querySelector<HTMLElement>(`[data-workspace-page="${page}"] > .page-content`);
}

function writeEntry(record: RecordEntry, replace: boolean) {
  const { entry, ...marker } = record;
  const state = workspaceHistoryState(entry, marker);
  const hash = navigationHash(entry);
  if (replace) window.history.replaceState(state, "", hash);
  else window.history.pushState(state, "", hash);
}

function markForwardEntry() {
  const state = window.history.state;
  const marker = workspaceHistoryMarker(state);
  if (marker && !marker.hasForward) {
    window.history.replaceState({ ...state, faceManagerWorkspace: { ...marker, hasForward: true } }, "");
  }
}

function snapshotNativeForwardEntries(entries: Set<string>) {
  for (const id of entries) {
    if (entries.size <= MAX_NATIVE_FORWARD_ENTRIES) break;
    entries.delete(id);
  }
  return [...entries];
}

function focusAddress(pane: HTMLElement | null) {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement) || !pane?.contains(active) || active.closest('[role="dialog"][aria-modal="true"]')) return undefined;
  const text = active.matches("button, a[href]") ? active.textContent?.trim().replace(/\s+/g, " ") : undefined;
  return {
    image: active.closest<HTMLElement>("[data-image-id]")?.dataset.imageId,
    id: active.id || undefined,
    label: active.getAttribute("aria-label") || undefined,
    name: active.getAttribute("name") || undefined,
    text: text && text.length <= 100 ? text : undefined,
  };
}

function restoreFocus(pane: HTMLElement, focus?: Snapshot["focus"]) {
  if (topNavigationDialog()) return;
  const image = focus?.image ? pane.querySelector<HTMLElement>(`[data-image-id="${CSS.escape(focus.image)}"]`) : null;
  const scope = image ?? pane;
  const id = focus?.id ? pane.querySelector<HTMLElement>(`#${CSS.escape(focus.id)}`) : null;
  const label = focus?.label ? scope.querySelector<HTMLElement>(`[aria-label="${CSS.escape(focus.label)}"]`) : null;
  const name = focus?.name ? scope.querySelector<HTMLElement>(`[name="${CSS.escape(focus.name)}"]`) : null;
  const text = focus?.text ? [...scope.querySelectorAll<HTMLElement>("button, a[href]")]
    .find((element) => element.textContent?.trim().replace(/\s+/g, " ") === focus.text) : undefined;
  const destination = [id, label, name, text, image?.querySelector<HTMLElement>("button, [tabindex], a[href]")]
    .find((element) => element && element.getClientRects().length && !element.matches(":disabled"))
    ?? pane.querySelector<HTMLElement>("h1") ?? pane;
  if (destination.tabIndex < 0) destination.tabIndex = -1;
  destination.focus({ preventScroll: true });
}

export function useAppNavigation(initialEntry: NavigationEntry, applyEntry: (entry: NavigationEntry) => void,
  onLibraryReplaced: () => void) {
  const [initial] = useState<RecordEntry>(() => {
    const marker = workspaceHistoryMarker(window.history.state);
    seedWorkspaceLibraryId(marker?.libraryId);
    return { entry: initialEntry, id: marker?.id ?? entryId(), position: marker?.position ?? 0,
      generation: marker?.generation ?? 0, libraryId: marker?.libraryId, hasForward: marker?.hasForward ?? false,
      nativeForwardEntries: marker?.nativeForwardEntries };
  });
  const libraryId = useRef(initial.libraryId);
  const verifiedLibraryId = useRef<string>();
  const [ready, setReady] = useState(!initial.libraryId);
  const current = useRef(initial);
  const generation = useRef(initial.generation);
  const [record, setRecord] = useState(initial);
  const [notice, setNotice] = useState(() => workspaceSelectionLimited(window.history.state) ? LIMITED_SELECTION_NOTICE : undefined);
  const furthestPosition = useRef(initial.position + Number(initial.hasForward));
  const nativeForwardEntries = useRef(new Set(initial.nativeForwardEntries));
  const known = useRef(new Map([[initial.position, initial]]));
  const snapshots = useRef(new Map<string, Snapshot>());
  const lastFocused = useRef(new Map<string, FocusAddress>());
  const latestByPage = useRef(new Map<AppPage, RecordEntry>());
  const bindings = useRef(new Map<AppPage, WorkspaceBinding>());
  const pending = useRef<{ id: string; snapshot?: Snapshot }>();
  const cursorReturn = useRef<string>();
  const deferredLibraryUpdate = useRef<() => void>();
  const presentation = useRef(0);

  const captureCurrent = useCallback(() => {
    const origin = current.current;
    const pane = paneContent(origin.entry.page);
    const previous = snapshots.current.get(origin.id);
    const binding = bindings.current.get(origin.entry.page);
    snapshots.current.set(origin.id, {
      state: binding ? binding.capture() : previous?.state,
      top: pane?.scrollTop ?? previous?.top ?? 0,
      left: pane?.scrollLeft ?? previous?.left ?? 0,
      focus: focusAddress(pane) ?? lastFocused.current.get(origin.id) ?? previous?.focus,
    });
    latestByPage.current.set(origin.entry.page, origin);
  }, []);

  const discardForward = useCallback((position: number) => {
    for (const [index, old] of known.current) {
      if (index < position) continue;
      if (latestByPage.current.get(old.entry.page)?.id !== old.id) snapshots.current.delete(old.id);
      lastFocused.current.delete(old.id);
      nativeForwardEntries.current.delete(old.id);
      known.current.delete(index);
    }
    furthestPosition.current = position;
  }, []);

  const activate = useCallback((next: RecordEntry, restore: boolean, snapshot?: Snapshot, updateRoute = true) => {
    const serial = ++presentation.current;
    const focusAtActivation = document.activeElement;
    current.current = next;
    known.current.set(next.position, next);
    setRecord(next);
    if (updateRoute) applyEntry(next.entry);
    pending.current = undefined;
    if (!restore) return;
    const binding = bindings.current.get(next.entry.page);
    if (binding) binding.restore(snapshot?.state);
    else pending.current = { id: next.id, snapshot };

    // Data-dependent pages may need several frames to rebuild the old scroll range.
    let tries = 0;
    const positionPane = () => {
      if (presentation.current !== serial) return;
      const pane = paneContent(next.entry.page);
      if (pane?.parentElement?.classList.contains("page-pane--active")) {
        pane.scrollTop = snapshot?.top ?? 0;
        pane.scrollLeft = snapshot?.left ?? 0;
        if (Math.abs(pane.scrollTop - (snapshot?.top ?? 0)) <= 2 || tries >= 45) {
          // A route can paint before its scroll position is restored. Keep
          // focus where the user moved it during those frames.
          const focused = document.activeElement;
          if (!focused || focused === document.body || focused === focusAtActivation) {
            restoreFocus(pane, snapshot?.focus);
          }
          return;
        }
      }
      if (++tries <= 45) window.requestAnimationFrame(positionPane);
    };
    window.requestAnimationFrame(() => window.requestAnimationFrame(positionPane));
  }, [applyEntry]);

  const addEntry = useCallback((entry: NavigationEntry, options: NavigateOptions = {}, snapshot?: Snapshot, updateRoute = true) => {
    if (cursorReturn.current) return;
    const previous = current.current;
    captureCurrent();
    const nextPosition = options.replace ? previous.position : previous.position + 1;
    if (!options.replace) {
      markForwardEntry();
      nativeForwardEntries.current.delete(previous.id);
      discardForward(nextPosition);
    }
    const next: RecordEntry = {
      entry, generation: generation.current,
      libraryId: libraryId.current,
      id: options.replace ? previous.id : entryId(),
      position: nextPosition,
      hasForward: options.replace ? previous.hasForward : false,
      nativeForwardEntries: snapshotNativeForwardEntries(nativeForwardEntries.current),
    };
    if (!options.replace && options.preserveWorkspace) {
      const oldFocus = lastFocused.current.get(previous.id);
      if (oldFocus) lastFocused.current.set(next.id, oldFocus);
    }
    writeEntry(next, options.replace ?? false);
    setNotice(undefined);
    activate(next, !options.preserveWorkspace, snapshot, updateRoute);
  }, [activate, captureCurrent, discardForward]);

  const navigate = useCallback((entry: NavigationEntry, options?: NavigateOptions) => {
    addEntry(entry, options);
  }, [addEntry]);

  const resume = useCallback((page: AppPage) => {
    if (current.current.entry.page === page) return;
    const last = latestByPage.current.get(page);
    const overview: NavigationEntry = page === "review" ? { page, reviewView: "directory" } : { page };
    addEntry(last?.entry ?? overview, {}, last ? snapshots.current.get(last.id) : undefined);
  }, [addEntry]);

  const reset = useCallback(() => {
    const { page, reviewView, personId, proposal } = current.current.entry;
    if (!closeTopNavigationDialog()) {
      addEntry(page === "review" && (reviewView === "directory" || personId !== undefined || proposal)
        ? { page, reviewView: "directory" } : { page });
    }
  }, [addEntry]);

  const workspace = useMemo<WorkspaceNavigationContextValue>(() => ({
    register(page, binding) {
      bindings.current.set(page, binding);
      const deferred = pending.current;
      if (deferred?.id === current.current.id && current.current.entry.page === page) {
        pending.current = undefined;
        binding.restore(deferred.snapshot?.state);
      }
      return () => { if (bindings.current.get(page) === binding) bindings.current.delete(page); };
    },
    checkpoint(page) {
      if (current.current.entry.page === page) {
        addEntry(current.current.entry, { preserveWorkspace: true }, undefined, false);
      }
    },
    replaceWithOverview(page) {
      if (current.current.entry.page === page && navigationHash(current.current.entry) !== navigationHash({ page })) {
        addEntry({ page }, { replace: true, preserveWorkspace: true });
        setNotice("Diese Auswahl ist nicht mehr verfügbar. Die Übersicht ist geöffnet.");
      }
    },
  }), [addEntry]);

  const step = useCallback((direction: -1 | 1) => {
    if (cursorReturn.current || closeTopNavigationDialog()) return true;
    const active = current.current;
    if (direction === -1 ? !active.position : !active.hasForward) return false;
    window.history.go(direction);
    return true;
  }, []);

  const replaceLibrary = useCallback(() => {
    generation.current += 1;
    snapshots.current.clear();
    lastFocused.current.clear();
    latestByPage.current.clear();
    known.current.clear();
    const next = { ...current.current, entry: { page: current.current.entry.page }, generation: generation.current,
      libraryId: libraryId.current };
    for (const [page, binding] of bindings.current) {
      if (page !== next.entry.page) binding.restore(undefined);
    }
    writeEntry(next, true);
    activate(next, true);
    onLibraryReplaced();
    setNotice("Die Bibliothek wurde gewechselt. Die Ansicht wurde zurückgesetzt.");
  }, [activate, onLibraryReplaced]);

  useEffect(() => {
    let disposed = false;
    let request: AbortController | undefined;
    let retry: number | undefined;
    let delay = 400;
    let lastHint: string | undefined;
    const reconcile = (action: () => void) => {
      // A popstate consumed by an overlay briefly points at another entry.
      // Wait for the cursor to return before writing updated library identity.
      if (cursorReturn.current) deferredLibraryUpdate.current = action;
      else action();
    };
    const load = (restart = false) => {
      if (disposed || (request && !restart)) return;
      request?.abort();
      window.clearTimeout(retry);
      const pendingRequest = new AbortController();
      request = pendingRequest;
      const timeout = window.setTimeout(() => pendingRequest.abort(), 4000);
      void fetchLibraryIdentity(pendingRequest.signal).then((id) => {
        if (request === pendingRequest && !disposed) publishWorkspaceLibraryId(id);
      }).catch(() => {
        if (request !== pendingRequest || disposed) return;
        retry = window.setTimeout(load, delay);
        delay = Math.min(3000, delay * 2);
      }).finally(() => {
        window.clearTimeout(timeout);
        if (request === pendingRequest) request = undefined;
      });
    };
    const unsubscribeUpdates = subscribeToTopic<{ library_id?: string; reason?: string }>("clusters", (update) => {
      // Compatibility with older servers or synthetic events during tests.
      if (!update?.library_id && update?.reason === "database_import") reconcile(replaceLibrary);
    });
    const unsubscribeVerified = subscribeToVerifiedLibraryIdentity((id) => {
      const pendingRequest = request;
      request = undefined;
      pendingRequest?.abort();
      window.clearTimeout(retry);
      delay = 400;
      reconcile(() => {
        const active = current.current;
        libraryId.current = id;
        // A new direct URL without a bound identity is current user intent.
        if (active.libraryId && active.libraryId !== id) {
          replaceLibrary();
        } else if (active.libraryId !== id) {
          const confirmed = { ...active, libraryId: id };
          current.current = confirmed;
          known.current.set(confirmed.position, confirmed);
          writeEntry(confirmed, true);
          setRecord(confirmed);
        }
        if (verifiedLibraryId.current !== id) {
          verifiedLibraryId.current = id;
          setReady(true);
        }
      });
    });
    const unsubscribeHints = subscribeToLibraryIdentityHints((id) => {
      load(id !== lastHint);
      lastHint = id;
    });
    let hasOpened = false;
    const unsubscribeConnection = subscribeToConnectionStatus((status) => {
      if (status !== "open") return;
      load(hasOpened);
      hasOpened = true;
    });
    load();
    return () => {
      disposed = true;
      deferredLibraryUpdate.current = undefined;
      request?.abort();
      window.clearTimeout(retry);
      unsubscribeConnection();
      unsubscribeHints();
      unsubscribeVerified();
      unsubscribeUpdates();
    };
  }, [replaceLibrary]);

  useEffect(() => {
    if (!workspaceHistoryMarker(window.history.state)
      || window.location.hash !== navigationHash(current.current.entry)) writeEntry(current.current, true);
    const oldScrollRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    const restoreFromLocation = () => {
      const state = window.history.state;
      const marker = workspaceHistoryMarker(state);
      const hash = window.location.hash;
      const previous = current.current;
      if (cursorReturn.current) {
        if (marker?.id === cursorReturn.current) {
          cursorReturn.current = undefined;
          const update = deferredLibraryUpdate.current;
          deferredLibraryUpdate.current = undefined;
          update?.();
        }
        return;
      }
      if (marker?.id === previous.id && hash === navigationHash(previous.entry)) return;
      const offset = marker ? previous.position - marker.position : 0;
      if (offset && closeTopNavigationDialog()) {
        // Undo the browser cursor motion without pushing an entry. In particular,
        // closing an overlay while in the middle of history leaves Forward intact.
        cursorReturn.current = previous.id;
        window.history.go(offset);
        return;
      }
      captureCurrent();
      const knownDestination = marker ? known.current.get(marker.position) : undefined;
      const cached = knownDestination?.id === marker?.id ? knownDestination : undefined;
      const routed = cached && hash === navigationHash(cached.entry) ? cached.entry : parseNavigationHash(hash, state);
      const verifiedId = verifiedLibraryId.current;
      const boundId = marker?.libraryId;
      if (marker && boundId && !verifiedId) {
        setReady(false);
        if (!libraryId.current) {
          libraryId.current = boundId;
          generation.current = marker.generation;
          seedWorkspaceLibraryId(boundId);
        }
      }
      const expectedId = verifiedId ?? libraryId.current;
      const wrongGeneration = marker && marker.generation !== generation.current;
      const wrongIdentity = boundId && expectedId && boundId !== expectedId;
      const unverifiedSelection = routed.geoSelection && (!boundId || (!cached && routed.geoSelectionEpoch !== marker?.generation));
      const wrongLibrary = wrongGeneration || wrongIdentity || unverifiedSelection;
      const entry = wrongLibrary ? { page: routed.page } : routed;
      const position = marker?.position ?? previous.position + 1;
      const id = marker?.id ?? entryId();
      const savedNativeEntries = marker?.nativeForwardEntries ?? [];
      const nativeEntries = nativeForwardEntries.current;
      for (const savedId of savedNativeEntries) nativeEntries.add(savedId);
      const hasNativeForward = nativeEntries.delete(id);
      if (marker) {
        furthestPosition.current = Math.max(furthestPosition.current, previous.position,
          position + Number(marker.hasForward === true || hasNativeForward));
      } else {
        // A native hash change adds an entry before we can mark its predecessor.
        // Carry that entry's ID through history jumps until we can mark it in place.
        if (!previous.hasForward) nativeEntries.add(previous.id);
        discardForward(position);
      }
      const rememberedNativeEntries = snapshotNativeForwardEntries(nativeEntries);
      const nativeEntriesChanged = savedNativeEntries.length !== rememberedNativeEntries.length
        || savedNativeEntries.some((savedId, index) => savedId !== rememberedNativeEntries[index]);
      const next: RecordEntry = { entry, id, position,
        generation: generation.current, libraryId: expectedId,
        hasForward: position < furthestPosition.current, nativeForwardEntries: rememberedNativeEntries };
      if (!marker || wrongLibrary || marker.libraryId !== next.libraryId || marker.hasForward !== next.hasForward
        || nativeEntriesChanged || hash !== navigationHash(entry)) writeEntry(next, true);
      activate(next, true, wrongLibrary ? undefined : snapshots.current.get(next.id));
      setNotice(wrongLibrary ? OLD_LIBRARY_NOTICE : !cached && workspaceSelectionLimited(state) ? LIMITED_SELECTION_NOTICE : undefined);
    };
    const keyboard = (event: KeyboardEvent) => {
      if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
        && (event.key === "ArrowLeft" || event.key === "ArrowRight") && step(event.key === "ArrowLeft" ? -1 : 1)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    const rememberFocus = () => {
      const active = current.current;
      const focus = focusAddress(paneContent(active.entry.page));
      if (focus) lastFocused.current.set(active.id, focus);
    };
    const bridge = window as Window & { faceManagerBack?: () => boolean };
    bridge.faceManagerBack = () => step(-1);
    window.addEventListener("popstate", restoreFromLocation);
    window.addEventListener("hashchange", restoreFromLocation);
    window.addEventListener("keydown", keyboard, true);
    document.addEventListener("focusin", rememberFocus);
    return () => {
      ++presentation.current;
      delete bridge.faceManagerBack;
      window.history.scrollRestoration = oldScrollRestoration;
      window.removeEventListener("popstate", restoreFromLocation);
      window.removeEventListener("hashchange", restoreFromLocation);
      window.removeEventListener("keydown", keyboard, true);
      document.removeEventListener("focusin", rememberFocus);
    };
  }, [activate, captureCurrent, discardForward, step]);

  return {
    workspace, navigate, resume, reset, step, ready, notice, id: record.id,
    canBack: record.position > 0,
    canForward: record.hasForward,
    backPage: known.current.get(record.position - 1)?.entry.page,
    forwardPage: known.current.get(record.position + 1)?.entry.page,
  };
}
