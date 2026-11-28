import { imageThumbnailUrl, type FaceImage } from "../../utils/api";
import { imageAspectRatiosMatch } from "../../utils/imageGeometry";

export const CARD_GAP = 16;
type PreviewState = { w: number; h: number } | "error";
export type CachedPreview = { preview?: PreviewState; retry?: number };

export function galleryDimensions(image: FaceImage, preview?: PreviewState) {
  const previewSize = preview === "error" ? undefined : preview;
  const imageSize = image.image_width && image.image_height ? { w: image.image_width, h: image.image_height } : undefined;
  return imageSize && (!previewSize || image.content_hash ||
    imageAspectRatiosMatch(imageSize.w, imageSize.h, previewSize.w, previewSize.h)) ? imageSize : previewSize;
}

interface PositionedPhoto {
  image: FaceImage;
  index: number;
  column: number;
  top: number;
  bottom: number;
}

export function positionPhotos(
  images: readonly FaceImage[],
  width: number,
  columnCount: number,
  previews: ReadonlyMap<string, CachedPreview>,
) {
  const columnWidth = Math.max(1, (width - CARD_GAP * (columnCount - 1)) / columnCount);
  const columns: PositionedPhoto[][] = Array.from({ length: columnCount }, () => []);
  const ends = columns.map(() => 0);
  const byId = new Map<number, PositionedPhoto>();
  images.forEach((image, index) => {
    const column = index % columnCount;
    const top = ends[column];
    const size = galleryDimensions(image, previews.get(imageThumbnailUrl(image.id))?.preview);
    const bottom = top + Math.max(2, size ? columnWidth * size.h / size.w : columnWidth);
    const photo = { image, index, column, top, bottom };
    columns[column].push(photo);
    byId.set(image.id, photo);
    ends[column] = bottom + CARD_GAP;
  });
  return { columns, byId, columnWidth, height: Math.max(...ends), shortestColumn: Math.min(...ends) };
}

export function photosInWindow(columns: PositionedPhoto[][], start: number, end: number) {
  return columns.flatMap((column) => {
    let first = 0;
    let remaining = column.length;
    while (first < remaining) {
      const middle = Math.floor((first + remaining) / 2);
      if (column[middle].bottom < start) first = middle + 1;
      else remaining = middle;
    }
    const visible: PositionedPhoto[] = [];
    for (let index = first; index < column.length && column[index].top <= end; index += 1) {
      visible.push(column[index]);
    }
    return visible;
  });
}
