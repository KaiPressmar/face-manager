import { useId, type MouseEventHandler } from "react";
import { imageThumbnailUrl, type MapImage } from "../../utils/api";

export default function MapPhotoTile({ image, position, inferred, onOpen }: {
  image: MapImage;
  position?: number;
  inferred?: boolean;
  onOpen: MouseEventHandler<HTMLButtonElement>;
}) {
  const note = useId();
  const filename = image.filename || "Foto";
  return <button type="button" className="gps-page__photo" data-gallery-index={position}
    aria-label={`${filename} öffnen`} aria-describedby={inferred === undefined ? undefined : note} onClick={onOpen}>
    <span className="gps-page__photo-image">
      <img src={imageThumbnailUrl(image.id)} alt="" loading="lazy" /><span className="gps-page__photo-name" title={filename}>{filename}</span>
    </span>
    {inferred !== undefined && <small id={note} className="gps-page__photo-source">
      {inferred ? "Nach Aufnahmezeit zugeordnet" : "Mit gespeichertem Standort"}
    </small>}
  </button>;
}
