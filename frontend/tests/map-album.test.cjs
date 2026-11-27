const test = require('node:test');
const assert = require('node:assert/strict');
const { albumOutputSize, albumPhotoQueryBounds, buildMapAlbumArtwork, fitAlbumJourney, normalizeAlbumColor, setMapAlbumPngDensity } =
  require('./load-typescript.cjs')('src/components/map/MapAlbumArtwork.ts');
const { archiveMapAlbumPages } = require('./load-typescript.cjs')('src/components/map/mapAlbumArchive.ts');

const world = { west: -180, east: 180, south: -90, north: 90 };
const artwork = { format: 'a4', title: 'Berlin & Paris <2026>', bounds: world, style: 'standard' };
const markerNumbers = ({ svg }) => [...svg.matchAll(/data-album-marker="(\d+)"(?: data-album-visits="([\d,]+)")?/g)]
  .flatMap(([, number, visits]) => (visits || number).split(',').map(Number)).sort((a, b) => a - b);

test('unavailable satellite imagery falls back to attributed vector artwork', async () => {
  // No browser raster decoder is available in this runner.
  const [page] = await buildMapAlbumArtwork({ ...artwork, style: 'satellite',
    points: [{ latitude: 48.86, longitude: 2.35, count: 5 }],
  });
  assert.equal(page.satelliteFallback, true);
  assert.match(page.svg, /Kartendaten von Natural Earth/);
  assert.doesNotMatch(page.svg, /NASA Earth Observatory|data-album-satellite/);
  assert.match(page.svg, /data-album-marker="5"/);
});

test('print presets preserve physical dimensions across resolutions and constrain custom sizes', async () => {
  assert.deepEqual(albumOutputSize('a4', 150), { width: 1754, height: 1240 });
  assert.deepEqual(albumOutputSize('portrait', 400), { width: 3307, height: 4677 });
  assert.deepEqual(albumOutputSize('square', 400), { width: 4000, height: 4000 });
  assert.deepEqual(albumOutputSize('square-210', 300), { width: 2480, height: 2480 });
  assert.deepEqual(albumOutputSize('square-300', 300), { width: 3543, height: 3543 });
  assert.equal(albumOutputSize('square-300', 400), null);
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
  assert.deepEqual(markerNumbers(wide[0]), stops.map(({ number }) => number));
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

test('city-day fitting accepts a tighter minimum without changing the print default', () => {
  const stops = [{ latitude: 48.86, longitude: 2.35 }];
  const full = fitAlbumJourney(stops, []);
  const close = fitAlbumJourney(stops, [], .012);
  assert.ok(full.east - full.west > 1.99);
  assert.ok(close.east - close.west < .025);
  assert.ok(close.north > stops[0].latitude && close.south < stops[0].latitude);
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
  assert.deepEqual(markerNumbers(pages[0]), stops.map(({ number }) => number));
  assert.match(pages[1].svg, /Weitere Stationen/);
  assert.match(pages[1].svg, />2 \/ 4</);
  const compact = await buildMapAlbumArtwork({ ...artwork, stops, connections, fitJourney: true, showStopsList: false });
  assert.equal(compact.length, 1);
  assert.doesNotMatch(compact[0].svg, /data-album-stop=/);
  assert.deepEqual(markerNumbers(compact[0]), stops.map(({ number }) => number));
});

test('return visits share a readable map marker while the complete stop list retains each visit', async () => {
  const berlin = { id: 's1', number: 1, title: 'Bei Berlin', placeName: 'Berlin', latitude: 52.52, longitude: 13.40 };
  const paris = { id: 's2', number: 2, title: 'Bei Paris', placeName: 'Paris', latitude: 48.86, longitude: 2.35 };
  const stops = [berlin, paris, { ...berlin, id: 's3', number: 3, longitude: 13.41 }];
  const input = { ...artwork, stops, fitJourney: true };
  const [compact] = await buildMapAlbumArtwork({ ...input, format: 'square-210', showStopsList: false });
  const [detailed] = await buildMapAlbumArtwork(input);
  assert.match(compact.svg, /data-album-marker="1" data-album-visits="1,3"/);
  assert.match(compact.svg, /data-album-place-label="true"[^>]*>Berlin<\/text>/);
  assert.match(compact.svg, /data-album-place-label="true"[^>]*>Paris<\/text>/);
  assert.match(detailed.svg, /data-album-stop="s3"[\s\S]*?Kartenpunkt 1/);
  assert.deepEqual(markerNumbers(detailed), [1, 2, 3]);
});

test('viewport export crops drawn visits and the stop list even when surrounding context is increased', async () => {
  const near = { id: 'near', number: 1, title: 'Bei Lyon', latitude: 45.76, longitude: 4.83 };
  const outside = { id: 'outside', number: 2, title: 'Bei Paris', latitude: 48.86, longitude: 2.35 };
  const stops = [near, outside];
  const input = { ...artwork, stops, connections: [{ from: near, to: outside }], fitJourney: false,
    bounds: { west: 3.8, east: 5.8, south: 44.75, north: 46.75 }, contextPercent: 100 };
  const [cropped] = await buildMapAlbumArtwork(input);
  assert.equal(cropped.visibleStopCount, 1);
  assert.match(cropped.svg, /data-album-connection=/);
  assert.match(cropped.svg, /data-album-stop="near"/);
  assert.doesNotMatch(cropped.svg, /data-album-stop="outside"|data-album-marker="2"/);
  assert.deepEqual(markerNumbers(cropped), [1]);
  const [complete] = await buildMapAlbumArtwork({ ...input, fitJourney: true });
  assert.deepEqual(markerNumbers(complete), [1, 2]);
});

test('single-page layout remains one image with many assigned transports and an optional title', async () => {
  const stops = Array.from({ length: 18 }, (_, index) => ({
    id: String(index + 1), number: index + 1, arrivalMode: 'train',
    title: `Stopp ${index + 1}`, longitude: 8 + index / 3, latitude: 49 + index / 5,
  }));
  const pages = await buildMapAlbumArtwork({ ...artwork, title: '', format: 'square-300',
    stops, fitJourney: true, showStopsList: false });
  assert.equal(pages.length, 1);
  assert.deepEqual([pages[0].width, pages[0].height], [3543, 3543]);
  assert.doesNotMatch(pages[0].svg, />Meine Fotokarte<\/text>/);
  assert.deepEqual(markerNumbers(pages[0]), stops.map(({ number }) => number));
});

test('full-bleed layout fills the print page and removes title, border and list while retaining source credit', async () => {
  const stops = [
    { id: 'first', number: 1, title: 'Bei Lyon', latitude: 45.76, longitude: 4.83 },
    { id: 'second', number: 2, title: 'Bei Paris', latitude: 48.86, longitude: 2.35, arrivalMode: 'train' },
  ];
  const pages = await buildMapAlbumArtwork({ ...artwork, title: 'Verdeckter Titel', period: 'Verdecktes Datum',
    format: 'square-210', fullBleed: true, stops, fitJourney: true, showStopsList: true });
  assert.equal(pages.length, 1);
  const { svg, width, height } = pages[0];
  assert.deepEqual([width, height], [2480, 2480]);
  assert.match(svg, /data-album-bleed="true" width="3000" height="3000"/);
  assert.match(svg, /id="album-map-clip"><rect x="0" y="0" width="3000" height="3000"\s/);
  assert.match(svg, /Kartendaten von Natural Earth/);
  assert.doesNotMatch(svg, /data-album-stop=|>Verdeckter Titel<\/text>|Verdecktes Datum|#9ead9f/);
  assert.deepEqual(markerNumbers(pages[0]), [1, 2]);
});

test('manual framing shares geographic bounds with photo queries and excludes cropped visits', async () => {
  const stops = [{ id: '1', number: 1, title: 'West', longitude: 2, latitude: 0 },
    { id: '2', number: 2, title: 'Ost', longitude: 7, latitude: 0 }];
  const bounds = { west: 0, east: 10, south: -5, north: 5 };
  const framing = { zoom: 200, horizontal: 25, vertical: 0 };
  assert.deepEqual(albumPhotoQueryBounds(bounds, framing), { west: 5, east: 10, south: -2.5, north: 2.5 });
  const [page] = await buildMapAlbumArtwork({ ...artwork, bounds, framing, stops, fitJourney: false,
    connections: [{ from: stops[0], to: stops[1] }] });
  assert.equal(page.visibleStopCount, 1);
  assert.deepEqual(markerNumbers(page), [2]);
  assert.match(page.svg, /data-album-stop="2"/);
  assert.doesNotMatch(page.svg, /data-album-stop="1"/);
  assert.deepEqual(albumPhotoQueryBounds({ west: 175, east: -175, south: -5, north: 5 }, { ...framing, horizontal: 0 }),
    { west: 177.5, east: -177.5, south: -2.5, north: 2.5 });
});

test('print names, connection lines and markers can be composed independently', async () => {
  const stops = [
    { id: '1', number: 1, title: 'Bei Berlin', placeName: 'Berlin', longitude: 13.4, latitude: 52.52 },
    { id: '2', number: 2, title: 'Hafen & Altstadt', placeName: 'Hafen & Altstadt', customPlaceName: true,
      longitude: 13.401, latitude: 52.52 },
    { id: '3', number: 3, title: 'Bei Potsdam', placeName: 'Potsdam', longitude: 13.06, latitude: 52.4 },
  ];
  const input = { ...artwork, stops, fitJourney: true, showStopsList: false, journeyPadding: .04,
    connections: [{ from: stops[0], to: stops[2] }] };
  const [named] = await buildMapAlbumArtwork({ ...input, markerStyle: 'dots', showRoute: false });
  assert.match(named.svg, /data-album-place-label="true"[^>]*>Hafen &amp; Altstadt<\/text>/);
  assert.doesNotMatch(named.svg, /data-album-connection=|>Berlin<\/text>|Zahlen zeigen/);
  assert.deepEqual(markerNumbers(named), [1, 2, 3]);
  const [unnamed] = await buildMapAlbumArtwork({ ...input, showPlaceNames: false });
  assert.doesNotMatch(unnamed.svg, /data-album-place-label/);
  assert.match(unnamed.svg, /data-album-connection=/);
  const [unmarked] = await buildMapAlbumArtwork({ ...input, markerStyle: 'none' });
  assert.equal(unmarked.visibleStopCount, 3);
  assert.doesNotMatch(unmarked.svg, /data-album-marker|data-album-place-label/);
  assert.match(unmarked.svg, /data-album-connection=/);
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

test('ZIP keeps checksums and stored entries intact across a checksum chunk boundary', async () => {
  const pages = [Buffer.alloc(262145, 'Z'), Buffer.from('PNG two')];
  const blob = await archiveMapAlbumPages(pages.map((page) => new Blob([page])));
  const zip = Buffer.from(await blob.arrayBuffer());
  let offset = 0;
  for (const [index, crc] of [0x4b8ae248, 0x2f8125c6].entries()) {
    assert.equal(zip.readUInt32LE(offset), 0x04034b50);
    assert.equal(zip.readUInt32LE(offset + 14), crc);
    assert.equal(zip.readUInt32LE(offset + 18), pages[index].length);
    const headerEnd = offset + 30 + zip.readUInt16LE(offset + 26);
    assert.equal(zip.toString('ascii', offset + 30, headerEnd), `Seite-0${index + 1}.png`);
    offset = headerEnd + pages[index].length;
    assert.deepEqual(zip.subarray(headerEnd, offset), pages[index]);
  }
  assert.equal(zip.readUInt32LE(offset), 0x02014b50);
  assert.equal(zip.readUInt16LE(zip.length - 12), pages.length);
});
