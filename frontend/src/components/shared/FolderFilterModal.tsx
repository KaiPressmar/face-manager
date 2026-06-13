import React, { useEffect, useMemo, useState } from "react";
import {
  fetchFolders,
  FolderNode,
  FolderTree,
} from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";

interface FolderFilterModalProps {
  selected: string[];
  onApply: (folders: string[]) => void;
  onClose: () => void;
}

const collectMatchingPaths = (node: FolderNode, query: string): Set<string> => {
  const matches = new Set<string>();
  const childMatches = node.children.flatMap((child) =>
    Array.from(collectMatchingPaths(child, query))
  );
  if (
    node.name.toLocaleLowerCase().includes(query) ||
    node.path.toLocaleLowerCase().includes(query) ||
    childMatches.length > 0
  ) {
    matches.add(node.path);
  }
  childMatches.forEach((path) => matches.add(path));
  return matches;
};

const expandCommonPath = (node: FolderNode, expanded: Set<string>) => {
  expanded.add(node.path);
  if (node.direct_image_count === 0 && node.children.length === 1) {
    expandCommonPath(node.children[0], expanded);
  }
};

const FolderRow: React.FC<{
  node: FolderNode;
  depth: number;
  selected: Set<string>;
  expanded: Set<string>;
  visiblePaths: Set<string> | null;
  onToggleFolder: (path: string) => void;
  onToggleExpanded: (path: string) => void;
}> = ({
  node,
  depth,
  selected,
  expanded,
  visiblePaths,
  onToggleFolder,
  onToggleExpanded,
}) => {
  if (visiblePaths && !visiblePaths.has(node.path)) return null;

  const hasChildren = node.children.length > 0;
  const isExpanded = expanded.has(node.path) || visiblePaths !== null;
  const isSelected = selected.has(node.path);

  return (
    <>
      <div
        className={`folder-tree-row${isSelected ? " folder-tree-row--selected" : ""}`}
        style={{ paddingLeft: 12 + depth * 22 }}
      >
        <button
          type="button"
          className="folder-expand-button"
          onClick={() => hasChildren && onToggleExpanded(node.path)}
          aria-label={isExpanded ? "Ordner einklappen" : "Ordner ausklappen"}
          disabled={!hasChildren}
        >
          {hasChildren ? (isExpanded ? "−" : "+") : ""}
        </button>
        <button
          type="button"
          className="folder-select-button"
          onClick={() => onToggleFolder(node.path)}
        >
          <span className="folder-icon" aria-hidden="true" />
          <span className="folder-row-label">
            <strong>{node.name}</strong>
            <small title={node.path}>{node.path}</small>
          </span>
          <span className="folder-count">{node.image_count}</span>
          <span
            className={`folder-checkbox${isSelected ? " folder-checkbox--checked" : ""}`}
            aria-hidden="true"
          >
            {isSelected ? "✓" : ""}
          </span>
        </button>
      </div>
      {hasChildren &&
        isExpanded &&
        node.children.map((child) => (
          <FolderRow
            key={child.path}
            node={child}
            depth={depth + 1}
            selected={selected}
            expanded={expanded}
            visiblePaths={visiblePaths}
            onToggleFolder={onToggleFolder}
            onToggleExpanded={onToggleExpanded}
          />
        ))}
    </>
  );
};

const FolderFilterModal: React.FC<FolderFilterModalProps> = ({
  selected,
  onApply,
  onClose,
}) => {
  const [tree, setTree] = useState<FolderTree | null>(null);
  const [draft, setDraft] = useState(() => new Set(selected));
  const [expanded, setExpanded] = useState(() => new Set<string>());
  const [search, setSearch] = useState("");

  useEffect(() => {
    fetchFolders().then((data) => {
      setTree(data);
      setExpanded((current) => {
        const next = new Set(current);
        data.roots.forEach((root) => expandCommonPath(root, next));
        return next;
      });
    });
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const visiblePaths = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    if (!query || !tree) return null;
    const paths = new Set<string>();
    tree.roots.forEach((root) =>
      collectMatchingPaths(root, query).forEach((path) => paths.add(path))
    );
    return paths;
  }, [search, tree]);

  const toggleFolder = (path: string) => {
    setDraft((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const toggleExpanded = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section
        className="folder-browser"
        role="dialog"
        aria-modal="true"
        aria-labelledby="folder-browser-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="folder-browser-header">
          <div>
            <span className="eyebrow">Bibliothek filtern</span>
            <h2 id="folder-browser-title">Ordner auswählen</h2>
            <p>
              Ein ausgewählter Ordner schließt automatisch alle Unterordner ein.
            </p>
          </div>
          <button className="modal-close-button" onClick={onClose} aria-label="Schließen">
            ×
          </button>
        </header>

        <div className="folder-browser-toolbar">
          <label className="folder-search">
            <span aria-hidden="true">⌕</span>
            <input
              autoFocus
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Ordner oder Pfad suchen"
            />
          </label>
          <button type="button" onClick={() => setDraft(new Set())}>
            Auswahl leeren
          </button>
        </div>

        {draft.size > 0 && (
          <div className="selected-folder-strip">
            {Array.from(draft).map((path) => (
              <button key={path} title={path} onClick={() => toggleFolder(path)}>
                <span>{pathBasename(path)}</span>
                <b>×</b>
              </button>
            ))}
          </div>
        )}

        <div className="folder-tree">
          {!tree ? (
            <div className="folder-browser-state">Ordner werden geladen…</div>
          ) : tree.roots.length === 0 ? (
            <div className="folder-browser-state">Noch keine Ordner entdeckt.</div>
          ) : visiblePaths && visiblePaths.size === 0 ? (
            <div className="folder-browser-state">Kein passender Ordner gefunden.</div>
          ) : (
            tree.roots.map((root) => (
              <FolderRow
                key={root.path}
                node={root}
                depth={0}
                selected={draft}
                expanded={expanded}
                visiblePaths={visiblePaths}
                onToggleFolder={toggleFolder}
                onToggleExpanded={toggleExpanded}
              />
            ))
          )}
        </div>

        <footer className="folder-browser-footer">
          <div>
            <strong>{draft.size || "Alle"}</strong>
            <span>{draft.size === 1 ? " Ordner ausgewählt" : draft.size ? " Ordner ausgewählt" : " Ordner sichtbar"}</span>
            {tree && <small>{tree.image_count} Bilder in der Bibliothek</small>}
          </div>
          <button className="secondary-button" onClick={onClose}>
            Abbrechen
          </button>
          <button
            className="primary-button"
            onClick={() => onApply(Array.from(draft))}
          >
            Filter anwenden
          </button>
        </footer>
      </section>
    </div>
  );
};

export default FolderFilterModal;
