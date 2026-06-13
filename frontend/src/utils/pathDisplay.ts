export function pathBasename(path: string) {
  return path.replace(/\\/g, "/").split("/").filter(Boolean).pop() || path;
}
