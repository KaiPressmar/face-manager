import { useEffect, useMemo, useState } from "react";
import type { Journey, JourneyStop } from "./mapData";
import { findNearbyPlace, loadJourneyPlaces, type JourneyPlace } from "./journeyPlaces";

const numbers = new Intl.NumberFormat("de-DE");
const dates = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
const place = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 3 });

export function journeyTime(value: string) {
  const date = new Date(value.slice(0, 19) + "Z");
  return Number.isFinite(date.getTime()) ? dates.format(date) : value;
}

export function journeyStopLabel(stop: JourneyStop) {
  if (stop.start_utc && stop.end_utc) {
    const startOffset = Date.parse(stop.start + "Z") - Date.parse(stop.start_utc);
    const endOffset = Date.parse(stop.end + "Z") - Date.parse(stop.end_utc);
    if (stop.end < stop.start || Math.abs(startOffset - endOffset) >= 60000) {
      return `${journeyTime(stop.start_utc)} – ${journeyTime(stop.end_utc)} (UTC)`;
    }
  }
  const start = journeyTime(stop.start);
  return stop.start === stop.end ? start : `${start} – ${journeyTime(stop.end)}`;
}

interface Props {
  journey: Journey;
  selected?: string;
  onSelect: (id: string) => void;
  areaFiltered: boolean;
  onClearArea: () => void;
  onClearDates?: () => void;
}

const searchable = (text: string) => text.toLocaleLowerCase("de")
  .normalize("NFD").replace(/\p{Diacritic}/gu, "");

export default function MapJourney({ journey, selected, onSelect, areaFiltered, onClearArea, onClearDates }: Props) {
  const [places, setPlaces] = useState<readonly JourneyPlace[] | null>(null);
  const [search, setSearch] = useState("");
  const hasStops = journey.stops.length > 0;
  useEffect(() => {
    if (!hasStops) return;
    let active = true;
    void loadJourneyPlaces().then((data) => {
      if (active) setPlaces(data);
    }).catch(() => {
      // Coordinates remain available if the optional place index cannot load.
    });
    return () => { active = false; };
  }, [hasStops]);
  const nearby = useMemo(() => new Map(journey.stops.map((stop) => [
    stop.id, places ? findNearbyPlace(places, stop.latitude, stop.longitude) : undefined,
  ])), [journey.stops, places]);
  const query = searchable(search.trim());
  const visibleStops = query ? journey.stops.filter((stop) => {
    const match = nearby.get(stop.id);
    const label = match?.name ?? `${place.format(stop.latitude)}°, ${place.format(stop.longitude)}°`;
    return searchable(`Stopp ${stop.number} ${label} ${journeyStopLabel(stop)}`).includes(query);
  }) : journey.stops;
  const showLocationNote = !areaFiltered && journey.missing_location_count > 0;

  return <div className="gps-journey">
    <p className="gps-page__results-hint">Die Stopps folgen den Aufnahmezeiten. Gestrichelte Linien verbinden beobachtete Orte in zeitlicher Richtung. Zwischen den Aufnahmen ist der tatsächliche Weg unbekannt.</p>
    {journey.indexing && <p className="gps-page__results-hint" role="status">Aufnahmezeiten werden im Hintergrund geprüft.</p>}
    {!hasStops && <div className="gps-page__empty">
      <strong>{areaFiltered ? "Keine Stopps in diesem Bereich und Zeitraum." : "In diesem Zeitraum gibt es keine Fotos mit Standort und Aufnahmezeit."}</strong>
      <div className="gps-results-actions">
        {areaFiltered && <button type="button" onClick={onClearArea}>Bereich entfernen</button>}
        {onClearDates && <button type="button" onClick={onClearDates}>Alle Zeiten zeigen</button>}
      </div>
    </div>}
    {(journey.stops.length >= 8 || Boolean(search)) && <label className="gps-page__field">
      <span>Stoppliste durchsuchen</span>
      <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Ort, Datum oder Stoppnummer" />
    </label>}
    {query && <p role="status" className="gps-page__results-hint">{numbers.format(visibleStops.length)} von {numbers.format(journey.stops.length)} Stopps in der Liste</p>}
    <ol aria-label="Chronologische Stopps" className="gps-journey__stops">
      {visibleStops.map((stop, index) => {
        const match = nearby.get(stop.id);
        return <li key={stop.id}>
        {index > 0 && stop.section !== visibleStops[index - 1].section && <p className="gps-journey__gap">{areaFiltered
          ? "Neuer Abschnitt im gewählten Bereich" : "Neuer Abschnitt nach mehr als einem Tag ohne Beobachtung"}</p>}
        <button type="button" aria-pressed={selected === stop.id} onClick={() => onSelect(stop.id)}>
          <span className="gps-journey__stop-number">{stop.number}</span>
          <span>
            {match && <strong>Bei {match.name}</strong>}
            {match ? <small>{journeyStopLabel(stop)}</small> : <strong>{journeyStopLabel(stop)}</strong>}
            <small>{match
              ? match.distanceKm < 1 ? "Unter 1 km Entfernung" : `Ca. ${numbers.format(Math.round(match.distanceKm))} km Entfernung`
              : `${place.format(stop.latitude)}°, ${place.format(stop.longitude)}°`}
              {" · "}{numbers.format(stop.image_ids.length)} {stop.image_ids.length === 1 ? "Foto" : "Fotos"}</small>
            {stop.uncertain && <small>Reihenfolge unsicher</small>}
          </span>
        </button>
        </li>;
      })}
    </ol>
    {(showLocationNote || journey.missing_time_count > 0 || journey.uncertain_count > 0) &&
      <ul className="gps-journey__notes">
        {showLocationNote && <li>{numbers.format(journey.missing_location_count)} Fotos im Zeitraum ohne Standort</li>}
        {journey.missing_time_count > 0 && <li>{numbers.format(journey.missing_time_count)} Fotos mit Standort ohne zuordenbare Aufnahmezeit {areaFiltered ? "im Bereich" : "in der Bibliothek"}</li>}
        {journey.uncertain_count > 0 && <li>{numbers.format(journey.uncertain_count)} Fotos mit unsicherer zeitlicher Einordnung</li>}
      </ul>}
  </div>;
}
