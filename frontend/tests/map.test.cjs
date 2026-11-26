const test = require('node:test');
const assert = require('node:assert/strict');
const { boundsForDrag, boundsForView, countryLabels, gridForSize, journeyConnectionSegments, layoutMapLabels, markerPixelPosition, normalizedView } =
  require('./load-typescript.cjs')('src/components/map/WorldMap.tsx');

function assertValidBounds(bounds) {
  assert.ok(Number.isFinite(bounds.west) && Number.isFinite(bounds.east));
  assert.ok(Number.isFinite(bounds.south) && Number.isFinite(bounds.north));
  assert.ok(bounds.west >= -180 && bounds.east <= 180 && bounds.west < bounds.east);
  assert.ok(bounds.south >= -90 && bounds.north <= 90 && bounds.south < bounds.north);
}

test('world view covers the exact geographic extent', () => {
  assert.deepEqual(boundsForView({ longitude: 0, latitude: 0, zoom: 1 }), {
    west: -180, east: 180, south: -90, north: 90,
  });
});

test('views clamp at poles and the date line without wrapping', () => {
  for (const view of [
    { longitude: 179, latitude: 89, zoom: 8 },
    { longitude: -179, latitude: -89, zoom: 8 },
    { longitude: 500, latitude: 200, zoom: 64 },
    { longitude: -500, latitude: -200, zoom: 64 },
  ]) {
    const normalized = normalizedView(view);
    const bounds = boundsForView(normalized);
    assertValidBounds(bounds);
    assert.ok(bounds.east - bounds.west <= 360 / normalized.zoom + 1e-8);
    assert.ok(bounds.north - bounds.south <= 180 / normalized.zoom + 1e-8);
  }
});

test('zoom transitions preserve a valid, shrinking viewport', () => {
  const center = { longitude: 13.4, latitude: 52.5 };
  let previousWidth = Infinity;
  for (const zoom of [0.1, 1, 2, 4, 8, 16, 32, 64, 128, 200]) {
    const normalized = normalizedView({ ...center, zoom });
    const bounds = boundsForView(normalized);
    assertValidBounds(bounds);
    assert.ok(bounds.west <= normalized.longitude && normalized.longitude <= bounds.east);
    assert.ok(bounds.south <= normalized.latitude && normalized.latitude <= bounds.north);
    assert.ok(bounds.east - bounds.west <= previousWidth + 1e-8);
    previousWidth = bounds.east - bounds.west;
  }
});

test('drag bounds use the map viewport and work in either direction', () => {
  const viewport = { west: 10, east: 30, south: 40, north: 60 };
  const start = { x: 0.75, y: 0.25 };
  const end = { x: 0.25, y: 0.75 };
  const expected = { west: 15, east: 25, south: 45, north: 55 };
  assert.deepEqual(boundsForDrag(viewport, start, end), expected);
  assert.deepEqual(boundsForDrag(viewport, end, start), expected);
});

test('dragging past the map clips selection to the visible area', () => {
  const viewport = { west: 10, east: 30, south: 40, north: 60 };
  assert.deepEqual(boundsForDrag(viewport, { x: 0.5, y: 0.5 }, { x: 1.25, y: -0.5 }), {
    west: 20, east: 30, south: 50, north: 60,
  });
});

test('marker grids fit touch targets and stay within the backend limit', () => {
  assert.deepEqual(gridForSize({ width: 320, height: 160 }), { columns: 6, rows: 3 });
  assert.deepEqual(gridForSize({ width: 40, height: 30 }), { columns: 1, rows: 1 });
  assert.deepEqual(gridForSize({ width: 2000, height: 1800 }), { columns: 32, rows: 32 });
});

test('adjacent cells keep hit targets apart when both markers are near the boundary', () => {
  const bounds = boundsForView({ longitude: 0, latitude: 0, zoom: 1 });
  const size = { width: 184, height: 92 };
  const grid = gridForSize(size);
  const west = markerPixelPosition({ x: 0, y: 1, longitude: -90.001, latitude: 45 }, bounds, size, grid);
  const east = markerPixelPosition({ x: 1, y: 1, longitude: -90, latitude: 45 }, bounds, size, grid);
  assert.equal(east.x - west.x, 46);
  assert.equal(west.y, 23);
  assert.equal(east.y, 23);
});

test('marker positions use north at the top and stay inside the canvas at world edges', () => {
  const bounds = boundsForView({ longitude: 0, latitude: 0, zoom: 1 });
  const size = { width: 200, height: 100 };
  const grid = gridForSize(size);
  assert.deepEqual(
    markerPixelPosition({ x: 0, y: 1, longitude: -180, latitude: 90 }, bounds, size, grid),
    { x: 23, y: 23 },
  );
  assert.deepEqual(
    markerPixelPosition({ x: 3, y: 0, longitude: 180, latitude: -90 }, bounds, size, grid),
    { x: 177, y: 77 },
  );
});

test('German place labels defer to zoom, higher-priority labels, and photo markers', () => {
  const bounds = { west: 0, east: 20, south: 40, north: 50 };
  const size = { width: 400, height: 200 };
  const labels = [
    { id: 'de', name: 'Deutschland', longitude: 10, latitude: 45, kind: 'country', minZoom: 3, priority: 10 },
    { id: 'c1', name: 'Köln', longitude: 11, latitude: 45, kind: 'city', minZoom: 3, priority: 100 },
    { id: 'c2', name: 'Bonn', longitude: 15, latitude: 42, kind: 'city', minZoom: 3, priority: 25 },
    { id: 'c3', name: 'Ulm', longitude: 5, latitude: 42, kind: 'city', minZoom: 8, priority: 25 },
  ];
  assert.deepEqual(layoutMapLabels(labels, 2, bounds, size, []), []);
  assert.deepEqual(layoutMapLabels(labels, 4, bounds, size, [{ x: 300, y: 160 }]).map(({ id }) => id), ['de']);
  assert.deepEqual(layoutMapLabels(labels, 4, bounds, size, []).map(({ id }) => id), ['de', 'c2']);
});

test('country lookup keeps German names, aliases, and a valid offline focus', () => {
  const germany = countryLabels.find(({ code }) => code === 'DEU');
  assert.equal(germany.name, 'Deutschland');
  assert.ok(germany.aliases.includes('Germany'));
  assert.ok(germany.minZoom <= 5);
  const bounds = boundsForView(normalizedView(germany.focus));
  assert.ok(germany.longitude > bounds.west && germany.longitude < bounds.east);
  assert.ok(germany.latitude > bounds.south && germany.latitude < bounds.north);
  assert.equal(new Set(countryLabels.map(({ code }) => code)).size, countryLabels.length);
});

test('journey segments maintain travel direction across the date line', () => {
  const east = { longitude: 170, latitude: 4 };
  const west = { longitude: -170, latitude: 8 };
  assert.deepEqual(journeyConnectionSegments({ from: east, to: west }), [
    { from: east, to: { longitude: 180, latitude: 6 } },
    { from: { longitude: -180, latitude: 6 }, to: west },
  ]);
  assert.deepEqual(journeyConnectionSegments({ from: west, to: east }), [
    { from: west, to: { longitude: -180, latitude: 6 } },
    { from: { longitude: 180, latitude: 6 }, to: east },
  ]);
  const from = { longitude: 17, latitude: 2 };
  const to = { longitude: -5, latitude: 10 };
  assert.deepEqual(journeyConnectionSegments({ from, to }), [{ from, to }]);
});
