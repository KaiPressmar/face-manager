import type { ImportJob } from "../../utils/api";

export function importSkippedImages(job: ImportJob): string {
  const counts = job.issue_counts;
  if (counts && Object.values(counts).some((count) => count > 0)) {
    const folders = counts.directory_unavailable ?? 0;
    const files = (counts.image_unavailable ?? 0) + (counts.image_unreadable ?? 0)
      + (counts.image_changed ?? 0) + (counts.image_failed ?? 0);
    return [
      folders > 0 && `${folders.toLocaleString("de-DE")} Ordner nicht erreichbar`,
      files > 0 && `${files.toLocaleString("de-DE")} ${files === 1 ? "Datei" : "Dateien"} übersprungen`,
    ].filter(Boolean).join(" · ");
  }
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
