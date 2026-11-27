const { test } = require('node:test');
const assert = require('node:assert/strict');
const { suggestTrips, photoIdsForTrip, applyTripSelection } = require('./load-typescript.cjs')('src/components/map/journeyTrips.ts');

const berlin = { name: 'Berlin', latitude: 52.52, longitude: 13.405 };
const paris = { latitude: 48.8566, longitude: 2.3522 };
const lyon = { latitude: 45.764, longitude: 4.8357 };
const stop = (id, day, place, uncertain = false) => ({
  id: String(id), number: id, section: 1, start: day + 'T11:00:00', end: day + 'T12:00:00',
  image_ids: [id * 10, id * 10 + 1], ...place, uncertain,
});
const journey = (stops) => ({
  stops, connections: stops.slice(1).map((to, index) => ({
    from: stops[index], to, from_stop_id: stops[index].id, to_stop_id: to.id,
  })), photo_count: stops.length * 2, missing_location_count: 0, missing_time_count: 0, uncertain_count: 0, indexing: false,
});

test('a voluntary home location identifies two visits to the same cities with independent transfers', () => {
  const source = journey([stop(1, '2025-04-12', berlin), stop(2, '2025-04-12', paris),
    stop(3, '2025-04-13', lyon), stop(4, '2025-04-14', berlin),
    stop(5, '2025-05-02', berlin), stop(6, '2025-05-02', paris),
    stop(7, '2025-05-03', lyon), stop(8, '2025-05-04', berlin)]);
  const [second, first] = suggestTrips(source, berlin);
  assert.deepEqual([second.id, first.id], ['6', '2']);
  assert.deepEqual(first.days, ['2025-04-12', '2025-04-13']);
  const core = { trip: first, includeArrival: false, includeDeparture: false, day: '' };
  const selection = applyTripSelection(source, photoIdsForTrip(core));
  assert.deepEqual(selection.stops.map(({ id }) => id), ['2', '3']);
  assert.deepEqual(selection.connections.map(({ from_stop_id, to_stop_id }) => [from_stop_id, to_stop_id]), [['2', '3']]);
  assert.deepEqual(applyTripSelection(source, photoIdsForTrip({ ...core, includeDeparture: true })).stops.map(({ id }) => id), ['2', '3', '4']);
  assert.deepEqual(applyTripSelection(source, photoIdsForTrip({ ...core, includeArrival: true })).stops.map(({ id }) => id), ['1', '2', '3']);
});

test('a missing link cannot supply a transfer and uncertain observations break suggestions', () => {
  const source = journey([stop(1, '2025-06-12', berlin), stop(2, '2025-06-12', paris),
    stop(3, '2025-06-13', lyon), stop(4, '2025-06-13', paris, true),
    stop(5, '2025-06-14', berlin)]);
  source.connections.shift();
  const [candidate] = suggestTrips(source, berlin);
  assert.equal(candidate.arrival, undefined);
  assert.equal(candidate.departure, undefined);
  assert.deepEqual(candidate.core.map(({ id }) => id), ['2', '3']);
  const filtered = applyTripSelection(source, new Set([21, 50]));
  assert.deepEqual(filtered.stops.map(({ image_ids, section }) => [image_ids, section]), [[[21], 1], [[50], 2]]);
  assert.equal(filtered.connections.length, 0);
  assert.equal(filtered.stops[0].uncertain, true);
});

test('unconnected distant observations do not form a trip and an overnight stay can be one stop', () => {
  const source = journey([stop(1, '2025-07-01', paris), { ...stop(2, '2025-07-02', { latitude: 35.6762, longitude: 139.6503 }), section: 2 }]);
  source.connections = [];
  assert.deepEqual(suggestTrips(source), []);
  const overnight = { ...stop(3, '2025-08-01', lyon), end: '2025-08-02T00:15:00' };
  assert.deepEqual(suggestTrips(journey([overnight]))[0].days, ['2025-08-01', '2025-08-02']);
});

const timed = (id, date, place, start, end) => ({
  ...stop(id, date, place), start: `${date}T${start}:00`, end: `${date}T${end}:00`,
  start_utc: `${date}T${start}:00+00:00`, end_utc: `${date}T${end}:00+00:00`,
});

function journeyWithUncertainPhoto(place = paris, nextPlace = lyon, section = 1, date = '2025-07-02') {
  const source = journey([
    timed(1, '2025-07-01', paris, '11:00', '11:10'),
    timed(2, '2025-07-02', paris, '08:00', '08:10'),
    { ...stop(3, '2025-07-02', place, true), start: '2025-07-02T08:11:00', end: '2025-07-02T08:15:00' },
    { ...timed(4, date, nextPlace, '10:00', '10:10'), section },
    { ...timed(5, '2025-07-04', nextPlace, '08:00', '08:10'), section },
  ]);
  source.connections = source.connections.filter(({ from_stop_id, to_stop_id }) => from_stop_id !== '3' && to_stop_id !== '3');
  return source;
}

test('nearby photos with an uncertain clock remain in the same travel suggestion without adding a connection', () => {
  const source = journeyWithUncertainPhoto();
  const [trip] = suggestTrips(source);
  assert.deepEqual(trip.core.map(({ id }) => id), ['1', '2', '3', '4', '5']);
  const route = applyTripSelection(source, photoIdsForTrip({ trip, includeArrival: false, includeDeparture: false, day: '' }));
  assert.deepEqual(route.connections.map(({ from_stop_id, to_stop_id }) => [from_stop_id, to_stop_id]), [['1', '2'], ['4', '5']]);
  assert.equal(route.stops[2].uncertain, true);
});

test('uncertain photos cannot join remote, separated, or implausibly distant travel periods', () => {
  const tokyo = { latitude: 35.6762, longitude: 139.6503 };
  for (const source of [journeyWithUncertainPhoto(berlin), journeyWithUncertainPhoto(paris, tokyo),
    journeyWithUncertainPhoto(paris, lyon, 2), journeyWithUncertainPhoto(paris, lyon, 1, '2025-07-03')]) {
    assert.deepEqual(suggestTrips(source).map((trip) => trip.core.map(({ id }) => id)), [['4', '5'], ['1', '2']]);
  }
});
