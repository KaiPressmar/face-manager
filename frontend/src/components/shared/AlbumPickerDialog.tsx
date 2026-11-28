import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { imageThumbnailUrl } from "../../utils/api";
import type { GeoPhotoSelection } from "../../utils/geoPhotoSelection";
import {
  addPhotoAlbumImages, createPhotoAlbum, deletePhotoAlbum, updatePhotoAlbum, usePhotoAlbums,
  type AlbumJourney, type AlbumPhotos, type PhotoAlbum,
} from "../../utils/photoAlbums";
import "./AlbumPickerDialog.css";

const albumNameLimit = 120;
const numbers = new Intl.NumberFormat("de-DE");
const photoCountLabel = (count: number) => `${numbers.format(count)} ${count === 1 ? "Foto" : "Fotos"}`;

function AlbumDialog({
  title, onClose, pending, suspended = false, focusClose = true, children,
}: {
  title: string;
  onClose: () => void;
  pending: boolean;
  suspended?: boolean;
  focusClose?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  useModalFocus(ref, onClose, !pending);
  return <div className="photo-album-overlay" {...(suspended ? { inert: "" } : {})}>
    <section ref={ref} role="dialog" aria-modal="true" aria-label={title} className="photo-album-dialog">
      <header>
        <h2>{title}</h2>
        <button type="button" data-initial-focus={focusClose ? true : undefined} disabled={pending} onClick={onClose}>Schließen</button>
      </header>
      {children}
    </section>
  </div>;
}

interface AlbumPickerProps {
  imageIds?: readonly number[];
  selection?: GeoPhotoSelection;
  photoCount?: number;
  initialAlbumId?: string;
  initialName?: string;
  journey?: AlbumJourney;
  onClose: () => void;
  onSaved?: (album: PhotoAlbum) => void;
}

export default function AlbumPickerDialog({
  imageIds, selection, photoCount, initialAlbumId, initialName, journey, onClose, onSaved,
}: AlbumPickerProps) {
  const count = selection ? photoCount : imageIds?.length;
  const photos: AlbumPhotos | undefined = selection ? { selection } : imageIds?.length ? { image_ids: imageIds } : undefined;
  const existingAlbumPicker = Boolean(photos && !journey);
  const { albums, loading, error, reload } = usePhotoAlbums(existingAlbumPicker);
  const [choice, setChoice] = useState<string | undefined>(initialAlbumId ?? (journey ? "" : undefined));
  const [name, setName] = useState(initialName?.slice(0, albumNameLimit) ?? "");
  const [pending, setPending] = useState(false);
  const [saveError, setSaveError] = useState("");
  const request = useRef(false);
  const destination = existingAlbumPicker ? choice ?? (albums.length ? albums[0].id : "") : "";
  const creating = destination === "";
  const validDestination = creating ? Boolean(name.trim()) : albums.some((album) => album.id === destination);
  const canSave = !pending && validDestination && (!photos || count !== 0);
  const title = photos ? "Zum Album hinzufügen" : "Album erstellen";

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (request.current || !canSave) return;
    request.current = true;
    setPending(true);
    setSaveError("");
    try {
      const album = photos && !creating
        ? await addPhotoAlbumImages(destination, photos)
        : await createPhotoAlbum(name.trim(), photos, journey);
      onSaved?.(album);
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Das Album konnte nicht gespeichert werden.");
    } finally {
      request.current = false;
      setPending(false);
    }
  };

  return <AlbumDialog title={title} onClose={onClose} pending={pending} focusClose={Boolean(photos)}>
    <form className="photo-album-form" onSubmit={(event) => void save(event)}>
      {photos && <p>{count == null ? "Fotos aus der aktuellen Auswahl" : photoCountLabel(count)}</p>}
      {selection && selection.image_ids === undefined && <p>
        Beim Speichern werden alle aktuellen Treffer der Auswahl hinzugefügt.
      </p>}
      {existingAlbumPicker && !albums.length && loading && <p role="status">Alben werden geladen …</p>}
      {existingAlbumPicker && error && <p role="alert">{error} <button type="button" onClick={reload}>Erneut versuchen</button></p>}
      {existingAlbumPicker && <label>
        <span id="photo-album-destination">Zielalbum</span>
        <select aria-labelledby="photo-album-destination" disabled={pending} value={destination} onChange={(event) => setChoice(event.target.value)}>
          <option value="">Neues Album</option>
          {albums.map((album) => <option key={album.id} value={album.id}>{album.name}</option>)}
        </select>
      </label>}
      {creating && <label>Albumname
        <input data-initial-focus={photos ? undefined : true} disabled={pending} required maxLength={albumNameLimit} value={name}
          onChange={(event) => setName(event.target.value)} />
      </label>}
      {saveError && <p role="alert">{saveError}</p>}
      <footer>
        <button type="submit" className="neon-button" disabled={!canSave}>
          {pending ? "Wird gespeichert …" : creating ? "Album erstellen" : "Fotos hinzufügen"}
        </button>
      </footer>
    </form>
  </AlbumDialog>;
}

interface AlbumLibraryProps {
  albums: readonly PhotoAlbum[];
  loading: boolean;
  error: string;
  reload: () => void;
  activeAlbumId?: string;
  suspended?: boolean;
  onClose: () => void;
  onSelect: (album?: PhotoAlbum) => void;
  onCreate: () => void;
  onDeleted: (id: string) => void;
}

export function AlbumLibraryDialog({
  albums, loading, error, reload, activeAlbumId, suspended, onClose, onSelect, onCreate, onDeleted,
}: AlbumLibraryProps) {
  const [editing, setEditing] = useState<string>();
  const [name, setName] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [pending, setPending] = useState(false);
  const [saveError, setSaveError] = useState("");
  const request = useRef(false);

  const save = async (submit: () => Promise<unknown>, after?: () => void) => {
    if (request.current) return;
    request.current = true;
    setPending(true);
    setSaveError("");
    try {
      await submit();
      setEditing(undefined);
      setConfirmDelete(false);
      after?.();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Das Album konnte nicht gespeichert werden.");
    } finally {
      request.current = false;
      setPending(false);
    }
  };

  return <AlbumDialog title="Fotoalben" onClose={onClose} pending={pending} suspended={suspended}>
    <div className="photo-album-library">
      <div className="photo-album-library__toolbar">
        {activeAlbumId && <button type="button" disabled={pending} onClick={() => onSelect()}>Alle Fotos anzeigen</button>}
        <button type="button" className="neon-button" disabled={pending} onClick={onCreate}>Neues Album</button>
      </div>
      {loading && albums.length === 0 && <p role="status">Alben werden geladen …</p>}
      {error && <p role="alert">{error} <button type="button" onClick={reload}>Erneut versuchen</button></p>}
      {!loading && !error && !albums.length && <p>Noch keine Alben. Wähle Fotos aus oder eröffne ein leeres Album.</p>}
      <ul aria-label="Deine Alben">
        {albums.map((album) => <li key={album.id}>
          <div className="photo-album-library__item">
            {album.cover_image_id && <img src={imageThumbnailUrl(album.cover_image_id)} alt="" loading="lazy" />}
            <div><strong>{album.name}</strong><small>{photoCountLabel(album.photo_count)}</small></div>
            <button type="button" onClick={() => onSelect(album)} disabled={pending} aria-label={`Album ${album.name} anzeigen`}>Anzeigen</button>
            <button type="button" disabled={pending} aria-expanded={editing === album.id} aria-label={`Album ${album.name} bearbeiten`}
              onClick={() => {
                setEditing(editing === album.id ? undefined : album.id);
                setName(album.name);
                setConfirmDelete(false);
                setSaveError("");
              }}>Bearbeiten</button>
          </div>
          {editing === album.id && <div className="photo-album-library__editing">
            {confirmDelete ? <>
              <p>„{album.name}“ und seine Auswahl löschen? Die Fotos bleiben in der Bibliothek.</p>
              <button type="button" disabled={pending} onClick={() => setConfirmDelete(false)}>Zurück</button>
              <button type="button" className="photo-album-delete" disabled={pending}
                onClick={() => void save(() => deletePhotoAlbum(album.id), () => onDeleted(album.id))}>Album endgültig löschen</button>
            </> : <form className="photo-album-form" onSubmit={(event) => {
              event.preventDefault();
              if (name.trim() && name.trim() !== album.name) void save(() => updatePhotoAlbum(album.id, { name: name.trim() }));
            }}>
              <label>Albumname <input disabled={pending} required maxLength={albumNameLimit} value={name} onChange={(event) => setName(event.target.value)} /></label>
              <footer>
                <button type="button" className="photo-album-delete" disabled={pending} onClick={() => setConfirmDelete(true)}>Album löschen …</button>
                <button type="submit" className="neon-button" disabled={pending || !name.trim() || name.trim() === album.name}>Name speichern</button>
              </footer>
            </form>}
            {saveError && <p role="alert">{saveError}</p>}
          </div>}
        </li>)}
      </ul>
    </div>
  </AlbumDialog>;
}
