import { useId, useState, type MouseEventHandler } from "react";
import { imageThumbnailUrl, type MapImage } from "../../utils/api";

export default function MapPhotoTile({ image, position, inferred, onOpen }: {
  image: MapImage;
  position?: number;
  inferred?: boolean;
  onOpen: MouseEventHandler<HTMLButtonElement>;
}) {
  const note = useId();
  const previewNote = `${note}-preview`;
  const filename = image.filename || "Foto";
  const source = imageThumbnailUrl(image.id);
  const [failedSource, setFailedSource] = useState("");
  const broken = failedSource === source;
  const descriptions = [inferred === undefined ? "" : note, broken ? previewNote : ""].filter(Boolean).join(" ");
  return <button type="button" className="gps-page__photo" data-gallery-index={position}
    aria-label={`${filename} öffnen`}
    aria-describedby={descriptions || undefined}
    onClick={onOpen}>
    <span className="gps-page__photo-image">
      {broken ? <span id={previewNote} className="gps-page__photo-preview-error">Vorschau nicht verfügbar</span>
        : <img src={source} alt="" loading="lazy" decoding="async" onError={() => setFailedSource(source)} />}
      <span className="gps-page__photo-name" title={filename}>{filename}</span>
    </span>
    {inferred !== undefined && <small id={note} className="gps-page__photo-source">
      {inferred ? "Nach Aufnahmezeit zugeordnet" : "Mit gespeichertem Standort"}
    </small>}
  </button>;
}
