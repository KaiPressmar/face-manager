const test = require('node:test');
const assert = require('node:assert/strict');
const { boundsForView, normalizedView } = require('./load-typescript.cjs')('src/components/map/WorldMap.tsx');

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
