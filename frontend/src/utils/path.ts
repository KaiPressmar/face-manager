export function windowsPathToWSL(path: string): string {
  if (!path) return "";

  // Beispiel: C:\Users\Kai\Pictures → /mnt/c/Users/Kai/Pictures
  const drive = path[0].toLowerCase();
  const rest = path.substring(2).replace(/\\/g, "/");

  return `/mnt/${drive}/${rest}`;
}
