import React, { useEffect, useRef, useState } from "react";
import { FaceImage, fetchImages } from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import PersonFilter from "./PersonFilter";
import ImageGrid from "./ImageGrid";
import FolderPickerModal from "../shared/FolderPickerModal";
import FolderFilterModal from "../shared/FolderFilterModal";

export type ImageGroupingMode = "date" | "folder";
export type SortDirection = "desc" | "asc";

const PAGE_SIZE = 40;

const PeoplePage = () => {
  const [images, setImages] = useState<FaceImage[]>([]);
  const [availablePersons, setAvailablePersons] = useState<string[]>([]);
  const [selectedPersons, setSelectedPersons] = useState<string[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  const [showFolderFilter, setShowFolderFilter] = useState(false);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [totalImages, setTotalImages] = useState(0);
  const [groupingMode, setGroupingMode] = useState<ImageGroupingMode>("date");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const latestQueryRef = useRef(0);
  const loadedCountRef = useRef(PAGE_SIZE);

  useEffect(() => {
    loadedCountRef.current = Math.max(PAGE_SIZE, images.length || PAGE_SIZE);
  }, [images.length]);

  useEffect(() => {
    let isMounted = true;
    const requestId = latestQueryRef.current + 1;
    latestQueryRef.current = requestId;
    setIsLoading(true);
    setHasMore(false);

    const loadImages = async (limit: number) => {
      try {
        const page = await fetchImages({
          folders: selectedFolders,
          persons: selectedPersons,
          sortBy: groupingMode,
          sortDirection,
          limit,
          offset: 0,
        });
        if (!isMounted || latestQueryRef.current !== requestId) return;

        setImages(page.items);
        setAvailablePersons(page.available_persons);
        setTotalImages(page.total);
        setHasMore(page.has_more);
        setSelectedPersons((current) => {
          const next = current.filter((person) =>
            page.available_persons.includes(person),
          );
          return next.length === current.length ? current : next;
        });
        loadedCountRef.current = Math.max(PAGE_SIZE, page.items.length || PAGE_SIZE);
      } finally {
        if (isMounted && latestQueryRef.current === requestId) {
          setIsLoading(false);
        }
      }
    };

    void loadImages(PAGE_SIZE);

    return () => {
      isMounted = false;
    };
  }, [groupingMode, selectedFolders, selectedPersons, sortDirection]);

  useEffect(() => {
    let isMounted = true;
    const refreshVisibleImages = () => {
      const requestId = latestQueryRef.current;
      void fetchImages({
        folders: selectedFolders,
        persons: selectedPersons,
        sortBy: groupingMode,
        sortDirection,
        limit: loadedCountRef.current,
        offset: 0,
      }).then((page) => {
        if (!isMounted || latestQueryRef.current !== requestId) return;
        setImages(page.items);
        setAvailablePersons(page.available_persons);
        setTotalImages(page.total);
        setHasMore(page.has_more);
      });
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible" && !isLoadingMore) {
        refreshVisibleImages();
      }
    };

    const handleWindowFocus = () => {
      if (!isLoadingMore) {
        refreshVisibleImages();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleWindowFocus);

    return () => {
      isMounted = false;
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleWindowFocus);
    };
  }, [groupingMode, isLoadingMore, selectedFolders, selectedPersons, sortDirection]);

  const loadMoreImages = async () => {
    if (isLoading || isLoadingMore || !hasMore) return;
    const requestId = latestQueryRef.current;
    setIsLoadingMore(true);
    try {
      const page = await fetchImages({
        folders: selectedFolders,
        persons: selectedPersons,
        sortBy: groupingMode,
        sortDirection,
        limit: PAGE_SIZE,
        offset: loadedCountRef.current,
      });
      if (latestQueryRef.current !== requestId) return;
      setImages((current) => {
        const merged = new Map(current.map((image) => [image.id, image]));
        page.items.forEach((image) => merged.set(image.id, image));
        const next = Array.from(merged.values());
        loadedCountRef.current = next.length;
        return next;
      });
      setAvailablePersons(page.available_persons);
      setTotalImages(page.total);
      setHasMore(page.has_more);
    } finally {
      setIsLoadingMore(false);
    }
  };

  return (
    <>
      <div className="people-toolbar">
        <PersonFilter
          persons={availablePersons}
          selected={selectedPersons}
          onChange={setSelectedPersons}
        />

        <div className="people-toolbar-actions">
          <div className="people-sort-panel">
            <label className="people-sort-panel__field">
              <span>Sortieren nach</span>
              <select
                value={groupingMode}
                onChange={(event) =>
                  setGroupingMode(event.target.value as ImageGroupingMode)
                }
              >
                <option value="date">Erstellungsdatum</option>
                <option value="folder">Ordnerpfad</option>
              </select>
            </label>

            <label className="people-sort-panel__field">
              <span>Reihenfolge</span>
              <select
                value={sortDirection}
                onChange={(event) =>
                  setSortDirection(event.target.value as SortDirection)
                }
              >
                <option value="desc">Absteigend</option>
                <option value="asc">Aufsteigend</option>
              </select>
            </label>
          </div>

          <div className="people-toolbar-count">
            {totalImages.toLocaleString()} Bilder
          </div>

          <button
            className={`folder-filter-trigger${selectedFolders.length ? " folder-filter-trigger--active" : ""}`}
            onClick={() => setShowFolderFilter(true)}
          >
            <span className="folder-icon" aria-hidden="true" />
            <span>
              <strong>Ordnerfilter</strong>
              <small>
                {selectedFolders.length
                  ? `${selectedFolders.length} ausgewählt`
                  : "Alle Ordner"}
              </small>
            </span>
            {selectedFolders.length > 0 && <b>{selectedFolders.length}</b>}
          </button>

          <button className="neon-card import-folder-button" onClick={() => setShowPicker(true)}>
            Ordner hinzufügen
          </button>
        </div>
      </div>

      {selectedFolders.length > 0 && (
        <div className="active-folder-filters">
          <span>Aktive Ordner</span>
          {selectedFolders.map((folder) => (
            <button
              key={folder}
              title={folder}
              onClick={() =>
                setSelectedFolders((current) =>
                  current.filter((path) => path !== folder),
                )
              }
            >
              {pathBasename(folder)}
              <b>×</b>
            </button>
          ))}
          <button className="clear-folder-filters" onClick={() => setSelectedFolders([])}>
            Alle löschen
          </button>
        </div>
      )}

      <ImageGrid
        images={images}
        isLoading={isLoading}
        hasMore={hasMore}
        isLoadingMore={isLoadingMore}
        onLoadMore={loadMoreImages}
        onImageDeleted={(imageId) => {
          setImages((current) => current.filter((image) => image.id !== imageId));
          setTotalImages((current) => Math.max(0, current - 1));
        }}
      />

      {showPicker && (
        <FolderPickerModal onClose={() => setShowPicker(false)} />
      )}
      {showFolderFilter && (
        <FolderFilterModal
          selected={selectedFolders}
          onClose={() => setShowFolderFilter(false)}
          onApply={(folders) => {
            setSelectedFolders(folders);
            setShowFolderFilter(false);
          }}
        />
      )}
    </>
  );
};

export default PeoplePage;
