const test = require('node:test');
const assert = require('node:assert/strict');
const { albumOutputSize, buildMapAlbumArtwork, fitAlbumJourney, normalizeAlbumColor, setMapAlbumPngDensity } =
  require('./load-typescript.cjs')('src/components/map/MapAlbumArtwork.ts');

const world = { west: -180, east: 180, south: -90, north: 90 };
const artwork = { format: 'a4', title: 'Berlin & Paris <2026>', bounds: world, style: 'standard' };

test('print presets preserve physical dimensions across resolutions and constrain custom sizes', async () => {
  assert.deepEqual(albumOutputSize('a4', 150), { width: 1754, height: 1240 });
  assert.deepEqual(albumOutputSize('portrait', 400), { width: 3307, height: 4677 });
  assert.deepEqual(albumOutputSize('square', 400), { width: 4000, height: 4000 });
  for (const dimensions of [{ width: 1199, height: 1200 }, { width: 4801, height: 3000 },
    { width: 4600, height: 4000 }, { width: 3201, height: 1600 }, { width: 3000.2, height: 3000 }]) {
    assert.equal(albumOutputSize('custom', 240, dimensions), null);
  }
  const dimensions = { width: 3600, height: 2200 };
  const [page] = await buildMapAlbumArtwork({ ...artwork, format: 'custom', dimensions, dpi: 240 });
  assert.deepEqual([page.width, page.height, page.dpi], [3600, 2200, 240]);
  assert.match(page.svg, /width="3600" height="2200" viewBox="0 0 \d+ 2480"/);
});

test('custom colors retain exact swatches while paper labels and markers stay legible', async () => {
  assert.equal(normalizeAlbumColor('#FB1'), '#ffbb11');
  assert.equal(normalizeAlbumColor(' <script> '), undefined);
  const [page] = await buildMapAlbumArtwork({ ...artwork, showCountryNames: true,
    colors: { paper: '#141414', accent: '#141414', water: '#a2b3c4', land: '#222' },
    points: [{ latitude: 5, longitude: 5, count: 23 }], markerScale: 140,
    formatting: { typeface: 'sans', alignment: 'center', margins: 'wide', titleScale: 120 },
  });
  assert.match(page.svg, /<rect width="3508" height="2480" fill="#141414"/);
  assert.match(page.svg, /fill="#a2b3c4"/);
  assert.match(page.svg, /fill="#222222"/);
  assert.match(page.svg, /fill="#ffffff" font-family="Arial, Helvetica, sans-serif" font-size="174" font-weight="600" text-anchor="middle">Berlin/);
  const marker = page.svg.match(/data-album-marker="23"[\s\S]*?<\/g>/)?.[0];
  assert.match(marker, /r="57.4" fill="#141414" stroke="#141414"/);
  assert.match(marker, /fill="#ffffff" text-anchor="middle"/);
});

test('one-column formatting and context preserve all stops and grow the atlas visible extent', async () => {
  const stops = Array.from({ length: 17 }, (_, index) => ({
    id: `custom-${index}`, number: index + 1, title: `Station ${index + 1}`, subtitle: '01.02.2026 · 2 Fotos',
    latitude: 50 + index / 25, longitude: 12 + index / 10,
  }));
  const common = { ...artwork, format: 'custom', dimensions: { width: 3300, height: 2300 }, fitJourney: true,
    formatting: { listColumns: 1, titleScale: 130, alignment: 'center', margins: 'wide' }, stops };
  const tight = await buildMapAlbumArtwork(common);
  const wide = await buildMapAlbumArtwork({ ...common, contextPercent: 100 });
  const atlasSpans = (page) => page.svg.match(/<svg x="[^"]+" y="[^"]+" width="[^"]+" height="[^"]+"\s+viewBox="([^"]+)"/)[1].split(' ').map(Number);
  assert.ok(atlasSpans(wide[0])[2] > atlasSpans(tight[0])[2]);
  assert.equal((wide[0].svg.match(/data-album-marker=/g) ?? []).length, stops.length);
  assert.deepEqual(wide.flatMap((page) => [...page.svg.matchAll(/data-album-stop="(custom-\d+)"/g)].map((match) => match[1])),
    stops.map(({ id }) => id));
});

test('album covers fit dateline crossings and connections without wrapping across the world', () => {
  const east = { longitude: 176, latitude: -4 };
  const west = { longitude: -177, latitude: 12 };
  const bounds = fitAlbumJourney([east, west], [{ from: east, to: west }]);
  assert.ok(bounds.west < east.longitude && bounds.east > west.longitude + 360);
  assert.ok(bounds.east - bounds.west < 10);
  assert.ok(bounds.south < east.latitude && bounds.north > west.latitude);

  const around = [
    { longitude: -150, latitude: 0 }, { longitude: -30, latitude: 0 },
    { longitude: 90, latitude: 0 }, { longitude: -150, latitude: 0 },
  ];
  const connections = around.slice(1).map((to, index) => ({ from: around[index], to }));
  const wholeRoute = fitAlbumJourney(around, connections);
  assert.equal(wholeRoute.west, -180);
  assert.equal(wholeRoute.east, 180);
});

test('album artwork keeps exact print dimensions and escapes text for standalone SVG previews', async () => {
  for (const [format, width, height] of [['a4', 3508, 2480], ['portrait', 2480, 3508], ['square', 3000, 3000]]) {
    const [page] = await buildMapAlbumArtwork({
      ...artwork, format, palette: 'coast', subtitle: 'Zugänge & Ausflüge',
      points: [{ longitude: 14, latitude: 52, count: 29 }],
    });
    assert.deepEqual([page.width, page.height, page.number, page.total], [width, height, 1, 1]);
    assert.ok(page.svg.includes(`width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"`));
    assert.match(page.svg, /<title>Berlin &amp; Paris &lt;2026&gt;<\/title>/);
    assert.match(page.svg, /Zugänge &amp; Ausflüge/);
    assert.match(page.svg, /fill="#f0f5f6"/);
    assert.match(page.svg, /data-album-marker="29"/);
    assert.match(page.svg, /Kartendaten von Natural Earth/);
  }
});

test('dense journeys preserve all station entries on continuation pages and allow hiding the lists', async () => {
  const stops = Array.from({ length: 49 }, (_, index) => ({
    id: `s${index + 1}`, number: index + 1, title: `Station ${index + 1}`,
    subtitle: '01.01.2026 · 2 Fotos', longitude: -17 + index / 3, latitude: 35 + index / 5,
  }));
  const connections = stops.slice(1).map((to, index) => ({ from: stops[index], to }));
  const pages = await buildMapAlbumArtwork({ ...artwork, stops, connections, fitJourney: true });
  assert.equal(pages.length, 4);
  const listed = pages.flatMap((page) => [...page.svg.matchAll(/data-album-stop="(s\d+)"/g)].map((match) => match[1]));
  assert.deepEqual(listed, stops.map(({ id }) => id));
  assert.equal((pages[0].svg.match(/data-album-marker=/g) ?? []).length, stops.length);
  assert.match(pages[1].svg, /Weitere Stationen/);
  assert.match(pages[1].svg, />2 \/ 4</);
  const compact = await buildMapAlbumArtwork({ ...artwork, stops, connections, fitJourney: true, showStopsList: false });
  assert.equal(compact.length, 1);
  assert.doesNotMatch(compact[0].svg, /data-album-stop=/);
  assert.equal((compact[0].svg.match(/data-album-marker=/g) ?? []).length, stops.length);
});

test('portrait wraps a long unbroken title and preserves the full subtitle', async () => {
  const title = 'W'.repeat(72);
  const subtitle = 'Über die Berge '.repeat(6) + 'am Morgen';
  const [page] = await buildMapAlbumArtwork({ ...artwork, format: 'portrait', title, subtitle });
  assert.ok(page.svg.includes(`<title>${title}</title>`));
  assert.equal((page.svg.match(/>W{36}<\/text>/g) ?? []).length, 2);
  assert.ok(page.svg.includes(`>${subtitle}</text>`));
});

test('wrapped photo bounds preserve both sides and balanced framing in a square', async () => {
  const bounds = { west: 178, east: -178, south: -2, north: 2 };
  const input = { ...artwork, format: 'square', bounds, points: [
    { longitude: 179, latitude: 0, count: 3 },
    { longitude: -179, latitude: 1, count: 7 },
  ] };
  const [close] = await buildMapAlbumArtwork(input);
  const [wide] = await buildMapAlbumArtwork({ ...input, wideFrame: true, accent: 'plum' });
  for (const page of [close, wide]) {
    assert.match(page.svg, /data-album-marker="3"/);
    assert.match(page.svg, /data-album-marker="7"/);
  }
  assert.match(wide.svg, /#713f69/);
  assert.notEqual(close.svg.match(/viewBox="([^"]+)"/g)[1], wide.svg.match(/viewBox="([^"]+)"/g)[1]);
});

test('transport stays leg-specific on a return trip and appears in print even without the full stop list', async () => {
  const start = { id: '1', number: 1, longitude: 176, latitude: -4, title: 'Start' };
  const middle = { id: '2', number: 2, longitude: -177, latitude: 12, title: 'Ziel', arrivalMode: 'plane' };
  const end = { ...start, id: '3', number: 3, arrivalMode: 'boat' };
  const [page] = await buildMapAlbumArtwork({ ...artwork, stops: [start, middle, end], showStopsList: false, fitJourney: true,
    wideFrame: true, connections: [{ from: start, to: middle, transport: 'plane' }, { from: middle, to: end, transport: 'boat' }] });
  assert.equal((page.svg.match(/data-album-transport="plane"/g) ?? []).length, 1);
  assert.equal((page.svg.match(/data-album-transport="boat"/g) ?? []).length, 1);
  assert.match(page.svg, /Verkehrsmittel nach Zielstopp/);
  assert.match(page.svg, /Anreise: Flugzeug/);
  assert.match(page.svg, /Anreise: Schiff/);
  assert.doesNotMatch(page.svg, /data-album-stop="1"/);
});

test('PNG print density replaces rather than duplicates an existing physical-size chunk', async () => {
  const input = new Blob([Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhwsAAAAASUVORK5CYII=', 'base64')]);
  const first = await setMapAlbumPngDensity(input);
  const second = await setMapAlbumPngDensity(first, 240);
  const result = Buffer.from(await second.arrayBuffer());
  assert.equal(first.size, second.size);
  assert.equal(second.type, 'image/png');
  assert.equal(result.toString('ascii', 37, 41), 'pHYs');
  assert.equal(Buffer.from(await first.arrayBuffer()).readUInt32BE(41), 11811);
  assert.equal(result.readUInt32BE(41), 9449);
  assert.equal(result.readUInt32BE(45), 9449);
  assert.equal(result[49], 1);
});
