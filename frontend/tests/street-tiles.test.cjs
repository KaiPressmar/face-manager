const assert = require('node:assert/strict');
const test = require('node:test');
const { decodeStreetTile, loadStreetTiles, streetTileKey, visibleStreetTiles } =
  require('./load-typescript.cjs')('src/components/map/streetTiles.ts');
const { streetTileFixture } = require('./street-tile-fixture.cjs');

const berlin = { west: 13.386, east: 13.426, south: 52.507, north: 52.527 };

test('fetch plan covers only visible XYZ cells and caps native detail', () => {
  const size = { width: 1000, height: 500 };
  const tiles = visibleStreetTiles(berlin, size);
  assert.ok(tiles.length > 0 && tiles.length <= 24);
  assert.ok(tiles.every(({ z }) => z === 14));
  const contains = (longitude, latitude) => {
    const latitudeRadians = latitude * Math.PI / 180;
    return tiles.some(({ z, x, y }) => x === Math.floor((longitude + 180) / 360 * 2 ** z) &&
      y === Math.floor((1 - Math.asinh(Math.tan(latitudeRadians)) / Math.PI) / 2 * 2 ** z));
  };
  assert.ok(contains(berlin.west, berlin.north));
  assert.ok(contains(berlin.east, berlin.south));
  assert.ok(!contains(13.3, 52.5));
  assert.deepEqual(visibleStreetTiles({ ...berlin, north: 89, south: 88 }, size), []);
  assert.deepEqual(visibleStreetTiles(berlin, { width: 0, height: 500 }), []);
});

test('vector geometry, German labels and bounded invalid input', () => {
  const tile = decodeStreetTile({ z: 1, x: 1, y: 0 }, streetTileFixture());
  assert.equal(tile.key, '1/1/0');
  assert.equal(tile.rectangle.x, 500);
  assert.equal(tile.rectangle.width, 500);
  assert.match(tile.paths.mainRoad, /^M500\.00000 \d+\.\d{5} 1000\.00000 \d+\.\d{5}$/);
  assert.match(tile.paths.building, /^M[^Z]+Z$/);
  assert.equal(tile.labels[0].name, 'Beispielstraße');
  assert.equal(tile.labels[0].x, 750);
  assert.throws(() => decodeStreetTile({ z: 0, x: 0, y: 0 }, Uint8Array.from([26, 10, 0])), /Truncated vector tile/);
});

test('tile loader limits concurrency, caches valid decoded tiles and retries unavailable entries', async (t) => {
  const tiles = visibleStreetTiles(berlin, { width: 1600, height: 800 });
  assert.ok(tiles.length > 4);
  const missing = streetTileKey(tiles[0]);
  let calls = 0;
  let inflight = 0;
  let peak = 0;
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, init) => {
    assert.match(url, /^\/api\/map\/detail-tiles\/street\/\d+\/\d+\/\d+$/);
    assert.deepEqual([init.credentials, init.referrerPolicy, init.cache], ['same-origin', 'no-referrer', 'no-store']);
    calls += 1;
    peak = Math.max(peak, ++inflight);
    await new Promise((resolve) => setImmediate(resolve));
    inflight -= 1;
    return url.endsWith(`/${missing}`) ? new Response(null, { status: 404 }) : new Response(streetTileFixture());
  };
  const cache = new Map();
  const controller = new AbortController();
  const result = await loadStreetTiles(tiles, cache, controller.signal, '/api');
  assert.equal(result.length, tiles.length);
  assert.equal(cache.size, tiles.length - 1);
  assert.equal(peak, 4);
  assert.equal(calls, tiles.length);
  await loadStreetTiles(tiles, cache, controller.signal, '/api');
  assert.equal(calls, tiles.length + 1);
  controller.abort();
  await loadStreetTiles(tiles, cache, controller.signal, '/api');
  assert.equal(calls, tiles.length + 1);
});
