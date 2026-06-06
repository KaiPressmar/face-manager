export function neonColorFromName(name: string | null): string {
  if (!name) return "#9e9e9e"; // unknown

  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }

  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 95%, 65%)`;
}
