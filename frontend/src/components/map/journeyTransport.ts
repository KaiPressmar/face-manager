import type { JourneyStop } from "./mapData";

type Endpoint = Pick<JourneyStop, "latitude" | "longitude" | "uncertain" | "start_utc" | "end_utc">;
export type JourneyTransportHint = "walk" | "plane" | "road-or-rail";

const radians = Math.PI / 180;
const instant = (value?: string | null) => value && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? Date.parse(value) : NaN;

export function suggestJourneyTransport(from: Endpoint, to: Endpoint): JourneyTransportHint | undefined {
  if (from.uncertain || to.uncertain) return undefined;
  const hours = (instant(to.start_utc) - instant(from.end_utc)) / 3600000;
  if (!(hours > 0 && hours <= 10)) return undefined;
  const lat = from.latitude * radians;
  const nextLat = to.latitude * radians;
  const chord = Math.sin((nextLat - lat) / 2) ** 2 +
    Math.cos(lat) * Math.cos(nextLat) * Math.sin((to.longitude - from.longitude) * radians / 2) ** 2;
  const distance = 6371.0088 * 2 * Math.asin(Math.sqrt(Math.min(1, chord)));
  const speed = distance / hours;

  // Observations bound average speed. Waiting and detours are unobserved.
  if (distance >= 650 && hours >= .75 && speed >= 420 && speed <= 1000) return "plane";
  if (distance >= .12 && distance <= 1.75 && hours >= 5 / 60 && hours <= 50 / 60 && speed >= 1.8 && speed <= 5.5) return "walk";
  if (distance >= 45 && hours <= 8 && speed >= 70 && speed <= 250) return "road-or-rail";
  return undefined;
}
