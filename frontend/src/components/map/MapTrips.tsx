import { useEffect, useMemo, useState } from "react";
import { findNearbyPlace, loadJourneyPlaces, type JourneyPlace } from "./journeyPlaces";
import { kilometers, selectedTripDays, suggestTrips, type TravelHome, type TripSelection } from "./journeyTrips";
import type { Journey } from "./mapData";

const dates = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeZone: "UTC" });
const labelDay = (value: string) => dates.format(new Date(value));
const searchKey = (value: string) => value.toLocaleLowerCase("de").normalize("NFD").replace(/\p{Diacritic}/gu, "");

interface Props {
  journey?: Journey | null;
  home?: TravelHome;
  onClearHome: () => void;
  selection?: TripSelection;
  canSave: boolean;
  onChange: (selection?: TripSelection) => void;
  onSave: (opener: HTMLElement) => void;
}

function tripTitle(trip: TripSelection["trip"], places: JourneyPlace[]) {
  const first = trip.core[0];
  const last = trip.core[trip.core.length - 1];
  const start = findNearbyPlace(places, first.latitude, first.longitude)?.name;
  const end = findNearbyPlace(places, last.latitude, last.longitude)?.name;
  if (!start || !end) return start || end || "Reisevorschlag";
  if (start !== end) return `${start} – ${end}`;

  const destinations = new Map<string, { distance: number; days: Set<string> }>();
  for (const stop of trip.core) {
    if (stop.uncertain) continue;
    const place = findNearbyPlace(places, stop.latitude, stop.longitude);
    if (!place || place.name === start) continue;
    const destination = destinations.get(place.name) ?? { distance: 0, days: new Set<string>() };
    destination.distance = Math.max(destination.distance, kilometers(first, stop));
    destination.days.add(stop.start.slice(0, 10));
    destination.days.add(stop.end.slice(0, 10));
    destinations.set(place.name, destination);
  }
  const distanceThreshold = Math.max(35, Math.max(0, ...[...destinations.values()].map((place) => place.distance)) * .6);
  const [representative] = [...destinations].filter(([, place]) => place.distance >= distanceThreshold)
    .sort(([, a], [, b]) => b.days.size - a.days.size || b.distance - a.distance);
  return representative ? `${start} – ${representative[0]} – ${end}` : start;
}

export default function MapTrips({ journey, home, onClearHome, selection, canSave, onChange, onSave }: Props) {
  const [open, setOpen] = useState(Boolean(selection));
  const [places, setPlaces] = useState<JourneyPlace[]>([]);
  const [search, setSearch] = useState("");
  const candidates = useMemo(() => open && !selection && journey ? suggestTrips(journey, home) : [], [journey, home, open, selection]);
  const indexed = useMemo(() => candidates.map((trip) => {
    const title = tripTitle(trip, places);
    const names = trip.core.map((stop) => findNearbyPlace(places, stop.latitude, stop.longitude)?.name);
    return { trip, title, search: searchKey([title, ...trip.days, ...trip.days.map(labelDay), ...names].join(" ")) };
  }), [candidates, places]);
  const words = searchKey(search.trim()).split(/\s+/).filter(Boolean);
  const matching = indexed.filter((entry) => words.every((word) => entry.search.includes(word)));
  useEffect(() => {
    if (!open || places.length) return;
    let mounted = true;
    void loadJourneyPlaces().then((data) => { if (mounted) setPlaces(data); }).catch(() => {});
    return () => { mounted = false; };
  }, [open, places.length]);
  return <details className="gps-map-discovery" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Reisen entdecken</summary>
    <div className="gps-map-discovery__content">
      {home ? <div className="gps-map-discovery__actions">
        <span>Heimatort für diese Sitzung: {home.name}</span>
        <button type="button" onClick={onClearHome}>Heimatort entfernen</button>
      </div> : <p>Über „Orte aus meinen Fotos“ kannst du freiwillig einen Heimatort für An- und Rückreise festlegen.</p>}
      {selection ? <>
        <strong>{selection.title || tripTitle(selection.trip, places)}</strong>
        <div className="gps-map-discovery__actions">
          <label>Reisetag<select aria-label="Reisetag" value={selection.day} onChange={(event) => onChange({ ...selection, day: event.target.value })}>
            <option value="">Alle Reisetage</option>
            {selectedTripDays(selection).map((date) => <option key={date} value={date}>{labelDay(date)}</option>)}
          </select></label>
          {selection.trip.arrival && <label><input type="checkbox" checked={selection.includeArrival}
            onChange={(event) => onChange({ ...selection, includeArrival: event.target.checked })} />Anreise einbeziehen</label>}
          {selection.trip.departure && <label><input type="checkbox" checked={selection.includeDeparture}
            onChange={(event) => onChange({ ...selection, includeDeparture: event.target.checked })} />Rückreise einbeziehen</label>}
        </div>
        <div className="gps-map-discovery__actions">
          <button type="button" disabled={!canSave} onClick={(event) => onSave(event.currentTarget)}>Als Reisealbum speichern</button>
          <button type="button" onClick={() => onChange(undefined)}>Reiseauswahl aufheben</button>
        </div>
      </> : <>
        {!journey ? <p role="status">Reiseverlauf wird geladen …</p> : candidates.length === 0 && <p>In der aktuellen Auswahl liegen keine zusammenhängenden Reisetage.</p>}
        {(candidates.length >= 8 || search) && <label>Reisen suchen
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Ort oder Jahr" />
        </label>}
        {words.length > 0 && candidates.length > 0 && <p role="status">{matching.length} von {candidates.length} Reisen</p>}
        <div className="gps-map-discovery__places">
          {matching.map(({ trip, title }) => {
            const startDay = trip.days[0];
            const endDay = trip.days[trip.days.length - 1];
            return <button type="button" key={trip.id} onClick={() => onChange({
              trip, title: title === "Reisevorschlag" ? undefined : title, includeArrival: false, includeDeparture: false, day: "",
            })}>{title} <small>{labelDay(startDay)}{startDay !== endDay && ` – ${labelDay(endDay)}`} · {trip.core.length} {trip.core.length === 1 ? "Stopp" : "Stopps"}</small></button>;
          })}
        </div>
      </>}
    </div>
  </details>;
}
