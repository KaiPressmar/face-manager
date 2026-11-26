import React, { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import Layout from "./components/layout/Layout";
import PeoplePage from "./components/people/PeoplePage";
import ClusterPage from "./components/clusters/ClusterPage";
import ImageRenamePage from "./components/renaming/ImageRenamePage";
import SettingsPage from "./components/settings/SettingsPage";
import type { FaceReviewGroupKey } from "./utils/api";
import { useSharedLibrary } from "./hooks/useSharedLibrary";
import ErrorBoundary from "./components/shared/ErrorBoundary";
import { subscribeToTopic } from "./utils/events";
import { hasGeoPhotoSelection, type GeoPhotoSelection } from "./utils/geoPhotoSelection";
import WhatsNewModal from "./components/shared/WhatsNewModal";
import UpdateAvailableModal from "./components/shared/UpdateAvailableModal";
import {
  acknowledgeCurrentReleaseNotes,
  checkForUpdates,
  fetchFullChangelog,
  fetchUnseenReleaseNotes,
  type AvailableUpdate,
  type ReleaseNotes,
} from "./utils/api";
import {
  navigationHash,
  parseNavigationHash,
  type AppPage,
  type NavigationEntry,
  type SettingsSection,
} from "./utils/navigation";

export type { AppPage } from "./utils/navigation";

export interface ClusterNavigationTarget {
  clusterId?: number;
  groupKey?: FaceReviewGroupKey;
  reviewView?: "open" | "shared";
  /** Person of the clicked face, if known — lets the review page open the
   *  matching work area without waiting for its cluster list to load. */
  personName?: string | null;
  token: number;
}

const MapPage = lazy(() => import("./components/map/MapPage"));

const PAGE_ORDER: AppPage[] = ["people", "map", "renaming", "review", "settings"];
const UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000;
const GEO_SELECTION_EPOCH_KEY = "face-manager:geo-selection-epoch";

function readGeoSelectionEpoch() {
  try {
    return Number(window.sessionStorage.getItem(GEO_SELECTION_EPOCH_KEY)) || 0;
  } catch {
    return 0;
  }
}

function readNavigationEntry(geoSelectionEpoch: number): NavigationEntry {
  const entry = parseNavigationHash(window.location.hash, window.history.state);
  return entry.geoSelection && entry.geoSelectionEpoch !== geoSelectionEpoch ? { page: "people" } : entry;
}

type Changelog =
  | { status: "idle" | "loading" }
  | { status: "error"; message: string }
  | { status: "loaded"; releases: ReleaseNotes[] };

const PagePane: React.FC<{ active: boolean; children: React.ReactNode }> = ({
  active,
  children,
}) => {
  const applyPaneState = useCallback((pane: HTMLDivElement | null) => {
    if (pane) pane.inert = !active;
  }, [active]);
  return (
    <div
      ref={applyPaneState}
      className={`page-pane${active ? " page-pane--active" : ""}`}
      aria-hidden={!active}
    >
      <div className="page-content">{children}</div>
    </div>
  );
};

const App: React.FC = () => {
  const sharedLibrary = useSharedLibrary();
  const [initialGeoSelectionEpoch] = useState(readGeoSelectionEpoch);
  const geoSelectionEpoch = useRef(initialGeoSelectionEpoch);
  const [initialEntry] = useState(() => readNavigationEntry(initialGeoSelectionEpoch));
  const [page, setPage] = useState<AppPage>(() => initialEntry.page);
  const [geoSelection, setGeoSelection] = useState(initialEntry.geoSelection);
  const [clusterNavigationTarget, setClusterNavigationTarget] =
    useState<ClusterNavigationTarget | null>(() => {
      if (initialEntry.clusterId !== undefined) {
        return { clusterId: initialEntry.clusterId, token: Date.now() };
      }
      if (initialEntry.groupKey !== undefined) {
        return { groupKey: initialEntry.groupKey, token: Date.now() };
      }
      if (initialEntry.reviewView) return { reviewView: initialEntry.reviewView, token: Date.now() };
      return null;
    });
  const [settingsSection, setSettingsSection] = useState<SettingsSection | undefined>(
    () => initialEntry.settingsSection,
  );
  const [unseenReleases, setUnseenReleases] = useState<ReleaseNotes[]>([]);
  const [showReleaseNotes, setShowReleaseNotes] = useState(false);
  const [changelog, setChangelog] = useState<Changelog>({ status: "idle" });
  const changelogInFlight = useRef(false);
  const [showChangelog, setShowChangelog] = useState(false);
  const [availableUpdate, setAvailableUpdate] = useState<AvailableUpdate | null>(null);
  const [showUpdate, setShowUpdate] = useState(false);

  // Pages are mounted lazily on first visit and then kept alive, so returning
  // to a page is instant and preserves its scroll position and state instead
  // of refetching and remounting.
  const visitedRef = useRef<Set<AppPage>>(new Set([page]));
  visitedRef.current.add(page);

  // Hash routes survive refreshes and work with both Vite and the packaged
  // static frontend because the route fragment is never sent to the server.
  const lastAppliedHashRef = useRef(navigationHash(initialEntry));

  const applyEntry = useCallback((entry: NavigationEntry) => {
    if (entry.clusterId !== undefined) {
      setClusterNavigationTarget({
        clusterId: entry.clusterId,
        personName: entry.personName,
        token: Date.now(),
      });
    } else if (entry.groupKey !== undefined) {
      setClusterNavigationTarget({ groupKey: entry.groupKey, token: Date.now() });
    } else if (entry.reviewView) {
      setClusterNavigationTarget({ reviewView: entry.reviewView, token: Date.now() });
    } else {
      setClusterNavigationTarget(null);
    }
    setSettingsSection(entry.page === "settings" ? entry.settingsSection : undefined);
    if (entry.page === "people") setGeoSelection(entry.geoSelection);
    setPage(entry.page);
  }, []);

  const navigate = useCallback(
    (entry: NavigationEntry, replace = false) => {
      const nextEntry = entry.geoSelection ? { ...entry, geoSelectionEpoch: geoSelectionEpoch.current } : entry;
      applyEntry(nextEntry);
      const nextHash = navigationHash(nextEntry);
      lastAppliedHashRef.current = nextHash;
      if (replace || window.location.hash === nextHash) {
        window.history.replaceState(nextEntry, "", nextHash);
      } else {
        window.history.pushState(nextEntry, "", nextHash);
      }
    },
    [applyEntry],
  );

  useEffect(() => {
    const entry = readNavigationEntry(geoSelectionEpoch.current);
    const canonicalHash = navigationHash(entry);
    lastAppliedHashRef.current = canonicalHash;
    window.history.replaceState(entry, "", canonicalHash);
  }, []);

  useEffect(() => {
    const handleSetting = (event: Event) => {
      const enabled = (event as CustomEvent<boolean>).detail;
      if (!enabled) {
        setAvailableUpdate(null);
        setShowUpdate(false);
        return;
      }
      void checkForUpdates(true)
        .then((update) => {
          if (!update.update_available || update.skipped) return;
          setAvailableUpdate(update);
          setShowUpdate(true);
        })
        .catch(() => undefined);
    };
    window.addEventListener("face-manager:update-check-setting", handleSetting);
    return () => window.removeEventListener("face-manager:update-check-setting", handleSetting);
  }, []);

  useEffect(() => {
    const handleAvailableUpdate = (event: Event) => {
      const update = (event as CustomEvent<AvailableUpdate>).detail;
      if (!update?.update_available) return;
      setAvailableUpdate(update);
      setShowUpdate(true);
    };
    window.addEventListener("face-manager:update-available", handleAvailableUpdate);
    return () => window.removeEventListener("face-manager:update-available", handleAvailableUpdate);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      void checkForUpdates()
        .then((update) => {
          if (cancelled || !update.update_available || update.skipped) return;
          setAvailableUpdate(update);
          setShowUpdate(true);
        })
        .catch(() => {
          // An offline or rate-limited check must never interrupt local work.
        });
    };
    check();
    const timer = window.setInterval(check, UPDATE_CHECK_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchUnseenReleaseNotes()
      .then((result) => {
        if (cancelled) return;
        const versions = result.versions.filter((release) =>
          release.sections.some((section) => section.items.length > 0),
        );
        setUnseenReleases(versions);
        if (!result.seen && versions.length > 0) {
          setShowReleaseNotes(true);
        }
      })
      .catch(() => {
        // Release notes must never prevent the application from starting.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const closeReleaseNotes = useCallback(() => {
    if (unseenReleases.length > 0) {
      void acknowledgeCurrentReleaseNotes().catch(() => {
        // If persistence fails, the notes reappear next time instead of being lost.
      });
    }
    setShowReleaseNotes(false);
  }, [unseenReleases.length]);

  const loadFullChangelog = useCallback(() => {
    if (changelogInFlight.current) return;
    changelogInFlight.current = true;
    setChangelog({ status: "loading" });
    void fetchFullChangelog()
      .then((releases) => setChangelog({ status: "loaded", releases }))
      .catch((error) => setChangelog({
        status: "error",
        message: error instanceof Error ? error.message : "Das Änderungsprotokoll ist nicht verfügbar.",
      }))
      .finally(() => { changelogInFlight.current = false; });
  }, []);

  const openFullChangelog = useCallback(() => {
    setShowChangelog(true);
    if (changelog.status === "idle" || changelog.status === "error") loadFullChangelog();
  }, [changelog.status, loadFullChangelog]);

  useEffect(() => {
    const restoreFromLocation = () => {
      if (lastAppliedHashRef.current === window.location.hash) return;
      const entry = readNavigationEntry(geoSelectionEpoch.current);
      const canonicalHash = navigationHash(entry);
      lastAppliedHashRef.current = canonicalHash;
      if (canonicalHash !== window.location.hash) {
        window.history.replaceState(entry, "", canonicalHash);
      }
      applyEntry(entry);
    };

    window.addEventListener("popstate", restoreFromLocation);
    window.addEventListener("hashchange", restoreFromLocation);
    return () => {
      window.removeEventListener("popstate", restoreFromLocation);
      window.removeEventListener("hashchange", restoreFromLocation);
    };
  }, [applyEntry]);

  useEffect(() => subscribeToTopic<{ reason?: string }>("clusters", (update) => {
    if (update?.reason !== "database_import") return;
    geoSelectionEpoch.current += 1;
    try {
      window.sessionStorage.setItem(GEO_SELECTION_EPOCH_KEY, String(geoSelectionEpoch.current));
    } catch {
      // History selections are still invalidated in the open application.
    }
    if (parseNavigationHash(window.location.hash, window.history.state).geoSelection) {
      navigate({ page: "people" }, true);
    } else {
      setGeoSelection(undefined);
    }
  }), [navigate]);

  const handleChangePage = useCallback(
    (nextPage: AppPage) => navigate({ page: nextPage }),
    [navigate],
  );

  const handleOpenPeople = useCallback(() => navigate({ page: "review", reviewView: "open" }), [navigate]);

  const handleOpenImages = useCallback((selection: GeoPhotoSelection) => {
    navigate({ page: "people", geoSelection: hasGeoPhotoSelection(selection) ? selection : undefined });
  }, [navigate]);

  const handleChangeGeoSelection = useCallback((selection?: GeoPhotoSelection) => {
    navigate({ page: "people", geoSelection: hasGeoPhotoSelection(selection) ? selection : undefined }, true);
  }, [navigate]);

  const handleNavigateToCluster = useCallback(
    (clusterId: number, personName?: string | null) =>
      navigate({ page: "review", clusterId, personName }),
    [navigate],
  );

  const handleOpenFilenameSettings = useCallback(() => {
    navigate({ page: "settings", settingsSection: "dateinamen" });
  }, [navigate]);

  const handleNavigateSettingsSection = useCallback(
    (section: SettingsSection) => navigate({ page: "settings", settingsSection: section }),
    [navigate],
  );

  const renderPage = (target: AppPage, active: boolean): React.ReactNode => {
    switch (target) {
      case "people":
        return (
          <PeoplePage
            active={active}
            onNavigateToCluster={handleNavigateToCluster}
            onOpenPeople={handleOpenPeople}
            geoSelection={geoSelection}
            onChangeGeoSelection={handleChangeGeoSelection}
          />
        );
      case "map":
        return (
          <ErrorBoundary title="Die Weltkarte konnte nicht angezeigt werden">
            <Suspense fallback={<p role="status">Weltkarte wird geladen …</p>}>
              <MapPage active={active} onNavigateToCluster={handleNavigateToCluster} onOpenImages={handleOpenImages} />
            </Suspense>
          </ErrorBoundary>
        );
      case "review":
        return (
          <ErrorBoundary title="„Gesichter prüfen“ konnte nicht angezeigt werden">
            <ClusterPage
              navigationTarget={clusterNavigationTarget}
              active={active}
              sharedLibrary={sharedLibrary}
              onLeaveSharedReview={() => navigate({ page: "review" })}
            />
          </ErrorBoundary>
        );
      case "renaming":
        return (
          <ImageRenamePage
            active={active}
            onOpenFilenameSettings={handleOpenFilenameSettings}
          />
        );
      case "settings":
        return (
          <SettingsPage
            activeSection={settingsSection}
            onNavigateSection={handleNavigateSettingsSection}
            sharedLibrary={sharedLibrary}
          />
        );
    }
  };

  return (
    <>
      <Layout
        page={page}
        sharedLibrary={sharedLibrary.resource}
        sharedLibraryReadError={sharedLibrary.readError}
        onChangePage={handleChangePage}
        onShowReleaseNotes={openFullChangelog}
        onShowUpdate={
          availableUpdate ? () => setShowUpdate(true) : undefined
        }
      >
        {PAGE_ORDER.filter((target) => visitedRef.current.has(target)).map(
          (target) => {
            const active = target === page;
            return (
              <PagePane key={target} active={active}>
                {renderPage(target, active)}
              </PagePane>
            );
          },
        )}
      </Layout>
      {showReleaseNotes && unseenReleases.length > 0 && (
        <WhatsNewModal
          releases={unseenReleases}
          variant="whats-new"
          onClose={closeReleaseNotes}
          onShowFullChangelog={() => {
            closeReleaseNotes();
            openFullChangelog();
          }}
        />
      )}
      {showChangelog && (
        <WhatsNewModal
          releases={changelog.status === "loaded" ? changelog.releases : []}
          variant="history"
          loading={changelog.status === "idle" || changelog.status === "loading"}
          error={changelog.status === "error" ? changelog.message : undefined}
          onRetry={loadFullChangelog}
          onClose={() => setShowChangelog(false)}
        />
      )}
      {showUpdate && availableUpdate && !showReleaseNotes && !showChangelog && (
        <UpdateAvailableModal
          update={availableUpdate}
          onClose={() => setShowUpdate(false)}
          onSkip={() => {
            setShowUpdate(false);
            setAvailableUpdate(null);
          }}
        />
      )}
    </>
  );
};

export default App;
