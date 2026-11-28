const assert = require('node:assert/strict');

module.exports = async function mapFaceliftFlow(browser, errors, newPage, webBase, apiBase, screenshot = async () => {}) {
  await checkCompactMap(browser, errors, newPage, webBase, screenshot);
  const page = await newPage(browser, 1212, 799, errors);
  let albumId;
  try {
    await page.addInitScript(() => localStorage.setItem('face-manager:help-introduction-v1', 'seen'));
    await page.addInitScript(() => {
      const NativeEventSource = window.EventSource;
      window.EventSource = class extends NativeEventSource {
        constructor(url) {
          super(url);
          window.mapTestEvents = this;
        }
      };
    });
    const photosResponse = await page.request.get(`${apiBase}/map/images?west=-180&east=180&south=-90&north=90&limit=12`);
    assert.ok(photosResponse.ok());
    const { items } = await photosResponse.json();
    assert.ok(items.length >= 12);
    const created = await page.request.post(`${apiBase}/albums`, { data: {
      name: 'Reiseroute im Verlaufstest', image_ids: items.map(({ id }) => id),
      journey: { from_date: '2025-01-01', to_date: '2025-12-31', detail: 'trip' },
    } });
    assert.equal(created.status(), 201, await created.text());
    albumId = (await created.json()).id;

    const route = `#/weltkarte/album/${albumId}`;
    await page.goto(`${webBase}/${route}`);
    const map = page.getByRole('group', { name: 'Weltkarte mit Fotostandorten' });
    const locations = page.getByRole('button', { name: 'Standorte', exact: true });
    const journey = page.getByRole('button', { name: 'Verlauf', exact: true });
    const from = page.getByLabel('Von', { exact: true });
    await journey.and(page.locator('[aria-pressed=true]')).waitFor();
    assert.equal(await from.inputValue(), '01.01.2025');
    await locations.click();
    assert.equal(new URL(page.url()).hash, route);
    await page.getByRole('button', { name: 'Filter zurücksetzen', exact: true }).click();
    await page.waitForFunction(() => location.hash === '#/weltkarte');
    assert.equal(await from.count(), 0);
    await page.getByRole('button', { name: 'Zurück', exact: true }).click();
    await page.waitForFunction((hash) => location.hash === hash, route);
    await locations.and(page.locator('[aria-pressed=true]')).waitFor();
    assert.equal(await from.inputValue(), '01.01.2025');
    await page.getByRole('button', { name: 'Zurück', exact: true }).click();
    await journey.and(page.locator('[aria-pressed=true]')).waitFor();
    assert.equal(new URL(page.url()).hash, route);
    await page.getByRole('button', { name: 'Vorwärts', exact: true }).click();
    await locations.and(page.locator('[aria-pressed=true]')).waitFor();
    assert.equal(await from.inputValue(), '01.01.2025');
    await page.getByRole('button', { name: 'Zur Ausgangsansicht', exact: true }).click();
    await page.waitForFunction(() => location.hash === '#/weltkarte');
    await locations.and(page.locator('[aria-pressed=true]')).waitFor();
    assert.equal(await from.count(), 0);
    await page.reload();
    await locations.and(page.locator('[aria-pressed=true]')).waitFor();
    assert.equal(new URL(page.url()).hash, '#/weltkarte');
    await page.locator('.gps-map__marker').first().waitFor();
    await screenshot(page, 'kompakte-fotokarte');
    const mapTop = await map.evaluate((element) => Math.round(element.getBoundingClientRect().top));
    assert.ok(mapTop < 540, `The compact map starts at ${mapTop}px`);
    await checkMapRefresh(page);

    const stops = items.slice(0, 11).map((image, index) => ({
      id: `review-${index}`, number: index + 1, section: 1,
      latitude: image.latitude, longitude: image.longitude,
      start: '2026-04-11T11:00:00', end: '2026-04-11T11:20:00', uncertain: false,
      image_ids: index ? [image.id] : [image.id, items[11].id],
      inferred_image_ids: index ? [] : [items[11].id],
    }));
    await page.route('**/api/map/journey**', (request) => request.fulfill({ json: {
      stops, connections: [], photo_count: 12, missing_location_count: 0, inferred_location_count: 1,
      missing_time_count: 0, uncertain_count: 0, indexing: false,
    } }));
    await journey.click();
    const stopList = page.getByRole('list', { name: 'Chronologische Stopps' });
    await stopList.getByRole('button', { name: /nach Aufnahmezeit zugeordnet/ }).first().click();
    const provenance = (scope) => scope.getByText('Nach Aufnahmezeit zugeordnet', { exact: true });
    const sourceFitsTile = (element) => {
      const note = element.getBoundingClientRect();
      const tile = element.closest('button').getBoundingClientRect();
      return note.height > 0 && note.top >= tile.top && note.bottom <= tile.bottom;
    };
    const results = page.locator('#gps-map-results');
    await provenance(results).waitFor();
    assert.ok(await provenance(results).evaluate(sourceFitsTile), 'Inferred photo provenance is clipped in results');
    await results.getByRole('button', { name: 'Galerie öffnen', exact: true }).click();
    const gallery = page.getByRole('dialog', { name: 'Galerie der Kartenauswahl' });
    assert.ok(await provenance(gallery).evaluate(sourceFitsTile), 'Inferred photo provenance is clipped in map gallery');
    await gallery.getByRole('button', { name: 'Galerie schließen', exact: true }).click();

    await page.setViewportSize({ width: 320, height: 680 });
    await page.getByRole('group', { name: 'Kartenansicht' }).getByRole('button', { name: 'Stopps', exact: true }).click();
    await page.getByRole('button', { name: 'Fotobuchbild gestalten', exact: true }).click();
    const book = page.getByRole('dialog', { name: 'Fotobuchbild erstellen' });
    const preview = book.locator('.gps-album-dialog__preview[aria-busy="false"] img');
    await preview.waitFor();
    assert.ok(await book.evaluate((element) => {
      const preview = element.querySelector('img').getBoundingClientRect();
      const editor = element.querySelector('.gps-album-dialog__editor').getBoundingClientRect();
      return preview.top < editor.top && preview.bottom <= element.getBoundingClientRect().bottom && element.clientWidth === element.scrollWidth;
    }), 'The print preview does not fit in the initial narrow dialog');
    await screenshot(page, 'druckvorschau-schmal');
    assert.equal(await book.getByLabel('Layout', { exact: true }).inputValue(), 'map');
    await book.getByLabel('Layout', { exact: true }).selectOption('list');
    await book.getByRole('button', { name: 'Nächste Seite', exact: true }).click();
    const pages = book.getByRole('group', { name: 'Fotobuchseiten' });
    await pages.getByText(/Seite 2 von /).waitFor();
    await verifyAndroidExports(page, book);
    const title = book.getByLabel('Titel', { exact: true });
    await title.fill('Geändertes Fotobuch');
    await book.getByText('Vorschau wird aktualisiert …', { exact: true }).waitFor();
    assert.ok(await pages.getByText(/Seite 2 von /).isVisible(), 'Pagination disappeared while replacing the preview');

    const discard = page.getByRole('dialog', { name: 'Änderungen am Fotobuch verwerfen?' });
    await page.evaluate(() => history.back());
    await discard.waitFor();
    await discard.getByRole('button', { name: 'Weiter bearbeiten', exact: true }).click();
    assert.equal(await title.inputValue(), 'Geändertes Fotobuch');
    await page.keyboard.press('Escape');
    await discard.getByRole('button', { name: 'Entwurf verwerfen', exact: true }).click();
    await book.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Fotobuchbild gestalten', exact: true }).and(page.locator(':focus')).waitFor();
    assert.equal(new URL(page.url()).hash, '#/weltkarte');
    await journey.and(page.locator('[aria-pressed=true]')).waitFor();
    console.log('PASS compact map, album history and reset, stable map refresh, visible provenance, narrow print preview and draft Back');
  } finally {
    if (albumId) await page.request.delete(`${apiBase}/albums/${albumId}`);
    await page.close();
  }
};

async function verifyAndroidExports(page, book) {
  const staged = [];
  await page.route('**/api/android/exports', async (route) => {
    const data = route.request().postDataBuffer();
    staged.push(data.readUInt32BE(0));
    return route.fulfill({ json: { token: `mock-token-${staged.length}` } });
  });
  await page.route('**/api/android/exports/**', (route) => route.fulfill({ status: 204 }));
  await page.evaluate(() => {
    const files = [];
    const statuses = new Map();
    window.mapAndroidFiles = files;
    window.AndroidExport = {
      requestDatabaseExport() {},
      requestStagedFileExport(token, filename) {
        files.push(filename);
        statuses.set(token, 'PENDING');
        setTimeout(() => {
          statuses.set(token, 'SAVED');
          window.dispatchEvent(new Event('face-manager:android-export'));
        }, 30);
      },
      getStagedFileExportStatus: (token) => statuses.get(token) ?? 'IDLE',
    };
  });
  await book.getByRole('button', { name: 'Diese Seite als PNG speichern', exact: true }).click();
  await book.getByText('PNG gespeichert.', { exact: true }).waitFor();
  await book.getByRole('button', { name: 'Alle Seiten als ZIP speichern', exact: true }).click();
  await book.getByText('ZIP gespeichert.', { exact: true }).waitFor();
  assert.deepEqual(staged, [0x89504e47, 0x504b0304]);
  const files = await page.evaluate(() => window.mapAndroidFiles);
  assert.match(files[0], /-02\.png$/);
  assert.match(files[1], /\.zip$/);
}

async function checkCompactMap(browser, errors, newPage, webBase, screenshot) {
  const page = await newPage(browser, 320, 760, errors, { isMobile: true, hasTouch: true });
  try {
    await page.addInitScript(() => localStorage.setItem('face-manager:help-introduction-v1', 'seen'));
    await page.goto(`${webBase}/#/weltkarte`);
    const panes = page.getByRole('group', { name: 'Kartenansicht' });
    const mapButton = panes.getByRole('button', { name: 'Karte', exact: true });
    const photoButton = panes.getByRole('button', { name: 'Fotos', exact: true });
    const filters = page.getByRole('button', { name: 'Kartenfilter', exact: true });
    const map = page.getByRole('group', { name: 'Weltkarte mit Fotostandorten' });
    const results = page.locator('#gps-map-results');
    await map.waitFor();
    await page.locator('.gps-map__marker').first().waitFor();
    assert.equal(await mapButton.getAttribute('aria-pressed'), 'true');
    assert.equal(await results.isHidden(), true);
    assert.equal(await filters.getAttribute('aria-expanded'), 'false');
    const frame = await map.boundingBox();
    assert.ok(frame && frame.y < 650 && frame.width <= 320, 'The map does not fit in the initial compact workspace');
    await screenshot(page, 'fotokarte-schmal');

    const mapControls = page.getByRole('group', { name: 'Kartensteuerung' });
    const resetMap = mapControls.getByRole('button', { name: 'Weltkarte anzeigen' });
    const touch = await page.context().newCDPSession(page);
    await verifyPinch(page, map, touch);
    assert.equal(await mapButton.getAttribute('aria-pressed'), 'true', 'Pinching activated a map marker');
    await resetMap.click();

    await page.getByRole('button', { name: 'Bereich wählen', exact: true }).click();
    await verifyPinch(page, map, touch);
    assert.equal(await map.locator('.gps-map__selection').count(), 0, 'Pinching selected a photo area');
    const zoomedFrame = await map.boundingBox();
    const contact = (x, y) => [{ id: 3, x: zoomedFrame.x + x * zoomedFrame.width, y: zoomedFrame.y + y * zoomedFrame.height }];
    await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: contact(.2, .25) });
    await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: contact(.8, .75) });
    await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await map.locator('.gps-map__selection').waitFor();
    await page.getByRole('button', { name: 'Auswahl löschen', exact: true }).click();
    await resetMap.click();
    await touch.detach();

    const pan = mapControls.getByText('Verschieben', { exact: true });
    const east = mapControls.getByRole('button', { name: 'Nach Osten verschieben' });
    assert.equal(await east.count(), 0);
    await mapControls.getByRole('button', { name: 'Vergrößern' }).click();
    const zoomed = await map.getAttribute('viewBox');
    await pan.click();
    await east.press('Enter');
    assert.notEqual(await map.getAttribute('viewBox'), zoomed);
    await pan.click();
    await resetMap.click();

    await filters.click();
    const period = page.getByLabel('Zeitraum', { exact: true });
    await period.selectOption('custom');
    const from = page.getByLabel('Von', { exact: true });
    await from.waitFor();
    await from.press('Escape');
    assert.equal(await filters.getAttribute('aria-expanded'), 'false');
    assert.ok(await filters.evaluate((element) => document.activeElement === element));
    await filters.click();
    assert.ok(await from.isVisible(), 'Custom dates were lost after collapsing the filters');
    await period.selectOption('all');
    await filters.click();

    await photoButton.click();
    await results.getByText('85 Fotos', { exact: true }).waitFor();
    assert.equal(await map.count(), 0);
    await screenshot(page, 'kartenfotos-schmal');
    const scroll = await results.evaluate((element) => { element.scrollTop = 300; return element.scrollTop; });
    assert.ok(scroll > 150);
    await mapButton.click();
    await map.waitFor();
    await photoButton.click();
    await results.waitFor();
    assert.equal(await results.evaluate((element) => element.scrollTop), scroll);

    await mapButton.click();
    const marker = page.locator('.gps-map__marker').first();
    await marker.press('Enter');
    await results.waitFor();
    assert.equal(await photoButton.getAttribute('aria-pressed'), 'true');
    assert.ok(await results.evaluate((element) => document.activeElement === element), 'A marker did not transfer focus to its photos');
    assert.equal(await results.evaluate((element) => element.scrollTop), 0);
    await page.setViewportSize({ width: 768, height: 600 });
    await panes.waitFor();
    assert.equal(await photoButton.getAttribute('aria-pressed'), 'true');
    assert.ok(await results.isVisible());
    await mapButton.click();
    await page.locator('.gps-map__marker').first().tap();
    await results.waitFor();
    await page.route((url) => /^\/api\/images\/\d+\/thumbnail$/.test(url.pathname), (route) =>
      route.fulfill({ status: 503 }));
    await page.reload();
    await photoButton.click();
    const unavailablePreview = results.locator('.gps-page__photo').filter({ hasText: 'Vorschau nicht verfügbar' }).first();
    await unavailablePreview.waitFor();
    assert.ok(await unavailablePreview.isEnabled(), 'An unavailable preview blocked the photo action');
    console.log('PASS compact map: two-finger zoom, date filters, unavailable previews, photo scroll and marker shortcut');
  } finally {
    await page.close();
  }
}

async function verifyPinch(page, map, touch) {
  await map.scrollIntoViewIfNeeded();
  const frame = await map.boundingBox();
  const width = Number((await map.getAttribute('viewBox')).split(' ')[2]);
  const contacts = (halfWidth) => [1, 2].map((id) => ({
    id, x: frame.x + frame.width * .58 + (id === 1 ? -halfWidth : halfWidth),
    y: frame.y + frame.height / 2,
  }));
  await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: contacts(28) });
  await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: contacts(59) });
  await page.waitForFunction((originalWidth) => Number(document.querySelector('.gps-map__svg')
    .getAttribute('viewBox').split(' ')[2]) < originalWidth * .6, width);
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForFunction((originalWidth) => Number(document.querySelector('.gps-map__svg')
    .getAttribute('viewBox').split(' ')[2]) < originalWidth * .6, width);
}

async function checkMapRefresh(page) {
  const places = page.getByText('Orte aus meinen Fotos', { exact: true });
  await places.click();
  const placeSearch = page.getByRole('searchbox', { name: 'Fotoorte suchen' });
  await placeSearch.fill('Berlin');
  const tiles = page.locator('.gps-page__photo');
  const results = page.locator('#gps-map-results');
  const more = results.getByRole('button', { name: 'Weitere Fotos laden', exact: true });
  await more.click();
  await page.waitForFunction(() => document.querySelectorAll('#gps-map-results .gps-page__photo').length === 72);
  const initialScroll = await results.evaluate((element) => {
    element.scrollTop = 250;
    return element.scrollTop;
  });
  assert.ok(initialScroll > 100);

  const matches = (url) => url.pathname === '/api/map/images' && !url.searchParams.has('before_id');
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(matches, async (route) => {
    await gate;
    await route.continue();
  }, { times: 1 });
  try {
    const request = page.waitForRequest((pending) => matches(new URL(pending.url())));
    await page.evaluate(() => window.mapTestEvents.dispatchEvent(new MessageEvent('clusters', {
      data: '{"reason":"albums_changed"}',
    })));
    await request;
    assert.equal(await tiles.count(), 72, 'A background refresh cleared the previous map photos');
    assert.ok(await page.locator('.gps-map__marker').first().isVisible());
    assert.ok(await placeSearch.isVisible(), 'A background refresh closed the photo place search');
    assert.equal(await placeSearch.inputValue(), 'Berlin');
    assert.equal(await results.evaluate((element) => element.scrollTop), initialScroll);
    release();
    await more.and(page.locator(':enabled')).waitFor();
    assert.equal(await tiles.count(), 72, 'A background refresh discarded loaded map pages');
    assert.equal(await results.evaluate((element) => element.scrollTop), initialScroll);
  } finally {
    release();
    await places.click();
  }
}
