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
}

export default function MapJourney({ journey, selected, onSelect }: Props) {
  const [places, setPlaces] = useState<readonly JourneyPlace[] | null>(null);
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

  return <div className="gps-journey">
    <p className="gps-page__results-hint">Die Stopps folgen den Aufnahmezeiten. Gestrichelte Linien verbinden beobachtete Orte in zeitlicher Richtung. Zwischen den Aufnahmen ist der tatsächliche Weg unbekannt.</p>
    {journey.indexing && <p className="gps-page__results-hint" role="status">Aufnahmezeiten werden im Hintergrund geprüft.</p>}
    {journey.stops.length === 0 && <p>In diesem Zeitraum gibt es keine Fotos mit Standort und Aufnahmezeit.</p>}
    <ol aria-label="Chronologische Stopps" className="gps-journey__stops">
      {journey.stops.map((stop, index) => {
        const match = nearby.get(stop.id);
        return <li key={stop.id}>
        {index > 0 && stop.section !== journey.stops[index - 1].section && <p className="gps-journey__gap">Neuer Abschnitt nach mehr als einem Tag ohne Beobachtung</p>}
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
    {(journey.missing_location_count > 0 || journey.missing_time_count > 0 || journey.uncertain_count > 0) &&
      <ul className="gps-journey__notes">
        {journey.missing_location_count > 0 && <li>{numbers.format(journey.missing_location_count)} Fotos im Zeitraum ohne Standort</li>}
        {journey.missing_time_count > 0 && <li>{numbers.format(journey.missing_time_count)} Fotos mit Standort ohne zuordenbare Aufnahmezeit in der Bibliothek</li>}
        {journey.uncertain_count > 0 && <li>{numbers.format(journey.uncertain_count)} Fotos mit unsicherer zeitlicher Einordnung</li>}
      </ul>}
  </div>;
}
