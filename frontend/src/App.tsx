import React, { useCallback, useEffect, useRef, useState } from "react";
import Layout from "./components/layout/Layout";
import PeoplePage from "./components/people/PeoplePage";
import ClusterPage from "./components/clusters/ClusterPage";
import ImageRenamePage from "./components/renaming/ImageRenamePage";
import SettingsPage from "./components/settings/SettingsPage";
import type { FaceReviewGroupKey } from "./utils/api";
import ErrorBoundary from "./components/shared/ErrorBoundary";
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
  /** Person of the clicked face, if known — lets the review page open the
   *  matching work area without waiting for its cluster list to load. */
  personName?: string | null;
  token: number;
}

const PAGE_ORDER: AppPage[] = ["people", "renaming", "review", "settings"];
const UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000;

const PagePane: React.FC<{ active: boolean; children: React.ReactNode }> = ({
  active,
  children,
}) => (
  <div
    className={`page-pane${active ? " page-pane--active" : ""}`}
    aria-hidden={!active}
  >
    <div className="page-content">{children}</div>
  </div>
);

const App: React.FC = () => {
  const initialEntry = parseNavigationHash(window.location.hash);
  const [page, setPage] = useState<AppPage>(() => initialEntry.page);
  const [clusterNavigationTarget, setClusterNavigationTarget] =
    useState<ClusterNavigationTarget | null>(() => {
      if (initialEntry.clusterId !== undefined) {
        return { clusterId: initialEntry.clusterId, token: Date.now() };
      }
      if (initialEntry.groupKey !== undefined) {
        return { groupKey: initialEntry.groupKey, token: Date.now() };
      }
      return null;
    });
  const [settingsSection, setSettingsSection] = useState<SettingsSection | undefined>(
    () => initialEntry.settingsSection,
  );
  const [unseenReleases, setUnseenReleases] = useState<ReleaseNotes[]>([]);
  const [showReleaseNotes, setShowReleaseNotes] = useState(false);
  const [fullChangelog, setFullChangelog] = useState<ReleaseNotes[] | null>(null);
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
    } else {
      setClusterNavigationTarget(null);
    }
    setSettingsSection(entry.page === "settings" ? entry.settingsSection : undefined);
    setPage(entry.page);
  }, []);

  const navigate = useCallback(
    (entry: NavigationEntry) => {
      applyEntry(entry);
      const nextHash = navigationHash(entry);
      lastAppliedHashRef.current = nextHash;
      if (window.location.hash === nextHash) {
        window.history.replaceState(entry, "", nextHash);
      } else {
        window.history.pushState(entry, "", nextHash);
      }
    },
    [applyEntry],
  );

  useEffect(() => {
    const entry = parseNavigationHash(window.location.hash);
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

  const openFullChangelog = useCallback(() => {
    setShowChangelog(true);
    if (fullChangelog === null) {
      fetchFullChangelog()
        .then((versions) => setFullChangelog(versions))
        .catch(() => setFullChangelog([]));
    }
  }, [fullChangelog]);

  useEffect(() => {
    const restoreFromLocation = () => {
      if (lastAppliedHashRef.current === window.location.hash) return;
      const entry = parseNavigationHash(window.location.hash);
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

  const handleChangePage = useCallback(
    (nextPage: AppPage) => navigate({ page: nextPage }),
    [navigate],
  );

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
        return <PeoplePage onNavigateToCluster={handleNavigateToCluster} />;
      case "review":
        return (
          <ErrorBoundary title="„Gesichter prüfen“ konnte nicht angezeigt werden">
            <ClusterPage
              navigationTarget={clusterNavigationTarget}
              active={active}
            />
          </ErrorBoundary>
        );
      case "renaming":
        return <ImageRenamePage onOpenFilenameSettings={handleOpenFilenameSettings} />;
      case "settings":
        return (
          <SettingsPage
            activeSection={settingsSection}
            onNavigateSection={handleNavigateSettingsSection}
          />
        );
    }
  };

  return (
    <>
      <Layout
        page={page}
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
          releases={fullChangelog ?? []}
          variant="history"
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
