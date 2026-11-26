export function formatMapDate(iso: string) {
  const [year, month, day] = iso.split("-");
  return year && month && day ? `${day}.${month}.${year}` : "";
}

/** Parse calendar days without applying the browser's locale or time zone. */
export function parseMapDate(input: string): string | null {
  const value = input.trim();
  if (!value) return "";
  const local = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(value) ?? /^(\d{2})(\d{2})(\d{4})$/.exec(value);
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const year = local?.[3] ?? iso?.[1];
  const month = local?.[2] ?? iso?.[2];
  const day = local?.[1] ?? iso?.[3];
  if (!year || !month || !day || year === "0000") return null;
  const leap = Number(year) % 4 === 0 && (Number(year) % 100 !== 0 || Number(year) % 400 === 0);
  const monthLength = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][Number(month) - 1];
  return monthLength && Number(day) >= 1 && Number(day) <= monthLength ? `${year}-${month}-${day}` : null;
}

export function describeMapDates(from: string, to: string) {
  if (from && to) return from === to ? formatMapDate(from) : `${formatMapDate(from)} – ${formatMapDate(to)}`;
  return from ? `Ab ${formatMapDate(from)}` : to ? `Bis ${formatMapDate(to)}` : "";
}
