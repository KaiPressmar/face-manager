const test = require('node:test');
const assert = require('node:assert/strict');
const { fetchRasterTiles, planRasterTiles, rasterSources } =
  require('./load-typescript.cjs')('src/components/map/rasterTiles.ts');

const size = { width: 1000, height: 500 };
const world = { west: -180, east: 180, south: -90, north: 90 };
const city = { west: 13.023, east: 13.064, south: 47.78, north: 47.810 };
const bbox = (tile) => {
  const [level, column, row] = tile.url.split('/').slice(-3).map(Number);
  const degrees = 180 / 2 ** level;
  const west = -180 + column * degrees;
  const north = 90 - row * degrees;
  return [west, north - degrees, west + degrees, north];
};

test('regional detail only plans requests for the two opt-in raster styles and zoomed bounds', () => {
  assert.equal(planRasterTiles('standard', city, size), undefined);
  assert.equal(planRasterTiles('satellite', world, size), undefined);
  assert.equal(planRasterTiles('landscape', city, { width: 0, height: 0 }), undefined);
  for (const style of ['landscape', 'satellite']) {
    const { tiles } = planRasterTiles(style, city, size);
    assert.ok(tiles.length > 0 && tiles.length <= 16);
    for (const tile of tiles) {
      assert.match(tile.url, new RegExp(`^/api/map/detail-tiles/${style}/\\d+/\\d+/\\d+$`));
      const [west, south, east, north] = bbox(tile);
      assert.ok(west < city.east && east > city.west && south < city.north && north > city.south);
      assert.equal(tile.x, (west + 180) / 360 * 1000);
      assert.equal(tile.y, (90 - north) / 180 * 500);
      assert.equal(tile.side, (east - west) / 360 * 1000);
    }
  }
});

test('tile plans reuse cache keys for small movements and never exceed source resolution', () => {
  const close = { west: 13.034, east: 13.035, south: 47.801, north: 47.802 };
  const shifted = { ...close, west: close.west + .0001, east: close.east + .0001 };
  const first = planRasterTiles('satellite', close, size);
  assert.equal(first.level, rasterSources.satellite.maxLevel);
  assert.deepEqual(first, planRasterTiles('satellite', shifted, size));
  const landscape = planRasterTiles('landscape', close, size);
  assert.equal(landscape.level, rasterSources.landscape.maxLevel);
  const eastEdge = planRasterTiles('satellite', { west: 179.9, east: 180, south: 89.9, north: 90 }, size);
  for (const tile of eastEdge.tiles) {
    const [west, south, east, north] = bbox(tile);
    assert.ok(west < east && east <= 180 && south < north && north <= 90);
  }
});

test('tile fetches cap concurrency, preserve the local app session, and retain successful tiles', async () => {
  const tiles = planRasterTiles('satellite', city, size).tiles;
  assert.ok(tiles.length > 4);
  let active = 0;
  let peak = 0;
  const skip = tiles[0].url;
  const controller = new AbortController();
  const loaded = await fetchRasterTiles(tiles, controller.signal, async (url, options) => {
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.referrerPolicy, 'no-referrer');
    assert.equal(options.redirect, 'error');
    assert.equal(options.signal, controller.signal);
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setImmediate(resolve));
    active -= 1;
    return url === skip ? new Response('unavailable', { status: 503 })
      : new Response('image', { headers: { 'Content-Type': 'image/jpeg' } });
  });
  assert.equal(peak, 4);
  assert.deepEqual(loaded.map(({ tile }) => tile.url).sort(), tiles.map(({ url }) => url).filter((url) => url !== skip).sort());
});

test('aborting a viewport cancels current fetches and skips queued tiles', async () => {
  const tiles = planRasterTiles('landscape', city, size).tiles;
  const controller = new AbortController();
  let started = 0;
  const pending = fetchRasterTiles(tiles, controller.signal, (url, { signal }) => {
    started += 1;
    return new Promise((_, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  });
  controller.abort();
  assert.deepEqual(await pending, []);
  assert.ok(started < tiles.length);
  assert.equal(started, 4);
});
