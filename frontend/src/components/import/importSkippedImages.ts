import type { ImportJob } from "../../utils/api";

export function importSkippedImages(job: ImportJob): string {
  const unavailable = job.unavailable_images ?? 0;
  const unreadable = job.unreadable_images ?? 0;
  const details = [];
  if (unavailable > 0) {
    details.push(unavailable === 1 ? "1 Datei nicht erreichbar" : `${unavailable} Dateien nicht erreichbar`);
  }
  if (unreadable > 0) {
    details.push(unreadable === 1 ? "1 Datei konnte nicht als Bild gelesen werden" : `${unreadable} Dateien konnten nicht als Bilder gelesen werden`);
  }
  return details.join(" · ");
}
