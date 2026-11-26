const test = require('node:test');
const assert = require('node:assert/strict');
const { findNearbyPlace } = require('./load-typescript.cjs')('src/components/map/journeyPlaces.ts');
const places = require('../src/components/map/journey-places.json');

test('bundled index finds a German place name at a known location', () => {
  assert.ok(places.every(([latitude], index) => index === 0 || latitude >= places[index - 1][0]));
  const match = findNearbyPlace(places, 48.002, 7.87);
  assert.equal(match.name, 'Freiburg im Breisgau');
  assert.ok(match.distanceKm < 1);
});

test('nearest named place can be across the date line', () => {
  const match = findNearbyPlace([[0, -179.98, 'West'], [0, 179.9, 'East']], 0, 179.995);
  assert.equal(match.name, 'West');
  assert.ok(match.distanceKm < 3);
});

test('remote and ambiguous locations remain unlabelled', () => {
  assert.equal(findNearbyPlace([[0, 0.2, 'Far away']], 0, 0), undefined);
  assert.equal(findNearbyPlace([[0, 0.02, 'East'], [0, -0.025, 'West']], 0, 0), undefined);
  assert.equal(findNearbyPlace([[0, 0.107, 'East'], [0, -0.109, 'West']], 0, 0), undefined);
  assert.equal(findNearbyPlace([[0, 0.02, 'Named place'], [0, 0.021, 'Named place']], 0, 0).name, 'Named place');
});
