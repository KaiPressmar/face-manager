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
  await page.getByRole('button', { name: 'Filter zurücksetzen' }).click();
  console.log('PASS geotagged library, historical period and Marseille across four years with paged search');
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

  const trips = page.locator('details').filter({ has: page.getByText('Reisen entdecken', { exact: true }) });
  await trips.getByText('Reisen entdecken', { exact: true }).click();
  const expected = manifest.trips.find((trip) => trip.from === '2025-07-19');
  assert.ok(expected);
  const vacation = trips.getByRole('button', { name: /19\.07\.2025\s*[–-]\s*26\.07\.2025/ });
  await vacation.waitFor();
  await vacation.filter({ hasText: /Stuttgart – .+ – Stuttgart/ }).waitFor();
  assert.equal(await trips.getByRole('button').count(), manifest.trips.length);
  await vacation.click();
  await page.getByRole('button', { name: 'Reisefilter entfernen' }).waitFor();
  await page.getByText('37 Stopps', { exact: false }).first().waitFor();
  await page.getByRole('button', { name: 'Route einpassen' }).click();
  const framed = await map.getAttribute('viewBox');
  await map.press('ArrowRight');
  assert.notEqual(await map.getAttribute('viewBox'), framed);
  await page.getByRole('button', { name: 'Route einpassen' }).click();
  assert.equal(await map.getAttribute('viewBox'), framed);
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
  const image = await preview.evaluate((element) => fetch(element.src).then((response) => response.text()));
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
