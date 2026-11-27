import React, { Suspense, lazy, memo, useCallback, useEffect, useRef, useState } from "react";
import Layout from "./components/layout/Layout";
import type { TopbarAction } from "./components/layout/Topbar";
import type { HelpDestination, HelpTopic } from "./components/shared/HelpTour";
import PeopleWorkspace from "./components/people/PeoplePage";
import ReviewWorkspace from "./components/clusters/ClusterPage";
import RenameWorkspace from "./components/renaming/ImageRenamePage";
import SettingsWorkspace from "./components/settings/SettingsPage";
import type { FaceReviewGroupKey } from "./utils/api";
import { useSharedLibrary } from "./hooks/useSharedLibrary";
import { LIBRARY_GENERATION_KEY, useAppNavigation } from "./hooks/useAppNavigation";
import { WorkspaceNavigationContext } from "./hooks/useWorkspaceNavigation";
import ErrorBoundary from "./components/shared/ErrorBoundary";
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
  parseNavigationHash,
  workspaceHistoryMarker,
  type AppPage,
  type NavigationEntry,
  type ReviewView,
  type SettingsSection,
} from "./utils/navigation";

export type { AppPage } from "./utils/navigation";

export interface ClusterNavigationTarget {
  clusterId?: number;
  groupKey?: FaceReviewGroupKey;
  archiveClusterId?: number;
  reviewView?: ReviewView;
  /** Person of the clicked face, if known — lets the review page open the
   *  matching work area without waiting for its cluster list to load. */
  personName?: string | null;
  token: number;
}

const PeoplePage = memo(PeopleWorkspace);
const ClusterPage = memo(ReviewWorkspace);
const ImageRenamePage = memo(RenameWorkspace);
const SettingsPage = memo(SettingsWorkspace);
const MapPage = memo(lazy(() => import("./components/map/MapPage")));
const HelpTour = lazy(() => import("./components/shared/HelpTour"));

const PAGE_ORDER: AppPage[] = ["people", "map", "renaming", "review", "settings"];
const UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000;
const HELP_INVITATION_KEY = "face-manager:help-introduction-v1";

function helpInvitationSeen() {
  try { return localStorage.getItem(HELP_INVITATION_KEY) === "seen"; } catch { return false; }
}

const helpTopicForPage: Record<AppPage, HelpTopic> = {
  people: "photos", map: "map", review: "people", renaming: "files", settings: "settings",
};

function focusHelpButton() {
  window.requestAnimationFrame(() => document.getElementById("app-help-trigger")?.focus());
}

function readGeoSelectionEpoch() {
  try {
    return Number(window.sessionStorage.getItem(LIBRARY_GENERATION_KEY)) || 0;
  } catch {
    return 0;
  }
}

function readNavigationEntry(geoSelectionEpoch: number): NavigationEntry {
  const entry = parseNavigationHash(window.location.hash, window.history.state);
  const marker = workspaceHistoryMarker(window.history.state);
  if (marker && marker.generation !== geoSelectionEpoch) return { page: entry.page };
  return entry.geoSelection && entry.geoSelectionEpoch !== geoSelectionEpoch ? { page: "people" } : entry;
}

function reviewTarget(entry: NavigationEntry, token: number): ClusterNavigationTarget | null {
  if (entry.page !== "review" || (!entry.groupKey && entry.clusterId === undefined && !entry.reviewView)) return null;
  return { clusterId: entry.clusterId, groupKey: entry.groupKey, archiveClusterId: entry.archiveClusterId,
    personName: entry.personName, reviewView: entry.reviewView, token };
}

type Changelog =
  | { status: "idle" | "loading" }
  | { status: "error"; message: string }
  | { status: "loaded"; releases: ReleaseNotes[] };

const PagePane: React.FC<{ active: boolean; page: AppPage; children: React.ReactNode }> = ({
  active,
  page,
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
      data-workspace-page={page}
    >
      <div className="page-content">{children}</div>
    </div>
  );
};

const App: React.FC = () => {
  const sharedLibrary = useSharedLibrary();
  const [initialGeoSelectionEpoch] = useState(readGeoSelectionEpoch);
  const [initialEntry] = useState(() => readNavigationEntry(initialGeoSelectionEpoch));
  const [page, setPage] = useState<AppPage>(() => initialEntry.page);
  const [geoSelection, setGeoSelection] = useState(initialEntry.geoSelection);
  const [mapAlbumId, setMapAlbumId] = useState(initialEntry.mapAlbumId);
  const [clusterNavigationTarget, setClusterNavigationTarget] =
    useState<ClusterNavigationTarget | null>(() => reviewTarget(initialEntry, 0));
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
  const [initialNotesChecked, setInitialNotesChecked] = useState(false);
  const [introSeen, setIntroSeen] = useState(helpInvitationSeen);
  const [helpTopic, setHelpTopic] = useState<HelpTopic | null>(null);
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [topbarAction, setTopbarAction] = useState<TopbarAction>();
  const helpSequence = useRef(0);

  const dismissIntroduction = () => {
    setIntroSeen(true);
    try { localStorage.setItem(HELP_INVITATION_KEY, "seen"); } catch { /* Keep the choice in this session. */ }
  };
  const showHelp = (topic: HelpTopic) => { dismissIntroduction(); setHelpTopic(topic); };
  const queuedDialog = showReleaseNotes || showChangelog || showUpdate;
  const closeHelp = () => {
    setHelpTopic(null);
    if (!queuedDialog) focusHelpButton();
  };

  // Pages are mounted lazily on first visit and then kept alive, so returning
  // to a page is instant and preserves its scroll position and state instead
  // of refetching and remounting.
  const visitedRef = useRef<Set<AppPage>>(new Set([page]));
  visitedRef.current.add(page);

  const reviewToken = useRef(0);
  const applyEntry = useCallback((entry: NavigationEntry) => {
    if (entry.page === "review") setClusterNavigationTarget(reviewTarget(entry, ++reviewToken.current));
    if (entry.page === "settings") setSettingsSection(entry.settingsSection);
    if (entry.page === "people") setGeoSelection(entry.geoSelection);
    if (entry.page === "map") setMapAlbumId(entry.mapAlbumId);
    setPage(entry.page);
  }, []);
  const clearLibraryTargets = useCallback(() => {
    setGeoSelection(undefined);
    setMapAlbumId(undefined);
    setClusterNavigationTarget(null);
  }, []);
  const navigation = useAppNavigation(initialEntry, applyEntry, initialGeoSelectionEpoch, clearLibraryTargets);
  const { navigate } = navigation;

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
        if (result.fresh_install) {
          void acknowledgeCurrentReleaseNotes().catch(() => undefined);
        } else if (!result.seen && versions.length > 0) {
          setShowReleaseNotes(true);
        }
      })
      .catch(() => {
        // Release notes must never prevent the application from starting.
      })
      .finally(() => {
        if (!cancelled) setInitialNotesChecked(true);
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

  const handleOpenPeople = useCallback(() => navigate({ page: "review", reviewView: "open" }), [navigate]);

  const handleOpenImages = useCallback((selection: GeoPhotoSelection) => {
    navigate({ page: "people", geoSelection: hasGeoPhotoSelection(selection) ? selection : undefined });
  }, [navigate]);

  const handleChangeGeoSelection = useCallback((selection?: GeoPhotoSelection) => {
    navigate({ page: "people", geoSelection: hasGeoPhotoSelection(selection) ? selection : undefined }, { replace: true, preserveWorkspace: true });
  }, [navigate]);

  const handleNavigateToCluster = useCallback(
    (clusterId: number, personName?: string | null, groupKey?: FaceReviewGroupKey) =>
      navigate(groupKey ? { page: "review", groupKey, archiveClusterId: clusterId } : { page: "review", clusterId, personName }),
    [navigate],
  );

  const handleOpenFilenameSettings = useCallback(() => {
    navigate({ page: "settings", settingsSection: "dateinamen" });
  }, [navigate]);

  const handleNavigateSettingsSection = useCallback(
    (section: SettingsSection) => navigate({ page: "settings", settingsSection: section }, { preserveWorkspace: true }),
    [navigate],
  );

  const handleNavigateReview = useCallback((target: Omit<ClusterNavigationTarget, "token">) => {
    navigate({ page: "review", ...target }, { preserveWorkspace: true });
  }, [navigate]);

  const handleMapAlbumChange = useCallback((id?: string, options?: { replace?: boolean }) => {
    navigate({ page: "map", mapAlbumId: id }, { replace: options?.replace, preserveWorkspace: true });
  }, [navigate]);

  const handleOpenAlbumMap = useCallback((id: string) => navigate({ page: "map", mapAlbumId: id }), [navigate]);
  const handleLeaveSharedReview = useCallback(() => navigate({ page: "review" }), [navigate]);

  const openHelpDestination = (destination: HelpDestination) => {
    setHelpTopic(null);
    if (destination === "import" || destination === "activities") {
      if (destination === "import") setImportDialogOpen(true);
      setTopbarAction({ destination, sequence: ++helpSequence.current });
      return;
    }
    navigate(destination === "review" ? { page: "review", reviewView: "open" }
      : destination === "settings" ? { page: "settings", settingsSection: "daten" }
        : { page: destination });
    if (queuedDialog) return;
    window.requestAnimationFrame(() => {
      const content = document.querySelector<HTMLElement>(".page-pane--active .page-content");
      const target = content?.querySelector<HTMLElement>("h1") ?? content;
      if (target) { target.tabIndex = -1; target.focus(); }
    });
  };

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
            onOpenAlbumMap={handleOpenAlbumMap}
          />
        );
      case "map":
        return (
          <ErrorBoundary title="Die Weltkarte konnte nicht angezeigt werden">
            <Suspense fallback={<p role="status">Weltkarte wird geladen …</p>}>
              <MapPage active={active} openAlbumId={mapAlbumId} onAlbumChange={handleMapAlbumChange}
                onNavigateToCluster={handleNavigateToCluster} onOpenImages={handleOpenImages} />
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
              onNavigateReview={handleNavigateReview}
              onLeaveSharedReview={handleLeaveSharedReview}
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
            active={active}
            activeSection={settingsSection}
            onNavigateSection={handleNavigateSettingsSection}
            sharedLibrary={sharedLibrary}
          />
        );
    }
  };

  return (
    <WorkspaceNavigationContext.Provider value={navigation.workspace}>
      <Layout
        page={page}
        navigation={navigation}
        sharedLibrary={sharedLibrary.resource}
        sharedLibraryReadError={sharedLibrary.readError}
        onChangePage={navigation.resume}
        onShowReleaseNotes={openFullChangelog}
        onShowHelp={() => showHelp(helpTopicForPage[page])}
        onImportStateChange={setImportDialogOpen}
        topbarAction={topbarAction}
        invitation={initialNotesChecked && !introSeen && !queuedDialog && !helpTopic && !importDialogOpen && (
          <aside className="help-invitation" aria-labelledby="help-invitation-title">
            <div>
              <strong id="help-invitation-title">Möchtest du Face Manager kennenlernen?</strong>
              <p>Wir zeigen dir die ersten Schritte. Du findest die Einführung auch jederzeit unter „Hilfe“.</p>
            </div>
            <div className="help-invitation__actions">
              <button type="button" onClick={() => showHelp("photos")}>Einführung starten</button>
              <button type="button" onClick={() => { dismissIntroduction(); focusHelpButton(); }}>Später</button>
            </div>
          </aside>
        )}
        onShowUpdate={
          availableUpdate ? () => setShowUpdate(true) : undefined
        }
      >
        {!navigation.ready && <p className="workspace-history__notice" role="status">Bibliothek wird geprüft …</p>}
        {navigation.ready && PAGE_ORDER.filter((target) => visitedRef.current.has(target)).map(
          (target) => {
            const active = target === page;
            return (
              <PagePane key={target} page={target} active={active}>
                {renderPage(target, active)}
              </PagePane>
            );
          },
        )}
      </Layout>
      {helpTopic && <Suspense fallback={null}>
        <HelpTour initialTopic={helpTopic} onClose={closeHelp} onOpenPage={openHelpDestination} />
      </Suspense>}
      {!helpTopic && !importDialogOpen && showReleaseNotes && unseenReleases.length > 0 && (
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
      {!helpTopic && !importDialogOpen && showChangelog && (
        <WhatsNewModal
          releases={changelog.status === "loaded" ? changelog.releases : []}
          variant="history"
          loading={changelog.status === "idle" || changelog.status === "loading"}
          error={changelog.status === "error" ? changelog.message : undefined}
          onRetry={loadFullChangelog}
          onClose={() => setShowChangelog(false)}
        />
      )}
      {!helpTopic && !importDialogOpen && showUpdate && availableUpdate && !showReleaseNotes && !showChangelog && (
        <UpdateAvailableModal
          update={availableUpdate}
          onClose={() => setShowUpdate(false)}
          onSkip={() => {
            setShowUpdate(false);
            setAvailableUpdate(null);
          }}
        />
      )}
    </WorkspaceNavigationContext.Provider>
  );
};

export default App;
