import React, { useEffect, useMemo, useRef, useState } from "react";

import type { ClusterNavigationTarget } from "../../App";
import {
  acceptPersonSuggestions,
  acceptPersonSuggestionBatches,
  acceptReviewSuggestions,
  batchUpdateFaces,
  ClusterDetails,
  ClusterFace,
  ClusterSummary,
  deletePerson,
  dismissPersonSuggestion,
  dismissReviewSuggestion,
  FaceReviewGroupDetails,
  FaceReviewGroupKey,
  FaceReviewGroupSummary,
  FaceImage,
  fetchImageDetail,
  fetchClusterFaces,
  fetchClusterOverview,
  fetchClusters,
  fetchFaceReviewGroupFaces,
  fetchFaceReviewGroups,
  fetchPersonSuggestions,
  fetchReviewSuggestions,
  listPersons,
  PersonSuggestion,
  ReviewSuggestion,
  renameCluster,
  renamePerson,
} from "../../utils/api";
import { subscribeToTopic } from "../../utils/events";
import ClusterFacesGrid from "./ClusterFacesGrid";
import FaceGroupGallery from "./FaceGroupGallery";
import ClusterList from "./ClusterList";
import ReviewInbox from "./ReviewInbox";

const UNKNOWN_PERSON_LABEL = "Noch zu prüfen";

interface PersonOption {
  id: number;
  name: string;
}

interface ClusterPageProps {
  navigationTarget: ClusterNavigationTarget | null;
  /** Whether this page is the visible one; background polling pauses when not. */
  active?: boolean;
}

type BatchActionType =
  | "remove_from_cluster"
  | "create_cluster"
  | "assign_person"
  | "mark_unknown_person"
  | "mark_not_face"
  | "restore_to_manual_review";

type SelectionTarget =
  | { type: "cluster"; clusterId: number }
  | { type: "group"; groupKey: FaceReviewGroupKey };

type ReviewWorkspaceView = "suggestions" | "open" | "people" | "archive";

type DetailModalState =
  | null
  | { type: "rename_cluster" }
  | { type: "manage_person" };

interface FaceGalleryContext {
  faces: ClusterFace[];
  initialFaceId: number;
  initialImage: FaceImage;
  contextLabel: string;
  groupLabel: string;
}

/** How many stacked groups render before the "show more" step. */
const SECTION_BATCH_SIZE = 25;
const LIVE_REFRESH_BATCH_MS = 2500;
const FINAL_REFRESH_DELAY_MS = 120;
const USER_IDLE_DELAY_MS = 900;

const DEFAULT_REVIEW_GROUPS: FaceReviewGroupSummary[] = [
  { group_key: "unknown_person", label: "Unbekannte Personen", face_count: 0, cluster_count: 0 },
  { group_key: "not_face", label: "Keine Gesichter", face_count: 0, cluster_count: 0 },
];

/** Pick which target should stay/become selected after a list refresh. */
function resolveSelectedTarget(
  current: SelectionTarget | null,
  clusters: ClusterSummary[],
  reviewGroups: FaceReviewGroupSummary[],
  pendingClusterId: number | null,
): SelectionTarget | null {
  if (
    pendingClusterId !== null &&
    clusters.some((cluster) => cluster.cluster_id === pendingClusterId)
  ) {
    return { type: "cluster", clusterId: pendingClusterId };
  }
  if (current?.type === "cluster") {
    if (clusters.some((cluster) => cluster.cluster_id === current.clusterId)) {
      return current;
    }
  } else if (current?.type === "group") {
    if (reviewGroups.some((group) => group.group_key === current.groupKey)) {
      return current;
    }
  }
  if (clusters.length > 0) {
    return { type: "cluster", clusterId: clusters[0].cluster_id };
  }
  if (reviewGroups.length > 0) {
    return { type: "group", groupKey: reviewGroups[0].group_key };
  }
  return null;
}

/**
 * First cluster as it appears in the people sidebar, which groups clusters by
 * person and sorts the persons alphabetically (see ClusterList). A stable sort
 * by person name reproduces that order, so element 0 is the first cluster of the
 * alphabetically first person — the one that should be selected by default.
 */
function firstAssignedClusterInSidebarOrder(
  assignedClusters: ClusterSummary[],
): ClusterSummary | null {
  if (assignedClusters.length === 0) {
    return null;
  }
  return [...assignedClusters].sort((a, b) =>
    (a.person_name || "")
      .trim()
      .localeCompare((b.person_name || "").trim(), "de", { sensitivity: "base" }),
  )[0];
}

function mergeReviewGroups(reviewGroups: FaceReviewGroupSummary[]): FaceReviewGroupSummary[] {
  const map = new Map(reviewGroups.map((group) => [group.group_key, group]));
  return DEFAULT_REVIEW_GROUPS.map((group) => map.get(group.group_key) || group);
}

const ClusterPage: React.FC<ClusterPageProps> = ({ navigationTarget, active = true }) => {
  const [clusters, setClusters] = useState<ClusterSummary[]>([]);
  const [reviewGroups, setReviewGroups] =
    useState<FaceReviewGroupSummary[]>(DEFAULT_REVIEW_GROUPS);
  const [selectedTarget, setSelectedTarget] = useState<SelectionTarget | null>(null);
  const [clusterDetails, setClusterDetails] = useState<ClusterDetails | null>(null);
  const [reviewGroupDetails, setReviewGroupDetails] =
    useState<FaceReviewGroupDetails | null>(null);
  const [persons, setPersons] = useState<PersonOption[]>([]);
  const [personSuggestions, setPersonSuggestions] = useState<PersonSuggestion[]>([]);
  const [reviewSuggestions, setReviewSuggestions] = useState<ReviewSuggestion[]>([]);
  // Opening the page through a jump must not flash the suggestions inbox first:
  // derive the work area from the destination right away.
  const [workspaceView, setWorkspaceView] = useState<ReviewWorkspaceView>(() => {
    if (navigationTarget?.groupKey) {
      return "archive";
    }
    if (typeof navigationTarget?.clusterId === "number") {
      return navigationTarget.personName ? "people" : "open";
    }
    return "suggestions";
  });
  const [assignQuery, setAssignQuery] = useState("");
  const [assignMenuOpen, setAssignMenuOpen] = useState(false);
  const [activeArchiveSectionKey, setActiveArchiveSectionKey] = useState<string | null>(null);
  const [visibleSectionCount, setVisibleSectionCount] = useState(SECTION_BATCH_SIZE);
  // Groups to continue with when the current one disappears after a mutation.
  const workflowFallbackRef = useRef<number[]>([]);
  // Full picture behind a face crop, so a crop can be judged in its context.
  const [faceGallery, setFaceGallery] = useState<FaceGalleryContext | null>(null);
  const [clusterLabelInput, setClusterLabelInput] = useState("");
  const [personNameInput, setPersonNameInput] = useState("");
  const [personDeleteTarget, setPersonDeleteTarget] = useState<
    "unassigned" | "unknown_person" | "not_face"
  >("unassigned");
  const [selectedFaceIds, setSelectedFaceIds] = useState<number[]>([]);
  const [detailModal, setDetailModal] = useState<DetailModalState>(null);
  const [isListLoading, setIsListLoading] = useState(true);
  const [isDetailsLoading, setIsDetailsLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false);
  const [highlightedClusterId, setHighlightedClusterId] = useState<number | null>(null);
  const [clusterDetailsMap, setClusterDetailsMap] = useState<Record<number, ClusterDetails>>({});
  const [loadingClusterIds, setLoadingClusterIds] = useState<Set<number>>(new Set());
  const listRequestIdRef = useRef(0);
  const detailsRequestIdRef = useRef(0);
  const appliedNavigationTokenRef = useRef<number | null>(null);
  const pendingNavigationClusterIdRef = useRef<number | null>(null);
  const pendingMainScrollClusterIdRef = useRef<number | null>(null);
  const selectionSourceRef = useRef<"explicit" | "navigation" | "scroll" | "system">("system");
  // Tracks the view the default-selection effect last resolved, so it can tell a
  // fresh entry into a view (snap to the sidebar's first item) from later reruns
  // within the same view (preserve the user's pick).
  const resolvedWorkspaceViewRef = useRef<ReviewWorkspaceView | null>(null);
  const clusterDetailsMapRef = useRef<Record<number, ClusterDetails>>({});
  const loadingClusterIdsRef = useRef<Set<number>>(new Set());
  const headerRef = useRef<HTMLDivElement | null>(null);
  const detailContentRef = useRef<HTMLDivElement | null>(null);
  const sectionRefs = useRef<Record<string, HTMLElement | null>>({});
  const liveRefreshTimerRef = useRef<number | null>(null);
  const needsCatchUpRefreshRef = useRef(false);

  const selectedClusterId =
    selectedTarget?.type === "cluster" ? selectedTarget.clusterId : null;
  const selectedReviewGroupKey =
    selectedTarget?.type === "group" ? selectedTarget.groupKey : null;

  const selectedSummary = useMemo(
    () =>
      selectedClusterId === null
        ? null
        : clusters.find((cluster) => cluster.cluster_id === selectedClusterId) || null,
    [clusters, selectedClusterId],
  );

  const selectedReviewGroupSummary = useMemo(
    () =>
      selectedReviewGroupKey === null
        ? null
        : reviewGroups.find((group) => group.group_key === selectedReviewGroupKey) || null,
    [reviewGroups, selectedReviewGroupKey],
  );

  const currentScope = useMemo(() => {
    if (selectedTarget?.type !== "cluster") {
      return null;
    }
    const personName = selectedSummary?.person_name?.trim() || "";
    if (personName) {
      // All groups of this person, so they can be scrolled through in one pass.
      return {
        kind: "person" as const,
        label: personName,
        description: "Alle Gesichtsgruppen dieser Person",
        clusters: clusters.filter(
          (cluster) => (cluster.person_name || "").trim() === personName,
        ),
      };
    }
    // All still unnamed groups, scrollable as one work list.
    return {
      kind: "unknown" as const,
      label: UNKNOWN_PERSON_LABEL,
      description: "Alle neuen Gesichtsgruppen",
      clusters: clusters.filter((cluster) => !(cluster.person_name || "").trim()),
    };
  }, [clusters, selectedSummary, selectedTarget]);

  const scopedClusterIds = useMemo(
    () => currentScope?.clusters.map((cluster) => cluster.cluster_id) ?? [],
    [currentScope],
  );

  const currentFaces = useMemo(() => {
    if (selectedTarget?.type === "cluster") {
      return clusterDetailsMap[selectedTarget.clusterId]?.faces ?? [];
    }
    if (selectedTarget?.type === "group") {
      return reviewGroupDetails?.group_key === selectedTarget.groupKey
        ? reviewGroupDetails.faces
        : [];
    }
    return [];
  }, [clusterDetailsMap, reviewGroupDetails, selectedTarget]);

  // Archived faces arrive as one flat list spanning many clusters. Group them by
  // their original cluster so related crops stay together instead of forming one
  // undifferentiated wall of faces.
  const reviewGroupSections = useMemo(() => {
    if (selectedTarget?.type !== "group") {
      return [] as Array<{ key: string; label: string; faces: ClusterFace[] }>;
    }
    const byCluster = new Map<number, ClusterFace[]>();
    const ungrouped: ClusterFace[] = [];
    currentFaces.forEach((face) => {
      if (face.cluster_id == null) {
        ungrouped.push(face);
        return;
      }
      const existing = byCluster.get(face.cluster_id);
      if (existing) {
        existing.push(face);
      } else {
        byCluster.set(face.cluster_id, [face]);
      }
    });

    const sections = Array.from(byCluster.entries())
      .sort((a, b) => b[1].length - a[1].length)
      .map(([clusterId, faces]) => {
        const summary = clusters.find((cluster) => cluster.cluster_id === clusterId);
        return {
          key: `cluster-${clusterId}`,
          label: summary?.cluster_label?.trim() || `Gruppe ${clusterId}`,
          faces,
        };
      });

    if (ungrouped.length > 0) {
      sections.push({ key: "ungrouped", label: "Einzelne Gesichter", faces: ungrouped });
    }
    return sections;
  }, [selectedTarget, currentFaces, clusters]);

  const selectedFaceIdSet = useMemo(() => new Set(selectedFaceIds), [selectedFaceIds]);

  /**
   * One uniform list of stacked groups for the detail pane, used by all three
   * work areas: the archive's sub-groups, every group of the selected person,
   * and every still unnamed group. Cluster-backed sections load their faces
   * lazily, so only what comes near the viewport is fetched.
   */
  const detailSections = useMemo(() => {
    if (selectedTarget?.type === "group") {
      return reviewGroupSections.map((section) => ({
        key: section.key,
        clusterId: null as number | null,
        label: section.label,
        faceCount: section.faces.length,
        faces: section.faces,
        loaded: true,
      }));
    }
    return (currentScope?.clusters ?? []).map((cluster) => {
      const entry = clusterDetailsMap[cluster.cluster_id];
      return {
        key: `cluster-${cluster.cluster_id}`,
        clusterId: cluster.cluster_id as number | null,
        label: cluster.cluster_label?.trim() || `Gruppe ${cluster.cluster_id}`,
        faceCount: entry?.face_count ?? cluster.face_count,
        faces: entry?.faces ?? [],
        loaded: !!entry,
      };
    });
  }, [selectedTarget, reviewGroupSections, currentScope, clusterDetailsMap]);

  /** Section the sidebar highlights / the header acts on. */
  const activeSectionKey =
    selectedTarget?.type === "cluster"
      ? `cluster-${selectedTarget.clusterId}`
      : activeArchiveSectionKey;

  // Render the stack in batches so a large scope stays responsive, but always
  // include the active group so it can be scrolled to.
  const activeSectionIndex = detailSections.findIndex(
    (section) => section.key === activeSectionKey,
  );
  const effectiveSectionCount = Math.max(
    visibleSectionCount,
    activeSectionIndex >= 0 ? activeSectionIndex + 1 : 0,
  );
  const visibleSections = detailSections.slice(0, effectiveSectionCount);
  const hiddenSectionCount = detailSections.length - visibleSections.length;
  const faceGalleryContextLabel =
    selectedTarget?.type === "group"
      ? `Kategorie: ${selectedReviewGroupSummary?.label || "Gesichter prüfen"}`
      : currentScope?.kind === "person"
        ? `Person: ${currentScope.label}`
        : `Kategorie: ${currentScope?.label || UNKNOWN_PERSON_LABEL}`;

  const scopeResetKey = `${workspaceView}:${currentScope?.label ?? ""}:${selectedReviewGroupKey ?? ""}`;
  useEffect(() => {
    setVisibleSectionCount(SECTION_BATCH_SIZE);
  }, [scopeResetKey]);

  /** Scroll one stacked group into view. Returns false if it is not rendered. */
  const scrollToSection = React.useCallback((
    sectionKey: string,
    behavior: ScrollBehavior = "smooth",
  ) => {
    const node = sectionRefs.current[sectionKey];
    const scroller = detailContentRef.current;
    if (!node || !scroller) {
      return false;
    }
    const nextTop =
      scroller.scrollTop +
      node.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top -
      8;
    scroller.scrollTo({ top: Math.max(0, nextTop), behavior });
    return true;
  }, []);

  const captureDetailAnchor = React.useCallback(() => {
    const scroller = detailContentRef.current;
    if (!scroller || pendingMainScrollClusterIdRef.current !== null) {
      return null;
    }
    const scrollerTop = scroller.getBoundingClientRect().top;
    const anchor = Object.entries(sectionRefs.current)
      .map(([key, node]) => ({ key, node }))
      .filter((entry): entry is { key: string; node: HTMLElement } => !!entry.node)
      .sort(
        (a, b) =>
          a.node.getBoundingClientRect().top - b.node.getBoundingClientRect().top,
      )
      .find((entry) => entry.node.getBoundingClientRect().bottom > scrollerTop);
    if (!anchor) return null;
    return {
      key: anchor.key,
      offset: anchor.node.getBoundingClientRect().top - scrollerTop,
    };
  }, []);

  const restoreDetailAnchor = React.useCallback(
    (anchor: ReturnType<typeof captureDetailAnchor>) => {
      if (!anchor || pendingMainScrollClusterIdRef.current !== null) return;
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          const scroller = detailContentRef.current;
          const node = sectionRefs.current[anchor.key];
          if (!scroller || !node || pendingMainScrollClusterIdRef.current !== null) {
            return;
          }
          const nextOffset =
            node.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
          scroller.scrollTop += nextOffset - anchor.offset;
        });
      });
    },
    [],
  );

  // Compact shape for the sidebar, which lists the archive sub-groups the same
  // way "Neue Gesichter" and "Personen korrigieren" list their clusters.
  const archiveSectionSummaries = useMemo(
    () =>
      reviewGroupSections.map((section) => ({
        key: section.key,
        label: section.label,
        faceCount: section.faces.length,
      })),
    [reviewGroupSections],
  );

  const currentFaceCount =
    selectedTarget?.type === "cluster"
      ? clusterDetailsMap[selectedTarget.clusterId]?.face_count ?? selectedSummary?.face_count ?? 0
      : (reviewGroupDetails?.group_key === selectedReviewGroupKey
          ? reviewGroupDetails.face_count
          : selectedReviewGroupSummary?.face_count) ?? 0;

  const selectedClusterLabel =
    selectedTarget?.type === "cluster"
      ? selectedSummary?.cluster_label?.trim() || `Gruppe ${selectedTarget.clusterId}`
      : selectedReviewGroupSummary?.label || "Arbeitsliste";

  const selectedPersonLabel =
    selectedSummary?.person_name?.trim() || UNKNOWN_PERSON_LABEL;

  const allVisibleFaceIds = useMemo(
    () => currentFaces.map((face) => face.id),
    [currentFaces],
  );

  const openClusters = useMemo(
    () => clusters.filter((cluster) => !(cluster.person_name || "").trim()),
    [clusters],
  );
  const assignedClusters = useMemo(
    () => clusters.filter((cluster) => (cluster.person_name || "").trim()),
    [clusters],
  );
  const assignedPersonCount = useMemo(
    () => new Set(assignedClusters.map((cluster) => cluster.person_name?.trim()).filter(Boolean)).size,
    [assignedClusters],
  );
  const suggestionCount = personSuggestions.length + reviewSuggestions.length;
  const archivedFaceCount = reviewGroups
    .filter((group) => group.group_key !== "unassigned")
    .reduce((sum, group) => sum + group.face_count, 0);

  useEffect(() => {
    clusterDetailsMapRef.current = clusterDetailsMap;
  }, [clusterDetailsMap]);

  const updateLoadingClusterIds = React.useCallback((clusterIds: number[], isLoading: boolean) => {
    if (clusterIds.length === 0) {
      return;
    }
    const next = new Set(loadingClusterIdsRef.current);
    clusterIds.forEach((clusterId) => {
      if (isLoading) {
        next.add(clusterId);
      } else {
        next.delete(clusterId);
      }
    });
    loadingClusterIdsRef.current = next;
    setLoadingClusterIds(next);
  }, []);

  const normalizeClusterDetails = React.useCallback((details: ClusterDetails): ClusterDetails => ({
    ...details,
    faces: Array.isArray(details.faces) ? details.faces : [],
  }), []);

  const mergeClusterDetails = React.useCallback((entries: Record<number, ClusterDetails>) => {
    if (Object.keys(entries).length === 0) {
      return;
    }
    clusterDetailsMapRef.current = {
      ...clusterDetailsMapRef.current,
      ...entries,
    };
    setClusterDetailsMap(clusterDetailsMapRef.current);
  }, []);

  // Load one cluster's faces on demand. Cached and in-flight clusters are
  // skipped so repeated triggers (scroll, neighbour preload, re-observe) never
  // hit the backend twice for the same cluster.
  const loadClusterFaces = React.useCallback(
    (clusterId: number) => {
      if (
        clusterDetailsMapRef.current[clusterId] ||
        loadingClusterIdsRef.current.has(clusterId)
      ) {
        return;
      }
      updateLoadingClusterIds([clusterId], true);
      void fetchClusterFaces(clusterId)
        .then((details) => {
          if (details) {
            mergeClusterDetails({
              [details.cluster_id]: normalizeClusterDetails(details),
            });
          }
        })
        .catch((error) => {
          console.error("Fehler beim Laden des Clusters:", error);
        })
        .finally(() => {
          updateLoadingClusterIds([clusterId], false);
        });
    },
    [mergeClusterDetails, normalizeClusterDetails, updateLoadingClusterIds],
  );

  // The person list is only needed inside the assign/manage modals, so it is
  // fetched on first modal open instead of on page enter to keep the initial
  // load lean.
  const personsLoadedRef = useRef(false);
  const ensurePersonsLoaded = React.useCallback(() => {
    if (personsLoadedRef.current) {
      return;
    }
    personsLoadedRef.current = true;
    listPersons()
      .then((data) => setPersons(data))
      .catch((error) => {
        personsLoadedRef.current = false;
        console.error("Fehler beim Laden der Personenliste:", error);
      });
  }, []);

  useEffect(() => {
    if (detailModal) {
      ensurePersonsLoaded();
    }
  }, [detailModal, ensurePersonsLoaded]);

  const loadSidebarData = React.useCallback(async (background = false) => {
    const requestId = listRequestIdRef.current + 1;
    listRequestIdRef.current = requestId;
    if (!background) {
      setIsListLoading(true);
    }

    try {
      const [clusterData, reviewGroupData, suggestionData, reviewSuggestionData] = await Promise.all([
        fetchClusters(),
        fetchFaceReviewGroups(),
        fetchPersonSuggestions(),
        fetchReviewSuggestions(),
      ]);
      if (listRequestIdRef.current !== requestId) {
        return;
      }

      const safeClusters = Array.isArray(clusterData) ? clusterData : [];
      const mergedGroups = mergeReviewGroups(
        Array.isArray(reviewGroupData) ? reviewGroupData : [],
      );

      setClusters(safeClusters);
      setReviewGroups(mergedGroups);
      setPersonSuggestions(Array.isArray(suggestionData) ? suggestionData : []);
      setReviewSuggestions(Array.isArray(reviewSuggestionData) ? reviewSuggestionData : []);
      setSelectedTarget((current) =>
        resolveSelectedTarget(
          current,
          safeClusters,
          mergedGroups,
          pendingNavigationClusterIdRef.current,
        ),
      );
    } catch (error) {
      console.error("Fehler beim Laden der Cluster-Ansicht:", error);
    } finally {
      if (listRequestIdRef.current === requestId) {
        setIsListLoading(false);
      }
    }
  }, []);

  const loadTargetDetails = React.useCallback((target: SelectionTarget | null) => {
    if (!target) {
      setClusterDetails(null);
      setReviewGroupDetails(null);
      setIsDetailsLoading(false);
      return;
    }

    const requestId = detailsRequestIdRef.current + 1;
    detailsRequestIdRef.current = requestId;
    setIsDetailsLoading(true);

    if (target.type === "cluster") {
      setReviewGroupDetails(null);
      const cachedDetails = clusterDetailsMapRef.current[target.clusterId];
      if (cachedDetails) {
        setClusterDetails(cachedDetails);
        setIsDetailsLoading(false);
        return;
      }
      if (loadingClusterIdsRef.current.has(target.clusterId)) {
        setIsDetailsLoading(false);
        return;
      }

      updateLoadingClusterIds([target.clusterId], true);
      void fetchClusterFaces(target.clusterId)
        .then((data) => {
          if (detailsRequestIdRef.current !== requestId) {
            return;
          }
          if (data) {
            const normalizedData = normalizeClusterDetails(data);
            setClusterDetails(normalizedData);
            mergeClusterDetails({ [target.clusterId]: normalizedData });
          } else {
            setClusterDetails(null);
          }
        })
        .catch((error) => {
          if (detailsRequestIdRef.current === requestId) {
            setClusterDetails(null);
          }
          console.error("Fehler beim Laden der Clusterdetails:", error);
        })
        .finally(() => {
          updateLoadingClusterIds([target.clusterId], false);
          if (detailsRequestIdRef.current === requestId) {
            setIsDetailsLoading(false);
          }
        });
      return;
    }

    setClusterDetails(null);
    setReviewGroupDetails((current) =>
      current?.group_key === target.groupKey ? current : null,
    );
    void fetchFaceReviewGroupFaces(target.groupKey)
      .then((data) => {
        if (detailsRequestIdRef.current !== requestId) {
          return;
        }
        setReviewGroupDetails(
          data
            ? {
                ...data,
                faces: Array.isArray(data.faces) ? data.faces : [],
              }
            : null,
        );
      })
      .catch((error) => {
        if (detailsRequestIdRef.current === requestId) {
          setReviewGroupDetails(null);
        }
        console.error("Fehler beim Laden der Prüfliste:", error);
      })
      .finally(() => {
        if (detailsRequestIdRef.current === requestId) {
          setIsDetailsLoading(false);
        }
      });
  }, [mergeClusterDetails, normalizeClusterDetails, updateLoadingClusterIds]);

  const refreshTargetDetails = React.useCallback(
    async (target: SelectionTarget | null) => {
      if (!target) return;
      const requestId = detailsRequestIdRef.current + 1;
      detailsRequestIdRef.current = requestId;
      try {
        if (target.type === "cluster") {
          const data = await fetchClusterFaces(target.clusterId);
          if (detailsRequestIdRef.current !== requestId || !data) return;
          const normalized = normalizeClusterDetails(data);
          setClusterDetails(normalized);
          mergeClusterDetails({ [target.clusterId]: normalized });
          return;
        }
        const data = await fetchFaceReviewGroupFaces(target.groupKey);
        if (detailsRequestIdRef.current !== requestId || !data) return;
        setReviewGroupDetails({
          ...data,
          faces: Array.isArray(data.faces) ? data.faces : [],
        });
      } catch (error) {
        console.error("Live-Aktualisierung der Gesichter fehlgeschlagen:", error);
      }
    },
    [mergeClusterDetails, normalizeClusterDetails],
  );

  // One-time bootstrap: fetch the summaries, review-group counts and the first
  // cluster's faces in a single round trip so the initial view paints without a
  // second request. Falls back to the split loaders if the overview fails.
  useEffect(() => {
    let cancelled = false;
    const requestId = listRequestIdRef.current + 1;
    listRequestIdRef.current = requestId;
    setIsListLoading(true);
    fetchClusterOverview()
      .then((overview) => {
        if (cancelled || listRequestIdRef.current !== requestId) {
          return;
        }
        const mergedGroups = mergeReviewGroups(overview.review_groups);
        setClusters(overview.clusters);
        setReviewGroups(mergedGroups);
        if (overview.first_cluster) {
          mergeClusterDetails({
            [overview.first_cluster.cluster_id]: normalizeClusterDetails(
              overview.first_cluster,
            ),
          });
        }
        setSelectedTarget((current) =>
          resolveSelectedTarget(
            current,
            overview.clusters,
            mergedGroups,
            pendingNavigationClusterIdRef.current,
          ),
        );
      })
      .catch((error) => {
        console.error("Fehler beim Laden der Cluster-Übersicht:", error);
        if (!cancelled) {
          void loadSidebarData();
        }
      })
      .finally(() => {
        if (!cancelled && listRequestIdRef.current === requestId) {
          setIsListLoading(false);
        }
      });
    void fetchPersonSuggestions()
      .then((items) => {
        if (!cancelled) setPersonSuggestions(items);
      })
      .catch((error) => console.error("Fehler beim Laden der Personenvorschläge:", error));
    void fetchReviewSuggestions()
      .then((items) => {
        if (!cancelled) setReviewSuggestions(items);
      })
      .catch((error) => console.error("Fehler beim Laden der Aussortier-Vorschläge:", error));
    return () => {
      cancelled = true;
    };
  }, [loadSidebarData, mergeClusterDetails, normalizeClusterDetails]);

  useEffect(() => {
    // Pause background polling while the cluster page is not the visible one so
    // we do not hit the backend from a kept-alive page in the background.
    if (!active) {
      needsCatchUpRefreshRef.current = true;
      return;
    }
    let isMounted = true;
    let refreshInFlight = false;
    let refreshPending = false;
    let userBusyUntil = 0;

    const refreshVisibleData = async () => {
      if (document.visibilityState !== "visible") {
        needsCatchUpRefreshRef.current = true;
        return;
      }
      if (isMutating) {
        needsCatchUpRefreshRef.current = true;
        return;
      }
      const idleIn = userBusyUntil - performance.now();
      if (idleIn > 0) {
        maybeRefreshVisibleData(idleIn + 50);
        return;
      }
      if (refreshInFlight) {
        refreshPending = true;
        return;
      }
      refreshInFlight = true;
      // Reclustering is scoped now, so a change usually touches only some
      // groups. Dropping every cached group here would collapse all sections to
      // their reserved height and throw the user's scroll position and marking
      // away. Instead just refetch, and let the reconciliation effect below
      // discard exactly the groups that really changed.
      try {
        const anchor = captureDetailAnchor();
        await loadSidebarData(true);
        await refreshTargetDetails(selectedTarget);
        restoreDetailAnchor(anchor);
        needsCatchUpRefreshRef.current = false;
      } finally {
        refreshInFlight = false;
        if (isMounted && refreshPending) {
          refreshPending = false;
          maybeRefreshVisibleData(LIVE_REFRESH_BATCH_MS);
        }
      }
    };

    const maybeRefreshVisibleData = (
      delay = LIVE_REFRESH_BATCH_MS,
      replacePending = false,
    ) => {
      if (liveRefreshTimerRef.current !== null) {
        if (!replacePending) return;
        window.clearTimeout(liveRefreshTimerRef.current);
      }
      liveRefreshTimerRef.current = window.setTimeout(() => {
        liveRefreshTimerRef.current = null;
        void refreshVisibleData();
      }, delay);
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        needsCatchUpRefreshRef.current = true;
        maybeRefreshVisibleData(FINAL_REFRESH_DELAY_MS, true);
      } else {
        needsCatchUpRefreshRef.current = true;
      }
    };
    const handleWindowFocus = () => {
      needsCatchUpRefreshRef.current = true;
      maybeRefreshVisibleData(FINAL_REFRESH_DELAY_MS, true);
    };
    const noteUserActivity = () => {
      userBusyUntil = performance.now() + USER_IDLE_DELAY_MS;
    };

    // Import checkpoints are applied in bounded batches. Final and explicit
    // changes arrive quickly, but even those wait until scrolling/pointer work
    // has been idle briefly so the page never moves underneath the user.
    const unsubscribeClusters = subscribeToTopic<{ reason?: string }>(
      "clusters",
      (update) => {
        needsCatchUpRefreshRef.current = true;
        const isBackgroundCheckpoint = update?.reason === "background_progress";
        maybeRefreshVisibleData(
          isBackgroundCheckpoint ? LIVE_REFRESH_BATCH_MS : FINAL_REFRESH_DELAY_MS,
          !isBackgroundCheckpoint,
        );
      },
    );
    document.addEventListener("visibilitychange", handleVisibilityChange);
    document.addEventListener("scroll", noteUserActivity, true);
    document.addEventListener("pointerdown", noteUserActivity, true);
    window.addEventListener("focus", handleWindowFocus);

    if (needsCatchUpRefreshRef.current && !isMutating) {
      maybeRefreshVisibleData(FINAL_REFRESH_DELAY_MS, true);
    }

    return () => {
      isMounted = false;
      if (liveRefreshTimerRef.current !== null) {
        window.clearTimeout(liveRefreshTimerRef.current);
        liveRefreshTimerRef.current = null;
      }
      unsubscribeClusters();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      document.removeEventListener("scroll", noteUserActivity, true);
      document.removeEventListener("pointerdown", noteUserActivity, true);
      window.removeEventListener("focus", handleWindowFocus);
    };
  }, [
    active,
    captureDetailAnchor,
    isMutating,
    loadSidebarData,
    refreshTargetDetails,
    restoreDetailAnchor,
    selectedTarget,
  ]);

  // Reconcile cached faces with a refreshed cluster list. A background rebuild
  // touches only some groups, so anything whose face count still matches is
  // kept — that is what stops the view from jumping when the group the user is
  // looking at did not change at all.
  useEffect(() => {
    const byId = new Map(clusters.map((cluster) => [cluster.cluster_id, cluster]));
    setClusterDetailsMap((current) => {
      let dropped = false;
      const next: Record<number, ClusterDetails> = {};
      Object.entries(current).forEach(([key, details]) => {
        const clusterId = Number(key);
        const summary = byId.get(clusterId);
        if (summary && summary.face_count === details.face_count) {
          next[clusterId] = details;
        } else {
          dropped = true;
        }
      });
      if (!dropped) {
        return current;
      }
      clusterDetailsMapRef.current = next;
      return next;
    });
  }, [clusters]);

  // Keep only marks whose faces are still present, so a refresh never acts on
  // faces that have meanwhile moved elsewhere.
  useEffect(() => {
    const availableFaceIds = new Set<number>();
    Object.values(clusterDetailsMap).forEach((details) => {
      details.faces.forEach((face) => availableFaceIds.add(face.id));
    });
    reviewGroupDetails?.faces.forEach((face) => availableFaceIds.add(face.id));
    setSelectedFaceIds((current) => {
      const kept = current.filter((faceId) => availableFaceIds.has(faceId));
      return kept.length === current.length ? current : kept;
    });
  }, [clusterDetailsMap, reviewGroupDetails]);

  // Refresh once when returning to the cluster page so kept-alive data catches
  // up with anything that changed while it was in the background.
  const wasActiveRef = useRef(active);
  useEffect(() => {
    if (active && !wasActiveRef.current && !isMutating) {
      void loadSidebarData(true);
    }
    wasActiveRef.current = active;
  }, [active, isMutating, loadSidebarData]);

  useEffect(() => {
    const selectionSource = selectionSourceRef.current;
    loadTargetDetails(selectedTarget);
    if (selectionSource !== "scroll") {
      setSelectedFaceIds([]);
      setDetailModal(null);
    }
    selectionSourceRef.current = "system";
  }, [loadTargetDetails, selectedTarget]);

  // Eagerly load only the selected cluster and its immediate neighbours so the
  // initial view and nearby scrolling feel instant. Everything else is loaded
  // lazily by the viewport observer below instead of fetching the whole scope.
  useEffect(() => {
    if (scopedClusterIds.length === 0) {
      return;
    }
    const anchorIndex =
      selectedClusterId != null
        ? Math.max(0, scopedClusterIds.indexOf(selectedClusterId))
        : 0;
    const startIndex = Math.max(0, anchorIndex - 1);
    const endIndex = Math.min(scopedClusterIds.length, anchorIndex + 2);
    scopedClusterIds.slice(startIndex, endIndex).forEach(loadClusterFaces);
  }, [scopedClusterIds, selectedClusterId, loadClusterFaces]);

  // Load the remaining groups as their section approaches the viewport, so a
  // long scope (every group of a person, every new group) never fetches all
  // faces up front. Sections reserve their height meanwhile, keeping scrolling
  // stable.
  useEffect(() => {
    const pending = detailSections.filter(
      (section) => section.clusterId != null && !section.loaded,
    );
    if (pending.length === 0) {
      return;
    }
    const nodeToClusterId = new Map<Element, number>();
    pending.forEach((section) => {
      const node = sectionRefs.current[section.key];
      if (node && section.clusterId != null) {
        nodeToClusterId.set(node, section.clusterId);
      }
    });
    if (nodeToClusterId.size === 0) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) {
            return;
          }
          const clusterId = nodeToClusterId.get(entry.target);
          if (clusterId != null) {
            loadClusterFaces(clusterId);
          }
        });
      },
      { root: detailContentRef.current, rootMargin: "600px 0px", threshold: 0 },
    );
    nodeToClusterId.forEach((_, node) => observer.observe(node));
    return () => observer.disconnect();
  }, [detailSections, loadClusterFaces]);

  useEffect(() => {
    if (selectedTarget?.type !== "cluster") {
      setClusterLabelInput("");
      setPersonNameInput("");
      return;
    }
    setClusterLabelInput(selectedSummary?.cluster_label?.trim() || `Gruppe ${selectedTarget.clusterId}`);
    setPersonNameInput(selectedSummary?.person_name?.trim() || "");
  }, [selectedSummary, selectedTarget]);

  useEffect(() => {
    if (!navigationTarget) {
      return;
    }
    if (appliedNavigationTokenRef.current === navigationTarget.token) {
      return;
    }
    appliedNavigationTokenRef.current = navigationTarget.token;
    if (navigationTarget.groupKey) {
      setWorkspaceView("archive");
      pendingNavigationClusterIdRef.current = null;
      pendingMainScrollClusterIdRef.current = null;
      selectionSourceRef.current = "navigation";
      setSelectedTarget({ type: "group", groupKey: navigationTarget.groupKey });
      setHighlightedClusterId(null);
      return;
    }
    if (typeof navigationTarget.clusterId !== "number") {
      return;
    }

    // The caller usually knows the person already, so the work area can switch
    // before the cluster list has arrived. `null` is a real answer here ("no
    // person"), so distinguish it from "no hint given". If no hint exists, a
    // separate effect resolves the destination from the loaded summaries.
    if (navigationTarget.personName !== undefined) {
      setWorkspaceView(navigationTarget.personName ? "people" : "open");
    }

    pendingNavigationClusterIdRef.current = navigationTarget.clusterId;
    pendingMainScrollClusterIdRef.current = navigationTarget.clusterId;
    selectionSourceRef.current = "navigation";
    setSelectedTarget({ type: "cluster", clusterId: navigationTarget.clusterId });
    setHighlightedClusterId(navigationTarget.clusterId);

    const timeoutId = window.setTimeout(() => {
      setHighlightedClusterId((current) =>
        current === navigationTarget.clusterId ? null : current,
      );
    }, 2600);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [navigationTarget]);

  useEffect(() => {
    const pendingClusterId = pendingNavigationClusterIdRef.current;
    if (pendingClusterId === null) return;
    const destination = clusters.find(
      (cluster) => cluster.cluster_id === pendingClusterId,
    );
    if (destination) {
      setWorkspaceView(destination.person_name?.trim() ? "people" : "open");
    }
  }, [clusters]);

  /**
   * After a group was emptied by an assignment, continue with the one that
   * followed it instead of snapping back to the top of the list — that is what
   * keeps a review session flowing. Consumed once, then cleared.
   */
  const pickContinuation = (available: ClusterSummary[]): number | null => {
    const candidates = workflowFallbackRef.current;
    if (candidates.length === 0) {
      return null;
    }
    const next =
      candidates.find((clusterId) =>
        available.some((cluster) => cluster.cluster_id === clusterId),
      ) ?? null;
    workflowFallbackRef.current = [];
    return next;
  };

  useEffect(() => {
    const enteringWorkspaceView = resolvedWorkspaceViewRef.current !== workspaceView;
    resolvedWorkspaceViewRef.current = workspaceView;
    if (workspaceView === "suggestions") return;

    // An explicit jump (e.g. clicking a face in the pictures view) switches the
    // work area *and* the selection at once. Entering a view normally snaps to
    // its first group, which would immediately discard that destination — so
    // the pending jump owns the selection until it has been applied.
    if (pendingNavigationClusterIdRef.current !== null) {
      return;
    }
    if (workspaceView === "open") {
      const selectedIsOpen =
        selectedTarget?.type === "cluster" &&
        openClusters.some((cluster) => cluster.cluster_id === selectedTarget.clusterId);
      if (!selectedIsOpen) {
        const next = pickContinuation(openClusters);
        setSelectedTarget(next ? { type: "cluster", clusterId: next } : null);
      } else {
        // Selection survived, so the continuation hint is stale.
        workflowFallbackRef.current = [];
      }
      return;
    }
    if (workspaceView === "people") {
      const selectedIsAssigned =
        selectedTarget?.type === "cluster" &&
        assignedClusters.some((cluster) => cluster.cluster_id === selectedTarget.clusterId);
      // On entering the view (or when the selection is invalid for it) default to
      // the first cluster as shown in the sidebar — grouped by person, sorted
      // alphabetically — instead of the raw data order. Once inside the view a
      // deliberate pick is preserved.
      if (!selectedIsAssigned || enteringWorkspaceView) {
        const continuation = enteringWorkspaceView ? null : pickContinuation(assignedClusters);
        const first = firstAssignedClusterInSidebarOrder(assignedClusters);
        const nextClusterId = continuation ?? (first ? first.cluster_id : null);
        const currentClusterId =
          selectedTarget?.type === "cluster" ? selectedTarget.clusterId : null;
        if (nextClusterId !== currentClusterId) {
          setSelectedTarget(
            nextClusterId !== null ? { type: "cluster", clusterId: nextClusterId } : null,
          );
        }
      } else {
        workflowFallbackRef.current = [];
      }
      return;
    }
    if (selectedTarget?.type !== "group") {
      const preferred = reviewGroups.find((group) => group.group_key === "unknown_person")
        ?? reviewGroups.find((group) => group.group_key === "not_face");
      setSelectedTarget(preferred ? { type: "group", groupKey: preferred.group_key } : null);
    }
  }, [assignedClusters, openClusters, reviewGroups, selectedTarget, workspaceView]);

  useEffect(() => {
    const pendingClusterId = pendingMainScrollClusterIdRef.current;
    if (pendingClusterId === null || selectedTarget?.type !== "cluster") {
      return;
    }
    if (selectedTarget.clusterId !== pendingClusterId) {
      return;
    }
    // Wait until the target's real face grid has replaced its placeholder, then
    // wait two frames for masonry/layout measurements. Scrolling earlier can
    // land on a neighbouring group when the first navigation causes the detail
    // pane to mount and expand at the same time.
    if (!clusterDetailsMap[pendingClusterId]) return;
    let secondFrame = 0;
    const firstFrame = window.requestAnimationFrame(() => {
      secondFrame = window.requestAnimationFrame(() => {
        if (
          pendingMainScrollClusterIdRef.current === pendingClusterId &&
          scrollToSection(`cluster-${pendingClusterId}`, "auto")
        ) {
          pendingMainScrollClusterIdRef.current = null;
        }
      });
    });
    return () => {
      window.cancelAnimationFrame(firstFrame);
      if (secondFrame) window.cancelAnimationFrame(secondFrame);
    };
  }, [selectedTarget, clusterDetailsMap, scopedClusterIds, scrollToSection]);

  useEffect(() => {
    const pendingClusterId = pendingNavigationClusterIdRef.current;
    if (pendingClusterId === null) {
      return;
    }
    if (
      selectedTarget?.type === "cluster" &&
      selectedTarget.clusterId === pendingClusterId &&
      selectedSummary
    ) {
      pendingNavigationClusterIdRef.current = null;
    }
  }, [selectedSummary, selectedTarget]);

  const refreshAllData = React.useCallback(async () => {
    await loadSidebarData(true);
    loadTargetDetails(selectedTarget);
  }, [loadSidebarData, loadTargetDetails, selectedTarget]);

  const handleAcceptSuggestions = async (personId: number, clusterIds: number[]) => {
    if (isMutating || clusterIds.length === 0) return;
    setIsMutating(true);
    try {
      await acceptPersonSuggestions(personId, clusterIds);
      setClusterDetailsMap({});
      clusterDetailsMapRef.current = {};
      await loadSidebarData(true);
    } catch (error) {
      console.error("Fehler beim Übernehmen der Personenvorschläge:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const handleAcceptAllRecommended = async (
    assignments: { person_id: number; cluster_ids: number[] }[],
  ) => {
    if (isMutating || assignments.length === 0) return;
    setIsMutating(true);
    try {
      await acceptPersonSuggestionBatches(assignments);
      setClusterDetailsMap({});
      clusterDetailsMapRef.current = {};
      await loadSidebarData(true);
    } catch (error) {
      console.error("Fehler beim Sammelübernehmen sicherer Vorschläge:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const handleDismissSuggestion = async (clusterId: number) => {
    if (isMutating) return;
    setIsMutating(true);
    try {
      await dismissPersonSuggestion(clusterId);
      setPersonSuggestions((items) =>
        items.filter((item) => item.cluster_id !== clusterId),
      );
    } catch (error) {
      console.error("Fehler beim Verwerfen des Personenvorschlags:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const handleAcceptReviewSuggestions = async (clusterIds: number[]) => {
    if (isMutating || clusterIds.length === 0) return;
    setIsMutating(true);
    try {
      await acceptReviewSuggestions(clusterIds);
      clusterDetailsMapRef.current = {};
      setClusterDetailsMap({});
      await loadSidebarData(true);
    } catch (error) {
      console.error("Fehler beim Übernehmen der Aussortier-Vorschläge:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const handleDismissReviewSuggestion = async (clusterId: number) => {
    if (isMutating) return;
    setIsMutating(true);
    try {
      await dismissReviewSuggestion(clusterId);
      await loadSidebarData(true);
    } catch (error) {
      console.error("Fehler beim Verwerfen des Aussortier-Vorschlags:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const toggleFaceSelection = (faceId: number) => {
    setSelectedFaceIds((current) =>
      current.includes(faceId)
        ? current.filter((id) => id !== faceId)
        : [...current, faceId],
    );
  };

  const handleSelectAllFaces = () => {
    setSelectedFaceIds((current) =>
      current.length === allVisibleFaceIds.length ? [] : allVisibleFaceIds,
    );
  };

  /** Groups to fall back to, nearest first, if the current one disappears. */
  const remainingScopeAfterSelection = () => {
    const scopeIds = (currentScope?.clusters ?? []).map((cluster) => cluster.cluster_id);
    const index = selectedClusterId === null ? -1 : scopeIds.indexOf(selectedClusterId);
    if (index === -1) {
      return scopeIds;
    }
    return [...scopeIds.slice(index + 1), ...scopeIds.slice(0, index).reverse()];
  };

  const openFaceContext = React.useCallback(
    async (
      face: ClusterFace,
      groupFaces: ClusterFace[],
      groupLabel: string,
      contextLabel: string,
    ) => {
      try {
        const initialImage = await fetchImageDetail(face.image_id);
        setFaceGallery({
          faces: groupFaces,
          initialFaceId: face.id,
          initialImage,
          groupLabel,
          contextLabel,
        });
      } catch (error) {
        console.error("Das Bild zu diesem Gesicht konnte nicht geladen werden:", error);
      }
    },
    [],
  );

  /** Mark or unmark every face of one archive section at once. */
  const setFaceGroupSelection = (faceIds: number[], selected: boolean) => {
    setSelectedFaceIds((current) => {
      const next = new Set(current);
      faceIds.forEach((id) => (selected ? next.add(id) : next.delete(id)));
      return Array.from(next);
    });
  };

  const resetAssignmentInputs = () => {
    setAssignQuery("");
    setAssignMenuOpen(false);
  };

  const handleBatchAction = async (
    action: BatchActionType,
    faceIds: number[] = selectedFaceIds,
    personName?: string,
  ) => {
    if (faceIds.length === 0 || isMutating) {
      return;
    }

    const payload: {
      action:
        | "remove_from_cluster"
        | "create_cluster"
        | "assign_person"
        | "mark_unknown_person"
        | "mark_not_face"
        | "restore_to_manual_review";
      face_ids: number[];
      cluster_id?: number;
      person_name?: string;
    } = {
      action,
      face_ids: faceIds,
    };

    if (action === "remove_from_cluster") {
      if (selectedTarget?.type !== "cluster") {
        return;
      }
      payload.cluster_id = selectedTarget.clusterId;
    }

    if (action === "assign_person") {
      const name = (personName ?? "").trim();
      if (!name) {
        return;
      }
      payload.person_name = name;
    }

    setIsMutating(true);
    try {
      // Assigning must not pull the view out of the current work area: the new
      // cluster belongs to a person, so selecting it while reviewing new faces
      // would bounce the selection to the top of the list. Stay put, refresh,
      // and only move on if the current group is gone — then to the *next* one.
      workflowFallbackRef.current = remainingScopeAfterSelection();
      await batchUpdateFaces(payload);
      resetAssignmentInputs();
      setSelectedFaceIds([]);
      setDetailModal(null);
      await loadSidebarData(true);
      loadTargetDetails(selectedTarget);

      if (action === "assign_person") {
        const nextPersons = await listPersons();
        setPersons(nextPersons);
      }
    } catch (error) {
      console.error("Fehler bei der Batch-Aktion:", error);
    } finally {
      setIsMutating(false);
    }
  };

  // Actions apply to the marked faces, or to the whole group when nothing is
  // marked — so assigning a full cluster to a person is just pick + confirm.
  const hasFaceSelection = selectedFaceIds.length > 0;
  const targetFaceIds = hasFaceSelection ? selectedFaceIds : allVisibleFaceIds;
  const assignQueryTrimmed = assignQuery.trim();
  const filteredPersons = useMemo(() => {
    const query = assignQueryTrimmed.toLocaleLowerCase("de");
    const list = query
      ? persons.filter((person) => person.name.toLocaleLowerCase("de").includes(query))
      : persons;
    return list.slice(0, 8);
  }, [persons, assignQueryTrimmed]);
  const exactPersonMatch = useMemo(
    () =>
      persons.find(
        (person) =>
          person.name.toLocaleLowerCase("de") === assignQueryTrimmed.toLocaleLowerCase("de"),
      ) ?? null,
    [persons, assignQueryTrimmed],
  );
  const canAssign = targetFaceIds.length > 0 && assignQueryTrimmed.length > 0;

  const assignToPerson = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || targetFaceIds.length === 0 || isMutating) {
      return;
    }
    setAssignMenuOpen(false);
    void handleBatchAction("assign_person", targetFaceIds, trimmed);
  };

  const runQuickAction = (action: BatchActionType) => {
    void handleBatchAction(action, targetFaceIds);
  };

  const handleRenameCluster = async () => {
    if (selectedTarget?.type !== "cluster" || isMutating) {
      return;
    }
    const nextLabel = clusterLabelInput.trim();
    if (!nextLabel) {
      return;
    }
    setIsMutating(true);
    try {
      await renameCluster(selectedTarget.clusterId, nextLabel);
      await refreshAllData();
    } catch (error) {
      console.error("Fehler beim Umbenennen des Clusters:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const selectedPersonId =
    selectedSummary?.person_name
      ? persons.find((person) => person.name === selectedSummary.person_name)?.id ?? null
      : null;

  const handleRenamePerson = async () => {
    if (!selectedPersonId || isMutating) {
      return;
    }
    const nextName = personNameInput.trim();
    if (!nextName) {
      return;
    }
    setIsMutating(true);
    try {
      await renamePerson(selectedPersonId, nextName);
      const nextPersons = await listPersons();
      setPersons(nextPersons);
      await refreshAllData();
    } catch (error) {
      console.error("Fehler beim Umbenennen der Person:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const handleDeletePerson = async () => {
    if (!selectedPersonId || !selectedSummary?.person_name || isMutating) {
      return;
    }
    const confirmed = window.confirm(
      `Person "${selectedSummary.person_name}" löschen und zugeordnete Gesichter als ${
        personDeleteTarget === "unassigned"
          ? "nicht zugewiesen"
          : personDeleteTarget === "unknown_person"
            ? "unbekannt"
            : "kein Gesicht"
      } einordnen?`,
    );
    if (!confirmed) {
      return;
    }
    setIsMutating(true);
    try {
      await deletePerson(selectedPersonId, personDeleteTarget);
      const nextPersons = await listPersons();
      setPersons(nextPersons);
      await refreshAllData();
    } catch (error) {
      console.error("Fehler beim Löschen der Person:", error);
    } finally {
      setIsMutating(false);
    }
  };

  // The "sort out" actions beside the assignment combobox. Person assignment is
  // handled by the combobox, and splitting off a new group is obsolete now that
  // faces can be assigned directly — so neither appears here.
  const secondaryActions = useMemo(() => {
    if (!selectedTarget) {
      return [] as Array<{ action: BatchActionType; label: string; description: string }>;
    }

    const actions: Array<{ action: BatchActionType; label: string; description: string }> = [
      {
        action: "mark_unknown_person",
        label: "Unbekannt",
        description: "Legt die Gesichter als unbekannte Person ab und berücksichtigt diese Entscheidung künftig.",
      },
      {
        action: "mark_not_face",
        label: "Kein Gesicht",
        description: "Legt Fehl-Erkennungen ab und berücksichtigt diese Entscheidung künftig.",
      },
    ];

    if (selectedTarget.type === "cluster") {
      actions.push({
        action: "remove_from_cluster",
        label: "Aus Gesichtsgruppe lösen",
        description: "Löst die ausgewählten Gesichter aus dieser Gruppe und legt sie erneut zur Prüfung vor.",
      });
    }

    if (
      selectedTarget.type === "group" &&
      (selectedTarget.groupKey === "unknown_person" || selectedTarget.groupKey === "not_face")
    ) {
      actions.push({
        action: "restore_to_manual_review",
        label: "Erneut prüfen",
        description: "Legt die Gesichter erneut unter „Neue Gesichter“ zur Prüfung vor.",
      });
    }

    return actions;
  }, [selectedTarget]);

  const selectCluster = (
    clusterId: number,
    options: { scrollToCluster?: boolean; source?: "explicit" | "navigation" | "scroll" | "system" } = {},
  ) => {
    if (options.scrollToCluster) {
      pendingMainScrollClusterIdRef.current = clusterId;
    }
    selectionSourceRef.current = options.source ?? "explicit";
    setSelectedTarget({ type: "cluster", clusterId });
  };

  const selectReviewGroup = (groupKey: FaceReviewGroupKey) => {
    selectionSourceRef.current = "explicit";
    setActiveArchiveSectionKey(null);
    setSelectedTarget({ type: "group", groupKey });
  };

  const scrollMainContentToTop = React.useCallback(() => {
    const scroller = detailContentRef.current;
    if (scroller) {
      scroller.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, []);

  /** Jump to one group from the sidebar (table-of-contents style). */
  const selectArchiveSection = React.useCallback(
    (sectionKey: string) => {
      setActiveArchiveSectionKey(sectionKey);
      scrollToSection(sectionKey);
    },
    [scrollToSection],
  );

  return (
    <div
      className={
        workspaceView === "suggestions"
          ? "review-page"
          : "review-page review-page--workspace"
      }
    >
      <header className="review-page__header">
        <div>
          <span>Prüfen und korrigieren</span>
          <h1>Gesichter prüfen</h1>
          <p>Bestätige Vorschläge, ordne neue Gesichter zu oder korrigiere bestehende Zuordnungen.</p>
        </div>
      </header>

      <nav className="review-page-tabs" aria-label="Prüfbereiche">
        {([
          ["suggestions", "Vorschläge", "Automatisch erkannte Treffer", suggestionCount],
          ["open", "Neue Gesichter", "Benennen oder aussortieren", openClusters.length],
          ["people", "Personen korrigieren", "Bestätigte Zuordnungen", assignedPersonCount],
          ["archive", "Aussortiert", "Unbekannt und Fehl-Erkennungen", archivedFaceCount],
        ] as const).map(([view, label, description, count]) => (
          <button
            className={workspaceView === view ? "review-page-tab review-page-tab--active" : "review-page-tab"}
            key={view}
            onClick={() => setWorkspaceView(view)}
            type="button"
          >
            <span><strong>{label}</strong><small>{description}</small></span>
            <b>{count.toLocaleString()}</b>
          </button>
        ))}
      </nav>

      {workspaceView === "suggestions" ? (
        <ReviewInbox
          personSuggestions={personSuggestions}
          reviewSuggestions={reviewSuggestions}
          busy={isMutating}
          onOpenCluster={(clusterId) => {
            setWorkspaceView("open");
            selectCluster(clusterId, { scrollToCluster: true, source: "explicit" });
          }}
          onAcceptPerson={handleAcceptSuggestions}
          onAcceptAllPeople={handleAcceptAllRecommended}
          onDismissPerson={handleDismissSuggestion}
          onAcceptReview={handleAcceptReviewSuggestions}
          onDismissReview={handleDismissReviewSuggestion}
          onOpenNewFaces={() => setWorkspaceView("open")}
        />
      ) : (
      <div className="cluster-workspace">
      <aside className="cluster-workspace__sidebar">
        <ClusterList
          mode={workspaceView}
          clusters={clusters}
          reviewGroups={reviewGroups}
          selected={selectedClusterId}
          selectedReviewGroupKey={selectedReviewGroupKey}
          highlightedClusterId={highlightedClusterId}
          onSelect={(clusterId) => selectCluster(clusterId, { scrollToCluster: true })}
          onSelectReviewGroup={(groupKey) => {
            scrollMainContentToTop();
            selectReviewGroup(groupKey as FaceReviewGroupKey);
          }}
          archiveSections={archiveSectionSummaries}
          activeArchiveSectionKey={activeArchiveSectionKey}
          onSelectArchiveSection={selectArchiveSection}
          isLoading={isListLoading}
        />
      </aside>

      <div className="cluster-workspace__main">
        {isListLoading && !selectedTarget ? (
          <div style={{ opacity: 0.5, paddingTop: 40, textAlign: "center" }}>Ansicht wird geladen…</div>
        ) : selectedTarget ? (
          <div className="cluster-detail-layout">
            <header
              ref={headerRef}
              className={
                selectedTarget.type === "cluster" && highlightedClusterId === selectedTarget.clusterId
                  ? "cluster-detail-topbar cluster-detail-topbar--highlighted"
                  : "cluster-detail-topbar"
              }
            >
              <div className="cluster-detail-topbar__identity">
                {selectedTarget.type === "cluster" ? (
                  <div className="cluster-detail-topbar__headline">
                    <div className="cluster-detail-topbar__entity">
                      <strong>{selectedPersonLabel}</strong>
                      {selectedSummary?.person_name?.trim() && (
                        <button
                          className="cluster-icon-button"
                          type="button"
                          title="Person bearbeiten"
                          aria-label="Person bearbeiten"
                          disabled={isMutating}
                          onClick={() => {
                            ensurePersonsLoaded();
                            setDetailModal({ type: "manage_person" });
                          }}
                        >
                          ✎
                        </button>
                      )}
                    </div>
                    <span className="cluster-detail-topbar__divider" aria-hidden="true">
                      /
                    </span>
                    <div className="cluster-detail-topbar__entity cluster-detail-topbar__entity--secondary">
                      <span>{selectedClusterLabel}</span>
                      <button
                        className="cluster-icon-button"
                          type="button"
                          title="Gesichtsgruppe umbenennen"
                          aria-label="Gesichtsgruppe umbenennen"
                        disabled={isMutating}
                        onClick={() => setDetailModal({ type: "rename_cluster" })}
                      >
                        ✎
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="cluster-detail-topbar__headline">
                    <div className="cluster-detail-topbar__entity">
                      <strong>{selectedClusterLabel}</strong>
                    </div>
                  </div>
                )}

              </div>

              <div className="cluster-assign-bar">
                <div className="cluster-person-combobox">
                  <input
                    type="text"
                    className="cluster-person-combobox__input"
                    placeholder="Person suchen oder neu anlegen…"
                    value={assignQuery}
                    disabled={isMutating || targetFaceIds.length === 0}
                    onChange={(event) => {
                      setAssignQuery(event.target.value);
                      setAssignMenuOpen(true);
                    }}
                    onFocus={() => setAssignMenuOpen(true)}
                    onBlur={() => window.setTimeout(() => setAssignMenuOpen(false), 120)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && canAssign) {
                        event.preventDefault();
                        assignToPerson(assignQueryTrimmed);
                      } else if (event.key === "Escape") {
                        setAssignMenuOpen(false);
                      }
                    }}
                  />
                  {assignMenuOpen &&
                    (filteredPersons.length > 0 || (assignQueryTrimmed !== "" && !exactPersonMatch)) && (
                      <ul className="cluster-person-combobox__menu" role="listbox">
                        {assignQueryTrimmed !== "" && !exactPersonMatch && (
                          <li>
                            <button
                              type="button"
                              className="cluster-person-combobox__option cluster-person-combobox__option--create"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => assignToPerson(assignQueryTrimmed)}
                            >
                              ＋ Neue Person „{assignQueryTrimmed}“ anlegen
                            </button>
                          </li>
                        )}
                        {filteredPersons.map((person) => (
                          <li key={person.id}>
                            <button
                              type="button"
                              className="cluster-person-combobox__option"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => assignToPerson(person.name)}
                            >
                              {person.name}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                </div>

                <button
                  className="cluster-action-button cluster-action-button--primary"
                  type="button"
                  disabled={!canAssign || isMutating}
                  onClick={() => assignToPerson(assignQueryTrimmed)}
                >
                  {hasFaceSelection
                    ? `${selectedFaceIds.length} Gesichter zuweisen`
                    : "Gesichtsgruppe zuweisen"}
                </button>

              </div>

              <div className="cluster-detail-toolbar">
                <div className="cluster-detail-toolbar__selection">
                  <button
                    className="cluster-selection-status__link"
                    type="button"
                    onClick={handleSelectAllFaces}
                    disabled={currentFaces.length === 0}
                  >
                    {hasFaceSelection && selectedFaceIds.length === currentFaces.length
                      ? "Auswahl aufheben"
                      : "Alle auswählen"}
                  </button>
                  {hasFaceSelection ? (
                    <>
                      <span className="cluster-selection-status__count">
                        {selectedFaceIds.length} von {currentFaces.length} markiert
                      </span>
                      <button
                        className="cluster-selection-status__link"
                        type="button"
                        onClick={() => setSelectedFaceIds([])}
                      >
                        Markierung entfernen
                      </button>
                    </>
                  ) : (
                    <span className="cluster-selection-status__hint">
                      Ohne Auswahl gilt die Aktion für die ganze Gesichtsgruppe
                    </span>
                  )}
                </div>

                {secondaryActions.length > 0 && (
                  <div className="cluster-quick-actions-inline">
                    <span className="cluster-quick-actions-inline__label">
                      {hasFaceSelection ? "Ausgewählte aussortieren:" : "Gesichtsgruppe aussortieren:"}
                    </span>
                    {secondaryActions.map((item) => {
                      // Removing faces only makes sense for a partial pick.
                      const requiresSelection = item.action === "remove_from_cluster";
                      return (
                        <button
                          key={item.action}
                          type="button"
                          className="cluster-action-button"
                          title={
                            requiresSelection && !hasFaceSelection
                              ? "Zuerst einzelne Gesichter markieren"
                              : item.description
                          }
                          disabled={
                            isMutating ||
                            targetFaceIds.length === 0 ||
                            (requiresSelection && !hasFaceSelection)
                          }
                          onClick={() => runQuickAction(item.action)}
                        >
                          {item.label}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </header>

            <div
              ref={detailContentRef}
              className="cluster-detail-layout__content"
              data-cluster-scroll-container="true"
            >
              {isDetailsLoading && detailSections.length === 0 ? (
                <div style={{ opacity: 0.5, paddingTop: 20, textAlign: "center" }}>
                  Gesichter werden geladen…
                </div>
              ) : (
                <div className="cluster-group-stack">
                  {visibleSections.map((section) => {
                    const sectionFaceIds = section.faces.map((face) => face.id);
                    const allMarked =
                      section.loaded &&
                      sectionFaceIds.length > 0 &&
                      sectionFaceIds.every((id) => selectedFaceIdSet.has(id));
                    const isActiveSection = activeSectionKey === section.key;
                    return (
                      <section
                        className={
                          isActiveSection
                            ? "cluster-group-card cluster-group-card--active"
                            : "cluster-group-card"
                        }
                        key={section.key}
                        data-section-key={section.key}
                        ref={(node) => {
                          sectionRefs.current[section.key] = node;
                        }}
                        onClick={() => {
                          if (section.clusterId != null && !isActiveSection) {
                            selectCluster(section.clusterId, { source: "explicit" });
                          }
                        }}
                      >
                        <header className="cluster-group-card__header">
                          <div>
                            <strong>{section.label}</strong>
                            <span>{section.faceCount} Gesichter</span>
                          </div>
                          <button
                            className="cluster-selection-status__link"
                            type="button"
                            disabled={!section.loaded || sectionFaceIds.length === 0}
                            onClick={(event) => {
                              event.stopPropagation();
                              setFaceGroupSelection(sectionFaceIds, !allMarked);
                            }}
                          >
                            {allMarked ? "Markierung entfernen" : "Alle auswählen"}
                          </button>
                        </header>
                        <ClusterFacesGrid
                          faces={section.faces}
                          reservedCount={section.faceCount}
                          loaded={section.loaded}
                          selectedFaceIds={selectedFaceIds}
                          onToggleFace={toggleFaceSelection}
                          onOpenImage={(face, groupFaces) =>
                            void openFaceContext(
                              face,
                              groupFaces,
                              section.label,
                              faceGalleryContextLabel,
                            )
                          }
                        />
                      </section>
                    );
                  })}
                  {hiddenSectionCount > 0 && (
                    <button
                      className="cluster-nav-load-more"
                      type="button"
                      onClick={() =>
                        setVisibleSectionCount((count) => count + SECTION_BATCH_SIZE)
                      }
                    >
                      Weitere {Math.min(SECTION_BATCH_SIZE, hiddenSectionCount)} Gruppen anzeigen
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ opacity: 0.5, paddingTop: 40, textAlign: "center" }}>
            Kein Eintrag ausgewählt.
          </div>
        )}
      </div>
      </div>
      )}

      {detailModal?.type === "rename_cluster" && selectedTarget?.type === "cluster" && (
        <div className="modal-backdrop" onMouseDown={() => setDetailModal(null)}>
          <section
            className="cluster-action-modal cluster-action-modal--narrow"
            role="dialog"
            aria-modal="true"
            aria-labelledby="cluster-rename-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="cluster-action-modal__header">
              <div>
                <span className="cluster-control-card__eyebrow">Gesichtsgruppe verwalten</span>
                <h2 id="cluster-rename-title">Gesichtsgruppe umbenennen</h2>
                <p>Gib der Gruppe bei Bedarf einen kurzen Namen, zum Beispiel „Kindheit“ oder „Profil“.</p>
              </div>
              <button
                className="modal-close-button"
                onClick={() => setDetailModal(null)}
                aria-label="Schließen"
                type="button"
              >
                ×
              </button>
            </header>
            <div className="cluster-assignment-form">
              <input
                type="text"
                value={clusterLabelInput}
                onChange={(event) => setClusterLabelInput(event.target.value)}
                className="cluster-assignment-form__input"
                placeholder={`Gruppe ${selectedTarget.clusterId}`}
                disabled={isMutating}
              />
            </div>
            <footer className="cluster-action-modal__footer">
              <button className="secondary-button" onClick={() => setDetailModal(null)} type="button">
                Abbrechen
              </button>
              <button
                className="primary-button"
                disabled={isMutating || !clusterLabelInput.trim()}
                onClick={() => void handleRenameCluster().then(() => setDetailModal(null))}
                type="button"
              >
                Speichern
              </button>
            </footer>
          </section>
        </div>
      )}

      {detailModal?.type === "manage_person" && selectedTarget?.type === "cluster" && selectedPersonId && (
        <div className="modal-backdrop" onMouseDown={() => setDetailModal(null)}>
          <section
            className="cluster-action-modal cluster-action-modal--narrow"
            role="dialog"
            aria-modal="true"
            aria-labelledby="person-manage-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="cluster-action-modal__header">
              <div>
                <span className="cluster-control-card__eyebrow">Person verwalten</span>
                <h2 id="person-manage-title">{selectedSummary?.person_name}</h2>
                <p>Ändere den Namen oder entferne die Person und sortiere ihre Gesichter neu ein.</p>
              </div>
              <button
                className="modal-close-button"
                onClick={() => setDetailModal(null)}
                aria-label="Schließen"
                type="button"
              >
                ×
              </button>
            </header>
            <div className="cluster-action-modal__stack">
              <div className="cluster-assignment-form">
                <input
                  type="text"
                  value={personNameInput}
                  onChange={(event) => setPersonNameInput(event.target.value)}
                  className="cluster-assignment-form__input"
                  placeholder="Personenname"
                  disabled={isMutating}
                />
                <button
                  className="primary-button"
                  disabled={isMutating || !personNameInput.trim()}
                  onClick={() => void handleRenamePerson().then(() => setDetailModal(null))}
                  type="button"
                >
                  Person umbenennen
                </button>
              </div>

              <div className="cluster-danger-zone">
                <strong>Person löschen</strong>
                <p>Wähle, wohin die bisher zugeordneten Gesichter beim Löschen einsortiert werden.</p>
                <select
                  className="app-select"
                  value={personDeleteTarget}
                  onChange={(event) =>
                    setPersonDeleteTarget(
                      event.target.value as "unassigned" | "unknown_person" | "not_face",
                    )
                  }
                  disabled={isMutating}
                >
                  <option value="unassigned">Zu „Neue Gesichter“ verschieben</option>
                  <option value="unknown_person">Als „Unbekannte Person“ ablegen</option>
                  <option value="not_face">Als „Kein Gesicht“ ablegen</option>
                </select>
                <button
                  className="neon-card cluster-danger-zone__button"
                  disabled={isMutating}
                  onClick={() => void handleDeletePerson().then(() => setDetailModal(null))}
                  type="button"
                >
                  Person löschen
                </button>
              </div>
            </div>
          </section>
        </div>
      )}

      {faceGallery && (
        <FaceGroupGallery
          faces={faceGallery.faces}
          initialFaceId={faceGallery.initialFaceId}
          initialImage={faceGallery.initialImage}
          contextLabel={faceGallery.contextLabel}
          groupLabel={faceGallery.groupLabel}
          onClose={() => setFaceGallery(null)}
          onNavigateToCluster={(clusterId) => {
            setFaceGallery(null);
            selectCluster(clusterId, { scrollToCluster: true, source: "explicit" });
          }}
        />
      )}
    </div>
  );
};

export default ClusterPage;
