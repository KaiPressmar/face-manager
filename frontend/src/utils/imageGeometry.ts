/** A scaled preview can round either dimension by up to one pixel. */
export function imageAspectRatiosMatch(width: number, height: number, previewWidth: number, previewHeight: number) {
  if (!width || !height || !previewWidth || !previewHeight) return false;
  const scale = Math.min(previewWidth / width, previewHeight / height);
  return previewWidth - width * scale <= 1 && previewHeight - height * scale <= 1;
}
