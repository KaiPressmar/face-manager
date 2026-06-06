export function windowsPathToWSL(winPath: string): string {
  if (!winPath) return "";

  // Normalize slashes
  const normalized = winPath.replace(/\\/g, "/");

  // Extract drive letter
  const driveMatch = normalized.match(/^([A-Za-z]):\//);
  if (!driveMatch) return "";

  const drive = driveMatch[1].toLowerCase();
  const rest = normalized.substring(3); // skip "D:/"

  return `/mnt/${drive}/${rest}`;
}
