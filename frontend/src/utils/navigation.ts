import type { FaceReviewGroupKey } from "./api";
import type { GeoPhotoSelection } from "./geoPhotoSelection";

export type AppPage = "people" | "map" | "renaming" | "review" | "settings";
export type ReviewView = "suggestions" | "open" | "people" | "archive" | "shared";
export type SettingsSection =
  | "erkennung"
  | "dateinamen"
  | "darstellung"
  | "updates"
  | "daten";

export interface NavigationEntry {
  page: AppPage;
  clusterId?: number;
  groupKey?: FaceReviewGroupKey;
  archiveClusterId?: number;
  personName?: string | null;
  reviewView?: ReviewView;
  settingsSection?: SettingsSection;
  geoSelection?: GeoPhotoSelection;
  geoSelectionEpoch?: number;
  mapAlbumId?: string;
}

const PAGE_SEGMENTS: Record<AppPage, string> = {
  people: "bilder",
  map: "weltkarte",
  renaming: "dateinamen",
  review: "gesichter-pruefen",
  settings: "einstellungen",
};

const REVIEW_SEGMENTS: Record<ReviewView, string> = {
  suggestions: "vorschlaege", open: "ohne-namen", people: "personen", archive: "archiv", shared: "abgleich",
};

export interface WorkspaceHistoryMarker {
  id: string;
  position: number;
  generation: number;
  libraryId?: string;
  hasForward?: boolean;
  nativeForwardEntries?: string[];
}

export const MAX_HISTORY_PHOTO_IDS = 200;
export const MAX_NATIVE_FORWARD_ENTRIES = 32;

export function workspaceHistoryMarker(state: unknown): WorkspaceHistoryMarker | undefined {
  const marker = (state as { faceManagerWorkspace?: WorkspaceHistoryMarker } | null)?.faceManagerWorkspace;
  if (marker && typeof marker.id === "string" && marker.id && Number.isSafeInteger(marker.position)
    && marker.position >= 0 && Number.isSafeInteger(marker.generation) && marker.generation >= 0
    && (marker.hasForward === undefined || typeof marker.hasForward === "boolean")
    && (marker.nativeForwardEntries === undefined || (Array.isArray(marker.nativeForwardEntries)
      && marker.nativeForwardEntries.length <= MAX_NATIVE_FORWARD_ENTRIES
      && Array.from(marker.nativeForwardEntries).every((id) => typeof id === "string" && id)))) return marker;
  return undefined;
}

export function workspaceHistoryState(entry: NavigationEntry, marker: WorkspaceHistoryMarker) {
  const { geoSelection, geoSelectionEpoch: _oldEpoch, ...durable } = entry;
  if (!geoSelection) return { ...durable, faceManagerWorkspace: marker };
  const { image_ids, ...filters } = geoSelection;
  const limited = Boolean(image_ids && image_ids.length > MAX_HISTORY_PHOTO_IDS);
  const savedSelection = limited ? filters : geoSelection;
  const selection = Object.values(savedSelection).some((value) => value !== undefined)
    ? { geoSelection: savedSelection, geoSelectionEpoch: marker.generation } : {};
  // Keep large explicit ID sets in the live entry. Ordinary filters and
  // bounded lists are durable for browser Back, Forward and reload.
  return { ...durable, ...selection, faceManagerWorkspace: marker, ...(limited ? { faceManagerSelectionLimited: true } : {}) };
}

export function workspaceSelectionLimited(state: unknown) {
  return (state as { faceManagerSelectionLimited?: boolean } | null)?.faceManagerSelectionLimited === true;
}

const PAGES_BY_SEGMENT = Object.fromEntries(
  Object.entries(PAGE_SEGMENTS).map(([page, segment]) => [segment, page]),
) as Record<string, AppPage>;

const REVIEW_GROUPS = new Set<FaceReviewGroupKey>([
  "unassigned",
  "unknown_person",
  "not_face",
]);

const SETTINGS_SECTIONS = new Set<SettingsSection>([
  "erkennung",
  "dateinamen",
  "darstellung",
  "updates",
  "daten",
]);

/** Build a static-host-safe URL. Hash routes are never sent to the web server. */
export function navigationHash(entry: NavigationEntry): string {
  const base = `#/${PAGE_SEGMENTS[entry.page]}`;
  if (entry.page === "review" && entry.groupKey !== undefined) {
    const group = `${base}/kategorie/${entry.groupKey}`;
    return entry.archiveClusterId === undefined ? group : `${group}/gruppe/${entry.archiveClusterId}`;
  }
  if (entry.page === "review" && entry.clusterId !== undefined) return `${base}/gruppe/${entry.clusterId}`;
  if (entry.page === "review" && entry.reviewView) return `${base}/${REVIEW_SEGMENTS[entry.reviewView]}`;
  if (entry.page === "settings" && entry.settingsSection !== undefined) {
    return `${base}/${entry.settingsSection}`;
  }
  if (entry.page === "people" && entry.geoSelection) return `${base}/auswahl`;
  if (entry.page === "map" && entry.mapAlbumId) return `${base}/album/${entry.mapAlbumId}`;
  return base;
}

function positiveId(raw?: string) {
  const id = raw && /^\d+$/.test(raw) ? Number(raw) : NaN;
  return Number.isSafeInteger(id) && id > 0 ? id : undefined;
}

/** Restore a useful view after refresh or a directly opened hash URL. */
export function parseNavigationHash(hash: string, state?: NavigationEntry | null): NavigationEntry {
  const segments = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (!Object.prototype.hasOwnProperty.call(PAGES_BY_SEGMENT, segments[0])) return { page: "people" };
  const page = PAGES_BY_SEGMENT[segments[0]];

  // Exact selections can include thousands of photo IDs. Keep the selection
  // in browser history state so neither the URL nor a copied link contains it.
  if (page === "people" && segments[1] === "auswahl" && state?.page === "people" && state.geoSelection) {
    return { page, geoSelection: state.geoSelection, geoSelectionEpoch: state.geoSelectionEpoch };
  }
  if (page === "map" && segments[1] === "album" && /^[a-f0-9]{32}$/.test(segments[2] ?? "")) {
    return { page, mapAlbumId: segments[2] };
  }

  if (page === "review" && segments[1] === "gruppe") {
    const clusterId = positiveId(segments[2]);
    if (clusterId !== undefined) {
      return { page, clusterId };
    }
  }
  if (page === "review" && segments[1] === "kategorie") {
    const groupKey = segments[2] as FaceReviewGroupKey;
    if (REVIEW_GROUPS.has(groupKey)) {
      const archiveClusterId = segments[3] === "gruppe" ? positiveId(segments[4]) : undefined;
      return archiveClusterId === undefined ? { page, groupKey } : { page, groupKey, archiveClusterId };
    }
  }
  if (page === "review") {
    const match = (Object.keys(REVIEW_SEGMENTS) as ReviewView[]).find((view) => REVIEW_SEGMENTS[view] === segments[1]);
    if (match) return { page, reviewView: match };
  }
  if (page === "settings") {
    const settingsSection = segments[1] as SettingsSection;
    if (SETTINGS_SECTIONS.has(settingsSection)) return { page, settingsSection };
  }
  return { page };
}
