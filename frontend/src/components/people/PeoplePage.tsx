import React, { useEffect, useState } from "react";
import { FaceImage, fetchImages } from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import PersonFilter from "./PersonFilter";
import ImageGrid from "./ImageGrid";
import FolderPickerModal from "../shared/FolderPickerModal";
import FolderFilterModal from "../shared/FolderFilterModal";

export type ImageGroupingMode = "date" | "folder";
export type SortDirection = "desc" | "asc";

const PeoplePage = () => {
  const [images, setImages] = useState<FaceImage[]>([]);
  const [selectedPersons, setSelectedPersons] = useState<string[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  const [showFolderFilter, setShowFolderFilter] = useState(false);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [groupingMode, setGroupingMode] = useState<ImageGroupingMode>("date");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);

    const loadImages = () =>
      fetchImages(selectedFolders)
      .then((data) => {
        if (isMounted) setImages(data);
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });

    loadImages();
    const interval = setInterval(loadImages, 15000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [selectedFolders]);

  return (
    <>
      <div className="people-toolbar">
        <PersonFilter
          images={images}
          selected={selectedPersons}
          onChange={setSelectedPersons}
        />

        <div className="people-toolbar-actions">
          <div className="people-sort-panel">
            <label className="people-sort-panel__field">
              <span>Gruppieren nach</span>
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
                  current.filter((path) => path !== folder)
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
        selectedPersons={selectedPersons}
        groupingMode={groupingMode}
        sortDirection={sortDirection}
        isLoading={isLoading}
        onImageDeleted={(imageId) =>
          setImages((current) =>
            current.filter((image) => image.id !== imageId)
          )
        }
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
