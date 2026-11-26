import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { listPersons } from "../../utils/api";
import {
  fetchManualFacePreview, fetchManualFaceSuggestions, saveManualFace, type NormalizedFaceRectangle, type SavedManualFace,
} from "../../utils/manualFaces";
import PersonAssignment, { type PersonOption } from "../clusters/PersonAssignment";
import "./ManualFaceEditor.css";

type Point = { x: number; y: number };
type Corner = "nw" | "ne" | "sw" | "se";
type Gesture =
  | { id: number; start: Point; mode: "draw"; original: NormalizedFaceRectangle | null }
  | { id: number; start: Point; mode: "move" | Corner; original: NormalizedFaceRectangle };

const corners: Corner[] = ["nw", "ne", "sw", "se"];
const minimumSpan = 0.001;
const centered = { x: .36, y: .32, width: .28, height: .36 };
const arrowSteps: Record<string, Point> = {
  ArrowLeft: { x: -1, y: 0 }, ArrowRight: { x: 1, y: 0 }, ArrowUp: { x: 0, y: -1 }, ArrowDown: { x: 0, y: 1 },
};
const clamp = (number: number, low: number, high: number) => Math.max(low, Math.min(high, number));
const percent = (number: number) => `${Math.round(number * 100)} %`;

function rectangleStyle(rectangle: NormalizedFaceRectangle) {
  return {
    left: `${rectangle.x * 100}%`, top: `${rectangle.y * 100}%`,
    width: `${rectangle.width * 100}%`, height: `${rectangle.height * 100}%`,
  };
}

function pointOnImage(element: HTMLElement, clientX: number, clientY: number): Point {
  const image = element.getBoundingClientRect();
  return { x: clamp((clientX - image.left) / image.width, 0, 1), y: clamp((clientY - image.top) / image.height, 0, 1) };
}

function moveRectangle(rectangle: NormalizedFaceRectangle, delta: Point): NormalizedFaceRectangle {
  return {
    ...rectangle,
    x: clamp(rectangle.x + delta.x, 0, 1 - rectangle.width),
    y: clamp(rectangle.y + delta.y, 0, 1 - rectangle.height),
  };
}

function resizeRectangle(rectangle: NormalizedFaceRectangle, corner: Corner, delta: Point): NormalizedFaceRectangle {
  const right = rectangle.x + rectangle.width;
  const bottom = rectangle.y + rectangle.height;
  const x = corner.endsWith("w") ? clamp(rectangle.x + delta.x, 0, right - minimumSpan) : rectangle.x;
  const y = corner.startsWith("n") ? clamp(rectangle.y + delta.y, 0, bottom - minimumSpan) : rectangle.y;
  return {
    x, y,
    width: (corner.endsWith("e") ? clamp(right + delta.x, x + minimumSpan, 1) : right) - x,
    height: (corner.startsWith("s") ? clamp(bottom + delta.y, y + minimumSpan, 1) : bottom) - y,
  };
}

function rectangleForGesture(gesture: Gesture, point: Point): NormalizedFaceRectangle {
  const delta = { x: point.x - gesture.start.x, y: point.y - gesture.start.y };
  if (gesture.mode === "draw") {
    return {
      x: Math.min(gesture.start.x, point.x), y: Math.min(gesture.start.y, point.y),
      width: Math.abs(delta.x), height: Math.abs(delta.y),
    };
  }
  return gesture.mode === "move" ? moveRectangle(gesture.original, delta) : resizeRectangle(gesture.original, gesture.mode, delta);
}

function validRectangle(rectangle: NormalizedFaceRectangle | null): rectangle is NormalizedFaceRectangle {
  return Boolean(rectangle && rectangle.x >= 0 && rectangle.y >= 0 &&
    rectangle.width >= minimumSpan && rectangle.height >= minimumSpan &&
    rectangle.x + rectangle.width <= 1 + Number.EPSILON && rectangle.y + rectangle.height <= 1 + Number.EPSILON);
}

export interface ManualFaceEditorSource {
  imageId: number;
  label: string;
  contentHash?: string | null;
  allowSuggestions?: boolean;
  existing: NormalizedFaceRectangle[];
}

export default function ManualFaceEditor({ source, onClose, onSaved }: {
  source: ManualFaceEditorSource;
  onClose: () => void;
  onSaved: (face: SavedManualFace, name: string, rectangle: NormalizedFaceRectangle) => void;
}) {
  const titleId = useId();
  const instructionsId = useId();
  const coordinatesId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const savingRef = useRef(false);
  const suggestionRequest = useRef<AbortController | null>(null);
  const [photo, setPhoto] = useState<{ url: string; token: string; originalAvailable: boolean }>();
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);
  const [photoError, setPhotoError] = useState("");
  const [photoRetry, setPhotoRetry] = useState(0);
  const [rectangle, setRectangle] = useState<NormalizedFaceRectangle | null>(null);
  const [dragging, setDragging] = useState(false);
  const [people, setPeople] = useState<PersonOption[]>([]);
  const [peopleStatus, setPeopleStatus] = useState<"loading" | "ready" | "error">("loading");
  const [peopleRetry, setPeopleRetry] = useState(0);
  const [suggestions, setSuggestions] = useState<NormalizedFaceRectangle[]>();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [suggestionError, setSuggestionError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const ready = photo !== undefined && dimensions !== null && !photoError;
  const canAssign = ready && !dragging && validRectangle(rectangle);
  const allowSuggestions = source.allowSuggestions !== false && photo?.originalAvailable !== false;
  useModalFocus(dialogRef, onClose, !saving, true, (target) =>
    target instanceof HTMLElement && target.matches('[role="combobox"][aria-expanded="true"]'));

  useEffect(() => {
    const request = new AbortController();
    let url: string | undefined;
    setPhoto(undefined);
    setDimensions(null);
    setPhotoError("");
    void fetchManualFacePreview(source.imageId, request.signal).then(({ blob, token, originalAvailable }) => {
      if (request.signal.aborted) return;
      url = URL.createObjectURL(blob);
      setPhoto({ url, token, originalAvailable });
    }).catch((error) => {
      if (!request.signal.aborted) {
        setPhotoError(error instanceof Error ? error.message : "Das Foto konnte nicht sicher geladen werden.");
      }
    });
    return () => {
      request.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoRetry, source.imageId]);

  useEffect(() => {
    let disposed = false;
    setPeopleStatus("loading");
    void listPersons().then((items) => {
      if (disposed) return;
      setPeople(items);
      setPeopleStatus("ready");
    }).catch(() => { if (!disposed) setPeopleStatus("error"); });
    return () => { disposed = true; };
  }, [peopleRetry]);

  useEffect(() => () => suggestionRequest.current?.abort(), []);

  const place = (candidate: NormalizedFaceRectangle) => {
    setRectangle(candidate);
    setShowSuggestions(false);
    setSaveError("");
    canvasRef.current?.focus({ preventScroll: true });
  };

  const toggleSuggestions = async () => {
    if (suggestionRequest.current || saving || !photo) return;
    if (suggestions) {
      setShowSuggestions((previous) => !previous);
      return;
    }
    const request = new AbortController();
    suggestionRequest.current = request;
    setSuggestionsLoading(true);
    setShowSuggestions(true);
    setSuggestionError("");
    try {
      const items = await fetchManualFaceSuggestions(source.imageId, photo.token, request.signal);
      if (!request.signal.aborted) setSuggestions(items.filter(validRectangle));
    } catch (error) {
      if (!request.signal.aborted) {
        setSuggestionError(error instanceof Error ? error.message : "Vorschläge konnten nicht geladen werden.");
      }
    } finally {
      if (suggestionRequest.current === request) {
        suggestionRequest.current = null;
        if (!request.signal.aborted) setSuggestionsLoading(false);
      }
    }
  };

  const startPointer = (event: PointerEvent<HTMLDivElement>) => {
    if (!ready || saving || gesture.current || event.button !== 0 || (event.target as Element).closest("button")) return;
    event.preventDefault();
    const start = pointOnImage(event.currentTarget, event.clientX, event.clientY);
    const corner = (event.target as Element).closest<HTMLElement>("[data-corner]")?.dataset.corner as Corner | undefined;
    const inRectangle = (event.target as Element).closest(".manual-face-rectangle");
    gesture.current = rectangle && (corner || inRectangle)
      ? { id: event.pointerId, start, mode: corner ?? "move", original: rectangle }
      : { id: event.pointerId, start, mode: "draw", original: rectangle };
    setDragging(true);
    setShowSuggestions(false);
    setSaveError("");
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const movePointer = (event: PointerEvent<HTMLDivElement>) => {
    const active = gesture.current;
    if (active?.id !== event.pointerId) return;
    setRectangle(rectangleForGesture(active, pointOnImage(event.currentTarget, event.clientX, event.clientY)));
  };

  const endPointer = (event: PointerEvent<HTMLDivElement>, commit: boolean) => {
    const active = gesture.current;
    if (active?.id !== event.pointerId) return;
    gesture.current = null;
    setDragging(false);
    const candidate = commit ? rectangleForGesture(active, pointOnImage(event.currentTarget, event.clientX, event.clientY)) : null;
    setRectangle(validRectangle(candidate) ? candidate : active.original);
  };

  const onCanvasKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!ready || saving || dragging || event.target !== event.currentTarget) return;
    if (!rectangle && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      place(centered);
      return;
    }
    const direction = arrowSteps[event.key];
    if (!rectangle || !direction) return;
    event.preventDefault();
    const step = event.altKey ? .0025 : .01;
    const delta = { x: direction.x * step, y: direction.y * step };
    setRectangle(event.shiftKey ? resizeRectangle(rectangle, "se", delta) : moveRectangle(rectangle, delta));
    setSaveError("");
  };

  const assign = async (name: string) => {
    if (savingRef.current || !canAssign || !photo) return;
    const box = rectangle;
    if (!box) return;
    savingRef.current = true;
    setSaving(true);
    setSaveError("");
    try {
      const face = await saveManualFace(source.imageId, box, name, photo.token, source.contentHash);
      onSaved(face, name, box);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Die Markierung konnte nicht gespeichert werden.");
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return <div className="manual-face-backdrop">
    <section ref={dialogRef} className="manual-face-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-busy={saving}>
      <header className="manual-face-header">
        <h2 id={titleId}>Gesicht markieren</h2>
        <button type="button" onClick={onClose} disabled={saving}>Abbrechen</button>
      </header>
      <div className="manual-face-content">
        <p id={instructionsId}>
          Ziehe einen Rahmen um das Gesicht. Ziehe den Rahmen zum Verschieben oder eine Ecke zum Anpassen.
          Mit den Pfeiltasten verschiebst du den Rahmen. Halte dabei Umschalt, um die Größe zu ändern.
        </p>
        {!ready && <p role={photoError ? "alert" : "status"}>{photoError || "Das Foto wird geladen …"}</p>}
        {photoError && <div className="manual-face-actions"><button type="button"
          onClick={() => setPhotoRetry((previous) => previous + 1)}>Foto erneut laden</button></div>}
        {ready && !photo.originalAvailable && <p className="manual-face-coordinates">Du markierst auf der lokalen Vorschau.</p>}
        <div className="manual-face-stage">
          <div ref={canvasRef} role="group" tabIndex={ready ? 0 : -1} data-initial-focus={ready || undefined}
            aria-label="Foto zum Markieren" aria-describedby={`${instructionsId} ${coordinatesId}`}
            className="manual-face-canvas"
            style={ready ? { width: `min(100%, ${dimensions.width}px, ${54 * dimensions.width / dimensions.height}vh)` } : undefined}
            onPointerDown={startPointer} onPointerMove={movePointer}
            onPointerUp={(event) => endPointer(event, true)}
            onPointerCancel={(event) => endPointer(event, false)}
            onLostPointerCapture={(event) => endPointer(event, false)}
            onKeyDown={onCanvasKeyDown}>
            {photo && <img src={photo.url} alt={source.label} draggable={false}
              className={ready ? undefined : "manual-face-image--loading"}
              onLoad={(event) => setDimensions({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
              onError={() => setPhotoError("Die Bildvorschau konnte nicht geöffnet werden.")} />}
            {ready && source.existing.filter(validRectangle).map((box, index) =>
              <span key={index} className="manual-face-existing" style={rectangleStyle(box)} aria-hidden="true" />
            )}
            {showSuggestions && suggestions?.map((candidate, index) =>
              <button key={index} type="button" className="manual-face-suggestion" disabled={saving}
                style={rectangleStyle(candidate)} onClick={() => place(candidate)} aria-label={`Vorschlag ${index + 1} übernehmen`}>
                <span aria-hidden="true">{index + 1}</span>
              </button>
            )}
            {rectangle && <div className="manual-face-rectangle" style={rectangleStyle(rectangle)} aria-hidden="true">
              {corners.map((corner) => <span key={corner} data-corner={corner} />)}
            </div>}
          </div>
        </div>
        <p id={coordinatesId} className="manual-face-coordinates">{validRectangle(rectangle)
          ? `Position: ${percent(rectangle.x)} von links, ${percent(rectangle.y)} von oben. Größe: ${percent(rectangle.width)} × ${percent(rectangle.height)}.`
          : "Mit Enter kannst du einen Rahmen auf dem Foto platzieren."}</p>
        <div className="manual-face-actions">
          <button type="button" disabled={!ready || saving || dragging} onClick={() => place(centered)}>Rahmen platzieren</button>
          {allowSuggestions && <button type="button" disabled={!ready || saving || suggestionsLoading}
            onClick={() => void toggleSuggestions()}>
            {suggestionsLoading ? "Vorschläge werden gesucht …" : showSuggestions && suggestions ? "Vorschläge ausblenden" : "Vorschläge anzeigen"}
          </button>}
        </div>
        {showSuggestions && suggestions && !suggestions.length && <p role="status">Keine Vorschläge gefunden. Du kannst den Rahmen selbst ziehen.</p>}
        {suggestionError && <p role="status">{suggestionError} Du kannst den Rahmen selbst ziehen.</p>}
        <PersonAssignment people={people} peopleStatus={peopleStatus} faceCount={canAssign ? 1 : 0}
          busy={saving} wholeGroup={false} onRetry={() => setPeopleRetry((previous) => previous + 1)}
          onAssign={(name) => void assign(name)} />
        {saving && <p role="status">Markierung wird gespeichert …</p>}
        {saveError && <p role="alert">{saveError}</p>}
      </div>
    </section>
  </div>;
}
