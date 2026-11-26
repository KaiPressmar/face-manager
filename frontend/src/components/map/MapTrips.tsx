import { useEffect, useMemo, useState } from "react";
import { findNearbyPlace, loadJourneyPlaces, type JourneyPlace } from "./journeyPlaces";
import { suggestTrips, type TravelHome, type TripSelection } from "./journeyTrips";
import type { Journey } from "./mapData";

const dates = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeZone: "UTC" });
const labelDay = (value: string) => dates.format(new Date(value));

interface Props {
  journey: Journey;
  home?: TravelHome;
  onClearHome: () => void;
  selection?: TripSelection;
  canSave: boolean;
  onChange: (selection?: TripSelection) => void;
  onSave: (opener: HTMLElement) => void;
}

export default function MapTrips({ journey, home, onClearHome, selection, canSave, onChange, onSave }: Props) {
  const [open, setOpen] = useState(Boolean(selection));
  const [places, setPlaces] = useState<JourneyPlace[]>([]);
  const candidates = useMemo(() => open && !selection ? suggestTrips(journey, home) : [], [journey, home, open, selection]);
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
        <div className="gps-map-discovery__actions">
          <label>Reisetag<select aria-label="Reisetag" value={selection.day} onChange={(event) => onChange({ ...selection, day: event.target.value })}>
            <option value="">Alle Reisetage</option>
            {selection.trip.days.map((date) => <option key={date} value={date}>{labelDay(date)}</option>)}
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
        <p>Vorschläge aus aufeinanderfolgenden Fotoorten. Prüfe die Reisetage und sichtbaren Stopps vor dem Speichern.</p>
        {candidates.length === 0 && <p>In der aktuellen Auswahl liegen keine zusammenhängenden Reisetage.</p>}
        <div className="gps-map-discovery__places">
          {candidates.map((trip) => {
            const first = trip.core[0];
            const last = trip.core[trip.core.length - 1];
            const start = findNearbyPlace(places, first.latitude, first.longitude)?.name;
            const end = findNearbyPlace(places, last.latitude, last.longitude)?.name;
            const title = start && end && end !== start ? `${start} – ${end}` : start || end || "Reisevorschlag";
            const startDay = trip.days[0];
            const endDay = trip.days[trip.days.length - 1];
            return <button type="button" key={trip.id} onClick={() => onChange({
              trip, includeArrival: false, includeDeparture: false, day: "",
            })}>{title} <small>{labelDay(startDay)}{startDay !== endDay && ` – ${labelDay(endDay)}`} · {trip.core.length} Stopps</small></button>;
          })}
        </div>
      </>}
    </div>
  </details>;
}
