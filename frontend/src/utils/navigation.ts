import type { FaceReviewGroupKey } from "./api";

export type AppPage = "people" | "renaming" | "review" | "settings";
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
  personName?: string | null;
  settingsSection?: SettingsSection;
}

const PAGE_SEGMENTS: Record<AppPage, string> = {
  people: "bilder",
  renaming: "dateinamen",
  review: "gesichter-pruefen",
  settings: "einstellungen",
};

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
  if (entry.page === "review" && entry.clusterId !== undefined) {
    return `${base}/gruppe/${entry.clusterId}`;
  }
  if (entry.page === "review" && entry.groupKey !== undefined) {
    return `${base}/kategorie/${entry.groupKey}`;
  }
  if (entry.page === "settings" && entry.settingsSection !== undefined) {
    return `${base}/${entry.settingsSection}`;
  }
  return base;
}

/** Restore a useful view after refresh or a directly opened hash URL. */
export function parseNavigationHash(hash: string): NavigationEntry {
  const segments = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const page = PAGES_BY_SEGMENT[segments[0]];
  if (!page) return { page: "people" };

  if (page === "review" && segments[1] === "gruppe") {
    const clusterId = Number.parseInt(segments[2] ?? "", 10);
    if (Number.isSafeInteger(clusterId) && clusterId > 0) {
      return { page, clusterId };
    }
  }
  if (page === "review" && segments[1] === "kategorie") {
    const groupKey = segments[2] as FaceReviewGroupKey;
    if (REVIEW_GROUPS.has(groupKey)) return { page, groupKey };
  }
  if (page === "settings") {
    const settingsSection = segments[1] as SettingsSection;
    if (SETTINGS_SECTIONS.has(settingsSection)) return { page, settingsSection };
  }
  return { page };
}
