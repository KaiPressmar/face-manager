export type JourneyPlace = readonly [latitude: number, longitude: number, name: string];

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

  let closest: { name: string; distanceKm: number } | undefined;
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
      closest = { name, distanceKm };
    } else if (name !== closest.name) {
      alternative = Math.min(alternative, distanceKm);
    }
  }
  return closest && closest.distanceKm <= NEARBY_KM && alternative - closest.distanceKm >= TIE_KM
    ? closest : undefined;
}
