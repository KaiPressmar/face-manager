export type JourneyPlace = readonly [latitude: number, longitude: number, name: string];
const coordinate = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 3 });

export function describeJourneyPlaces(stops: readonly { id: string; latitude: number; longitude: number }[], places?: readonly JourneyPlace[]) {
  const names = new Map<string, number>();
  const details = stops.map((stop) => {
    const nearby = places && findNearbyPlace(places, stop.latitude, stop.longitude);
    if (nearby) names.set(nearby.name, (names.get(nearby.name) ?? 0) + 1);
    return [stop.id, { nearby, coordinates: `${coordinate.format(stop.latitude)}°, ${coordinate.format(stop.longitude)}°` }] as const;
  });
  return new Map(details.map(([id, detail]) => [id, {
    ...detail, sharedName: detail.nearby ? (names.get(detail.nearby.name) ?? 0) > 1 : false,
  }]));
}

export async function loadJourneyPlaces(): Promise<JourneyPlace[]> {
  const { default: data } = await import("./journey-places.json");
  return data.flatMap<JourneyPlace>(([latitude, longitude, name]) =>
    typeof latitude === "number" && typeof longitude === "number" && typeof name === "string"
      ? [[latitude, longitude, name]] : []);
}

const EARTH_RADIUS_KM = 6371.0088;
const NEARBY_KM = 12;
const TIE_KM = 1;
const RAD = Math.PI / 180;

/** The bundled place index is sorted by latitude. */
export function findNearbyPlace(places: readonly JourneyPlace[], latitude: number, longitude: number) {
  const searchKm = NEARBY_KM + TIE_KM;
  const latitudeMargin = searchKm / (EARTH_RADIUS_KM * RAD);
  let low = 0;
  let high = places.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (places[middle][0] < latitude - latitudeMargin) low = middle + 1;
    else high = middle;
  }

  let closest: { name: string; distanceKm: number; latitude: number; longitude: number } | undefined;
  let alternative = Infinity;
  const latitudeRadians = latitude * RAD;
  for (let index = low; index < places.length; index += 1) {
    const [placeLatitude, placeLongitude, name] = places[index];
    if (placeLatitude > latitude + latitudeMargin) break;
    const angle = Math.sin((placeLatitude - latitude) * RAD / 2) ** 2 +
      Math.cos(latitudeRadians) * Math.cos(placeLatitude * RAD) *
      Math.sin((placeLongitude - longitude) * RAD / 2) ** 2;
    const distanceKm = 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(Math.min(1, angle)));
    if (distanceKm > searchKm) continue;
    if (!closest || distanceKm < closest.distanceKm) {
      if (closest && name !== closest.name) alternative = closest.distanceKm;
      closest = { name, distanceKm, latitude: placeLatitude, longitude: placeLongitude };
    } else if (name !== closest.name) {
      alternative = Math.min(alternative, distanceKm);
    }
  }
  return closest && closest.distanceKm <= NEARBY_KM && alternative - closest.distanceKm >= TIE_KM
    ? closest : undefined;
}
