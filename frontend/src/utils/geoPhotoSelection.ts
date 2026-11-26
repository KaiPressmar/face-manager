export interface GeoPhotoSelection {
  bounds?: { west: number; east: number; south: number; north: number };
  from_date?: string;
  to_date?: string;
  image_ids?: number[];
}

export function hasGeoPhotoSelection(selection?: GeoPhotoSelection): selection is GeoPhotoSelection {
  return Boolean(selection && (selection.bounds || selection.from_date || selection.to_date || selection.image_ids));
}
