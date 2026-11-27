#!/usr/bin/env node
// Start the frontend against the library made with scripts/map-review-fixture.py --index.
// Run with: node frontend/tests/map-review-flow.cjs FRONTEND_URL FIXTURE_DIRECTORY
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const [webBase, fixture] = process.argv.slice(2);
assert.ok(webBase && fixture, 'Provide the frontend URL and fixture directory');
const manifest = JSON.parse(fs.readFileSync(path.join(fixture, 'manifest.json'), 'utf8'));

async function mapPoints(page, search = '') {
  const response = await page.request.get(`${webBase}/api/map/points${search}`);
  assert.ok(response.ok(), await response.text());
  return response.json();
}

async function screenshot(page, label) {
  await page.screenshot({ path: path.join(fixture, `nachher-${label}.png`) });
}

async function selectYear(page, year) {
  const updated = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/map/points' && url.searchParams.get('from_date') === `${year}-01-01` && response.ok();
  });
  await page.getByLabel('Zeitraum', { exact: true }).selectOption(`year-${year}`);
  const points = await (await updated).json();
  assert.ok(points.total > 0 && points.total < manifest.with_gps);
}

async function checkLocations(page) {
  const points = await mapPoints(page);
  assert.equal(points.total, manifest.with_gps);
  await page.locator('.gps-map__marker').first().waitFor();
  await selectYear(page, 2021);
  await page.getByRole('button', { name: 'Filter zurücksetzen' }).click();

  const offsets = [];
  await page.route('**/api/map/observed-places?**', (route) => {
    // Short pages exercise the real API pagination with the fixture's 45 locations.
    const url = new URL(route.request().url());
    offsets.push(Number(url.searchParams.get('offset')));
    url.searchParams.set('limit', '8');
    return route.continue({ url: url.href });
  });
  await page.getByText('Orte aus meinen Fotos', { exact: true }).click();
  const placeSearch = page.getByRole('searchbox', { name: 'Fotoorte suchen' });
  await placeSearch.fill('Marseille');
  const years = page.getByRole('group', { name: 'Aufnahmejahre in Marseille' });
  const yearChip = (year) => years.getByRole('button', { name: `Marseille im Jahr ${year} anzeigen` });
  await yearChip(2021).waitFor();
  assert.deepEqual(await years.getByRole('button').allTextContents().then((labels) => labels.map((label) => label.slice(0, 4))),
    ['2026', '2025', '2023', '2021']);
  assert.ok(offsets.some((offset) => offset > 0), 'The search stopped at the first result page');
  await screenshot(page, 'marseille-aufnahmejahre');
  await yearChip(2025).click();
  assert.equal(await page.getByLabel('Von', { exact: true }).inputValue(), '01.01.2025');
  assert.equal(await page.getByLabel('Bis', { exact: true }).inputValue(), '31.12.2025');
  await page.getByRole('button', { name: 'Bereichsfilter entfernen' }).filter({ hasText: 'Marseille' }).waitFor();
  const results = page.locator('#gps-map-results');
  await results.getByText('31 Fotos', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Fotobuchbild gestalten', exact: true }).click();
  const book = page.getByRole('dialog', { name: 'Fotobuchbild erstellen' });
  await book.getByText('31 Fotos im Kartenausschnitt', { exact: true }).waitFor();
  await book.getByText('Ausschnitt anpassen', { exact: true }).click();
  const cropResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/points/selection') && url.searchParams.get('grid_columns') === '18' && response.ok();
  });
  await book.getByRole('slider', { name: 'Zoom', exact: true }).press('End');
  const crop = await cropResponse;
  const { selection: cropScope } = crop.request().postDataJSON();
  assert.equal(cropScope.from_date, '2025-01-01');
  assert.ok(cropScope.bounds, 'Changing the frame must retain the selected place');
  const { total: croppedPhotos } = await crop.json();
  assert.ok(croppedPhotos <= 31);
  await book.getByText(`${croppedPhotos} ${croppedPhotos === 1 ? 'Foto' : 'Fotos'} im Kartenausschnitt`, { exact: true }).waitFor();
  await book.getByRole('button', { name: 'Ausschnitt zurücksetzen', exact: true }).click();
  await book.getByText('31 Fotos im Kartenausschnitt', { exact: true }).waitFor();
  await book.getByRole('button', { name: 'Schließen', exact: true }).click();
  const outside = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/points/selection') && Number(url.searchParams.get('east')) < 0 && response.ok();
  });
  await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Nordamerika');
  assert.equal((await (await outside).json()).total, 0, 'Pins outside the fixed photo selection were counted');
  await results.getByText('31 Fotos', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Filterbereich zeigen', exact: true }).click();
  await results.getByRole('button', { name: 'Verlauf dieser Auswahl', exact: true }).click();
  await page.getByText(/Stopps · 31 Fotos/).waitFor();
  await results.getByRole('button', { name: 'Als Reisealbum speichern', exact: true }).waitFor();
  await page.getByRole('navigation', { name: 'Ansichtsverlauf' }).getByRole('button', { name: 'Zurück', exact: true }).click();
  await results.getByText('31 Fotos', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Filter zurücksetzen' }).click();
  console.log('PASS historical period, paged place search and consistent location and journey scope for Marseille');
}

async function checkJourney(page) {
  await page.getByRole('button', { name: 'Verlauf', exact: true }).click();
  const map = page.getByRole('group', { name: 'Weltkarte mit Fotostandorten' });
  const markers = map.getByRole('button', { name: /\d+ Stopps an dieser Stelle/ });
  const list = page.getByRole('list', { name: 'Chronologische Stopps' });
  const marker = markers.first();
  await marker.waitFor();
  const markerCount = Number((await marker.getAttribute('aria-label')).match(/^\d+/)[0]);
  await marker.press('Enter');
  await page.getByText(`${markerCount} Stopps an dieser Stelle auf der Karte`).waitFor();
  assert.equal(await list.getByRole('button').count(), markerCount);
  await page.getByRole('button', { name: 'Alle Stopps zeigen' }).click();
  const stopSearch = page.getByRole('searchbox', { name: 'Stoppliste durchsuchen' });
  for (const search of ['19.07.2025', '19. Juli 2025', '2025-07-19']) {
    await stopSearch.fill(search);
    await page.getByText('5 von 503 Stopps in der Liste', { exact: true }).waitFor();
  }
  await stopSearch.clear();

  const trips = page.locator('details').filter({ has: page.getByText('Reisen entdecken', { exact: true }) });
  await trips.getByText('Reisen entdecken', { exact: true }).click();
  const expected = manifest.trips.find((trip) => trip.from === '2025-07-19');
  assert.ok(expected);
  const vacation = trips.getByRole('button', { name: /19\.07\.2025\s*[–-]\s*26\.07\.2025/ });
  await vacation.waitFor();
  await vacation.filter({ hasText: /Stuttgart – .+ – Stuttgart/ }).waitFor();
  assert.equal(await trips.getByRole('button').count(), manifest.trips.length);
  const tripSearch = trips.getByRole('searchbox', { name: 'Reisen suchen' });
  await tripSearch.fill('Lyon 2025');
  await vacation.waitFor();
  assert.ok(await trips.getByRole('button').count() < manifest.trips.length);
  await tripSearch.clear();
  await vacation.click();
  await page.getByRole('button', { name: 'Reisefilter entfernen' }).waitFor();
  await page.getByText('37 Stopps', { exact: false }).first().waitFor();
  await page.getByRole('button', { name: 'Route einpassen' }).click();
  const framed = await map.getAttribute('viewBox');
  await map.press('ArrowRight');
  assert.notEqual(await map.getAttribute('viewBox'), framed);
  await page.getByRole('button', { name: 'Route einpassen' }).click();
  assert.equal(await map.getAttribute('viewBox'), framed);
  const exact = page.waitForRequest((request) => request.url().includes('/api/map/images/selection?') && request.method() === 'POST');
  await page.getByRole('button', { name: 'Standorte', exact: true }).click();
  const { selection } = (await exact).postDataJSON();
  assert.ok(selection.image_ids.length > 100 && selection.image_ids.length < manifest.with_gps);
  assert.ok(selection.bounds, 'The location viewport must still restrict the selected IDs');
  await page.getByRole('button', { name: 'Reisefilter entfernen' }).waitFor();
  await page.getByRole('button', { name: 'Verlauf', exact: true }).click();
  await page.getByText(/37 Stopps ·/).waitFor();
  await list.getByRole('button').first().click();
  await page.getByRole('button', { name: 'Stopp auf Karte zeigen', exact: true }).waitFor();
  await page.getByLabel('Stoppabstand', { exact: true }).selectOption('city');
  await list.getByRole('button').first().waitFor();
  const cityFrameWidth = Number((await map.getAttribute('viewBox')).split(' ')[2]);
  assert.ok(cityFrameWidth <= 0.625, 'Changing the stop spacing should keep the selected stop in a street-level view');
  await page.getByLabel('Stoppabstand', { exact: true }).selectOption('trip');
  await page.getByText(/37 Stopps ·/).waitFor();
  await page.locator('.gps-page__header').scrollIntoViewIfNeeded();
  await screenshot(page, 'route-provence');
  console.log('PASS overlapping stops, 15 coherent trips, Provence fixture scope and route framing');
}

async function checkPhotobook(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  const pane = page.getByRole('group', { name: 'Kartenansicht' });
  await pane.getByRole('button', { name: 'Stopps', exact: true }).click();
  const exportAction = page.getByRole('button', { name: 'Fotobuchbild gestalten' });
  assert.ok(await exportAction.isVisible());
  await exportAction.click();
  const dialog = page.getByRole('dialog', { name: 'Fotobuchbild erstellen' });
  const layout = dialog.getByRole('combobox', { name: /^Layout/ });
  const area = dialog.getByRole('combobox', { name: /^Kartenbereich/ });
  const preview = dialog.locator('.gps-album-dialog__preview[aria-busy="false"] img');
  const pagination = dialog.getByRole('group', { name: 'Fotobuchseiten' });
  await preview.waitFor();
  assert.equal(await layout.inputValue(), 'map');
  assert.equal(await pagination.count(), 0);
  const currentSvg = () => preview.evaluate((element) => fetch(element.src).then((response) => response.text()));
  await layout.selectOption({ label: 'Randlose Karte' });
  await preview.waitFor();
  assert.match(await currentSvg(), /data-album-bleed="true"/);
  assert.equal(await dialog.getByLabel('Titel', { exact: true }).count(), 0);
  await dialog.getByText('Ausschnitt anpassen', { exact: true }).click();
  await dialog.getByRole('slider', { name: 'Zoom', exact: true }).press('End');
  await dialog.getByRole('button', { name: 'Ausschnitt nach rechts', exact: true }).click();
  await preview.waitFor();
  const cropped = await dialog.getByRole('status').filter({ hasText: /Stopps? im Kartenausschnitt/ }).textContent();
  assert.ok(Number(cropped.match(/^\d+/)[0]) < 37, 'The custom frame did not crop the route');
  await dialog.getByRole('button', { name: 'Ausschnitt zurücksetzen', exact: true }).click();
  await dialog.getByText('37 Stopps im Kartenausschnitt', { exact: true }).waitFor();
  await screenshot(page, 'fotobuch-randlos');
  const bleedDownload = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'PNG speichern', exact: true }).click();
  await (await bleedDownload).saveAs(path.join(fixture, 'fotobuch-provence-randlos.png'));
  await dialog.getByText('Ausschnitt anpassen', { exact: true }).click();
  await layout.selectOption({ label: 'Karte mit Stoppliste' });
  await pagination.getByText(/Seite 1 von /).waitFor();
  await layout.selectOption({ label: 'Karte auf einer Seite' });
  await pagination.waitFor({ state: 'hidden' });
  await area.selectOption({ label: 'Sichtbarer Kartenausschnitt' });
  await preview.waitFor();
  assert.equal(await area.inputValue(), 'viewport');
  await area.selectOption({ label: 'Ganze Auswahl einrahmen' });
  await dialog.getByLabel('Titel', { exact: true }).fill('Provence und Côte d’Azur · 2025');
  await dialog.getByLabel('Farbwelt', { exact: true }).selectOption('coast');
  await preview.waitFor();
  const image = await currentSvg();
  assert.match(image, /Provence und Côte d’Azur · 2025/);
  assert.ok(await dialog.evaluate((element) => element.scrollWidth === element.clientWidth));
  await screenshot(page, 'fotobuch-mobil');
  const download = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'PNG speichern', exact: true }).click();
  const file = path.join(fixture, 'fotobuch-provence-2025.png');
  await (await download).saveAs(file);
  const png = fs.readFileSync(file);
  assert.equal(png.subarray(1, 4).toString(), 'PNG');
  assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], [3508, 2480]);
  const density = png.indexOf('pHYs');
  assert.ok(density >= 0);
  assert.deepEqual([png.readUInt32BE(density + 4), png.readUInt32BE(density + 8), png[density + 12]], [11811, 11811, 1]);
  console.log(`PASS mobile photobook workflow, one-page map and verified A4 PNG (300 dpi), ${file}`);
}

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, acceptDownloads: true });
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('face-manager:help-introduction-v1', 'seen'));
    await page.goto(`${webBase}/#/weltkarte`);
    await checkLocations(page);
    await checkJourney(page);
    await checkPhotobook(page);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
