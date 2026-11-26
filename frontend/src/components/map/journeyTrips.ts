import type { Journey, JourneyStop } from "./mapData";

type Position = Pick<JourneyStop, "latitude" | "longitude">;
export type TravelHome = Position & { name: string };
export type TripSuggestion = {
  id: string;
  core: JourneyStop[];
  arrival?: JourneyStop;
  departure?: JourneyStop;
  days: string[];
};
export type TripSelection = {
  trip: TripSuggestion;
  includeArrival: boolean;
  includeDeparture: boolean;
  day: string;
};

const dayStart = (value: string) => value.slice(0, 10);
const dayMs = (value: string) => Date.parse(dayStart(value) + "T00:00:00Z");
const radians = Math.PI / 180;

function kilometers(first: Position, second: Position) {
  const chord = Math.sin((first.latitude - second.latitude) * radians / 2) ** 2
    + Math.cos(first.latitude * radians) * Math.cos(second.latitude * radians)
    * Math.sin((first.longitude - second.longitude) * radians / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(Math.min(1, chord)));
}

function daysWithPhotos(stops: readonly JourneyStop[]) {
  return [...new Set(stops.flatMap((stop) => [dayStart(stop.start), dayStart(stop.end)]))].sort();
}

export function suggestTrips(journey: Journey, home?: TravelHome): TripSuggestion[] {
  const candidates: TripSuggestion[] = [];
  const connections = new Set(journey.connections.map((leg) => `${leg.from_stop_id}:${leg.to_stop_id}`));
  const isHome = (stop: JourneyStop) => Boolean(home && kilometers(home, stop) <= 40);
  let start = 0;
  const addTrip = (end: number) => {
    const core = journey.stops.slice(start, end);
    const first = core[0];
    const last = core[core.length - 1];
    if (!first || (!home && dayStart(first.start) === dayStart(last.end)
      && (core.length < 2 || !core.some((stop) => kilometers(first, stop) >= 60)))) return;
    const before = home ? journey.stops[start - 1] : undefined;
    const after = home ? journey.stops[end] : undefined;
    const arrival = before && isHome(before) && connections.has(`${before.id}:${first.id}`) ? before : undefined;
    const departure = after && isHome(after) && connections.has(`${last.id}:${after.id}`) ? after : undefined;
    candidates.push({ id: first.id, core, arrival, departure, days: daysWithPhotos(core) });
  };

  journey.stops.forEach((stop, index) => {
    if (stop.uncertain || isHome(stop)) {
      addTrip(index);
      start = index + 1;
      return;
    }
    const previous = journey.stops[index - 1];
    if (index <= start) return;
    const linked = connections.has(`${previous.id}:${stop.id}`);
    const distance = kilometers(previous, stop);
    const hours = previous.end_utc && stop.start_utc ? (Date.parse(stop.start_utc) - Date.parse(previous.end_utc)) / 3600000 : null;
    if (dayMs(stop.start) - dayMs(previous.end) > 2 * 86400000
      || ((!linked || previous.section !== stop.section) && distance > 35)
      || (hours !== null && hours > 0 && distance > 200 && distance / hours > 1100)) {
      addTrip(index);
      start = index;
    }
  });
  addTrip(journey.stops.length);
  return candidates.reverse();
}

export function photoIdsForTrip(selection: TripSelection) {
  const { trip, includeArrival, includeDeparture } = selection;
  return new Set([
    ...(includeArrival && trip.arrival ? trip.arrival.image_ids : []),
    ...trip.core.flatMap((stop) => stop.image_ids),
    ...(includeDeparture && trip.departure ? trip.departure.image_ids : []),
  ]);
}

export function applyTripSelection(journey: Journey, wanted: ReadonlySet<number>): Journey {
  const keep = new Set<string>();
  const stops: JourneyStop[] = [];
  let section = 0;
  let previousIndex = -2;
  journey.stops.forEach((stop, index) => {
    const image_ids = stop.image_ids.filter((id) => wanted.has(id));
    if (!image_ids.length) return;
    if (previousIndex !== index - 1 || journey.stops[previousIndex]?.section !== stop.section) section += 1;
    previousIndex = index;
    const split = image_ids.length !== stop.image_ids.length;
    if (!split) keep.add(stop.id);
    stops.push({ ...stop, image_ids, section, number: stops.length + 1, uncertain: stop.uncertain || split });
  });
  return {
    ...journey, stops,
    connections: journey.connections.filter((leg) => keep.has(leg.from_stop_id) && keep.has(leg.to_stop_id)),
    photo_count: stops.reduce((count, stop) => count + stop.image_ids.length, 0),
    uncertain_count: stops.reduce((count, stop) => count + (stop.uncertain ? stop.image_ids.length : 0), 0),
  };
}
