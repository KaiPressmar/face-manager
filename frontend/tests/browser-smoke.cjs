#!/usr/bin/env node
/* Run through scripts/check-ui.sh so the API and Vite use an isolated fixture. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  console.error('Playwright is unavailable. Set NODE_PATH to an external node_modules containing playwright.');
  process.exit(1);
}

const apiBase = process.env.UI_SMOKE_API_BASE;
const webBase = process.env.UI_SMOKE_WEB_BASE;
if (!apiBase || !webBase) {
  console.error('Run this test through scripts/check-ui.sh (or set UI_SMOKE_API_BASE and UI_SMOKE_WEB_BASE).');
  process.exit(1);
}
const screenshotDir = process.env.UI_SMOKE_SCREENSHOT_DIR;
if (screenshotDir) fs.mkdirSync(screenshotDir, { recursive: true });
const screenshot = async (page, name) => {
  if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, `${name}.png`), fullPage: true });
};

async function newPage(browser, width, height, errors) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(12000);
  page.on('pageerror', (error) => errors.push(error.message));
  // Android 12's system WebView in CI lacks Array.prototype.at.
  await page.addInitScript(() => Reflect.deleteProperty(Array.prototype, 'at'));
  return page;
}

async function assertFocusInDialog(page, dialog) {
  assert.ok(await dialog.evaluate((element) => element.contains(document.activeElement)), 'Focus escaped the open dialog');
}

async function assertTabTrap(page, dialog) {
  const focusable = dialog.locator('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])');
  const first = focusable.first();
  const last = focusable.last();
  await first.focus();
  await page.keyboard.press('Shift+Tab');
  assert.ok(await last.evaluate((element) => element === document.activeElement), 'Shift+Tab did not wrap to the last dialog control');
  await page.keyboard.press('Tab');
  assert.ok(await first.evaluate((element) => element === document.activeElement), 'Tab did not wrap to the first dialog control');
}

async function previewFlow(browser, errors) {
  const page = await newPage(browser, 1280, 800, errors);
  const originals = [];
  page.on('request', (request) => {
    if (/\/api\/images\/\d+\/file$/.test(new URL(request.url()).pathname)) {
      originals.push(request.url());
    }
  });
  try {
    let firstPreview = true;
    await page.route('**/api/images/*/thumbnail', (route) => {
      if (!firstPreview) return route.continue();
      firstPreview = false;
      return route.fulfill({ status: 422, body: 'Invalid preview fixture' });
    });
    await page.goto(`${webBase}/#/bilder`);
    const tile = page.locator('[data-image-id]').first();
    await tile.waitFor();
    await page.getByText('Vorschau nicht verfügbar', { exact: true }).waitFor();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-image-id] img'))
      .some((image) => image.naturalWidth > 0 && image.src.includes('/thumbnail')));
    assert.deepEqual(originals, [], 'The library fetched full-size photos before opening a viewer');
    await tile.click();
    await page.locator('img.gallery-image').waitFor();
    await page.waitForFunction(() => document.querySelector('img.gallery-image').naturalWidth > 0);
    assert.ok(originals.length > 0, 'The fullscreen viewer must still request the original');
    const firstOriginal = await page.locator('img.gallery-image').getAttribute('src');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction((src) => {
      const current = document.querySelector('img.gallery-image')?.getAttribute('src');
      return Boolean(current) && current !== src;
    }, firstOriginal);
    await page.keyboard.press('Escape');
    await page.getByRole('dialog', { name: 'Bildansicht' }).waitFor({ state: 'hidden' });
    console.log('PASS photo previews: lazy thumbnails in the grid and originals in the viewer');
  } finally {
    await page.close();
  }
}

async function mapFlow(browser, errors) {
  const page = await newPage(browser, 1440, 1000, errors);
  const waitForCount = (count) => page.waitForFunction(
    (expected) => document.querySelector('.gps-page__results-head > span')?.textContent === `${expected} Fotos`, count,
  );
  try {
    await page.goto(`${webBase}/#/weltkarte`);
    await waitForCount(85);
    const markers = page.locator('.gps-map__marker');
    await markers.first().waitFor();
    assert.equal(await page.locator('.gps-page__photo').count(), 36);

    const selectArea = page.getByRole('button', { name: 'Bereich wählen', exact: true });
    await selectArea.press('Enter');
    await page.locator('.gps-map__svg').press('Escape');
    await selectArea.waitFor();
    await selectArea.press('Enter');
    await page.getByRole('button', { name: 'Sichtbaren Bereich wählen' }).press('Enter');
    await waitForCount(85);
    await page.getByRole('button', { name: 'Zu den Fotos' }).press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'gps-map-results');
    await page.getByRole('button', { name: 'Auswahl löschen', exact: true }).click();

    const mapRequest = page.waitForRequest((request) => request.url().includes('/api/map/points?') && request.url().includes('from_date=2025'));
    await page.route('**/api/map/points?**', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 600));
      await route.continue().catch(() => {});
    });
    await page.getByLabel('Von', { exact: true }).fill('2025-01-01');
    await mapRequest;
    assert.equal(await markers.count(), 0, 'Markers from the previous filter stayed active during loading');
    await page.getByLabel('Bis', { exact: true }).fill('2024-12-31');
    await page.getByRole('alert').filter({ hasText: 'Enddatum' }).waitFor();
    assert.equal(await page.getByText('Karte wird aktualisiert …').count(), 0, 'Invalid dates left the map in a loading state');
    assert.equal(await markers.count(), 0);
    await page.unroute('**/api/map/points?**');
    await page.getByRole('button', { name: 'Zurücksetzen', exact: true }).click();
    await waitForCount(85);
    await markers.first().waitFor();
    await page.getByRole('button', { name: 'Weitere Fotos laden', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.gps-page__photo').length === 72);

    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
    await waitForCount(29);
    await page.getByLabel('Von', { exact: true }).fill('2025-01-01');
    await page.getByLabel('Bis', { exact: true }).fill('2025-12-31');
    await waitForCount(14);
    await page.getByLabel('Bis', { exact: true }).fill('2024-12-31');
    await page.getByRole('alert').filter({ hasText: 'Enddatum' }).waitFor();
    assert.equal(await page.locator('.gps-page__photo').count(), 0);
    await page.getByRole('button', { name: 'Zurücksetzen', exact: true }).click();
    await waitForCount(85);

    await page.getByRole('button', { name: 'Bereich wählen', exact: true }).click();
    const box = await page.locator('.gps-map__svg').boundingBox();
    assert.ok(box);
    await page.mouse.move(box.x + box.width * 170 / 360, box.y + box.height * 25 / 180);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 215 / 360, box.y + box.height * 55 / 180, { steps: 8 });
    await page.mouse.up();
    await waitForCount(29);
    await page.getByRole('button', { name: 'Auswahl löschen', exact: true }).click();
    await waitForCount(85);

    await markers.first().waitFor();
    const firstMarker = markers.first();
    const markerLabel = await firstMarker.getAttribute('aria-label');
    const markerCount = Number(markerLabel?.match(/^([\d.]+) Foto/)?.[1].replaceAll('.', ''));
    assert.ok(markerCount > 1, 'Fixture marker needs multiple photos to test the viewer sequence');
    await firstMarker.press('Enter');
    await waitForCount(markerCount);
    const galleryTile = page.locator('.gps-page__photo').first();
    await galleryTile.click();
    const gallery = page.getByRole('dialog', { name: 'Bildansicht' });
    await gallery.waitFor();
    await assertFocusInDialog(page, gallery);
    await assertTabTrap(page, gallery);
    const resizedQuery = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname === '/api/map/points' && url.searchParams.has('grid_columns');
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await resizedQuery;
    await assertFocusInDialog(page, gallery);
    await waitForCount(markerCount);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: 'Nächstes Bild', exact: true }).click();
    await page.keyboard.press('Escape');
    await gallery.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.activeElement?.classList.contains('gps-page__photo'));

    await page.route('**/api/images/*/detail', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await route.continue().catch(() => {});
    });
    for (const closeWithEscape of [false, true]) {
      await galleryTile.click();
      const loadingDialog = page.getByRole('dialog', { name: 'Foto öffnen' });
      await loadingDialog.waitFor();
      if (closeWithEscape) await page.keyboard.press('Escape');
      else await loadingDialog.getByRole('button', { name: 'Abbrechen' }).click();
      await loadingDialog.waitFor({ state: 'hidden' });
      await page.waitForFunction(() => document.activeElement?.classList.contains('gps-page__photo'));
    }
    await page.waitForTimeout(1100);
    assert.equal(await page.getByRole('dialog', { name: 'Bildansicht' }).count(), 0, 'Cancelled detail request reopened the gallery');
    await page.unroute('**/api/images/*/detail');

    await page.route('**/api/images/*/detail', (route) => route.fulfill({ status: 503, body: 'Unavailable detail fixture' }));
    await galleryTile.click();
    const failedDetail = page.getByRole('dialog', { name: 'Foto öffnen' });
    await failedDetail.getByRole('alert').waitFor();
    await page.unroute('**/api/images/*/detail');
    await failedDetail.getByRole('button', { name: 'Erneut versuchen' }).click();
    await gallery.waitFor();
    await gallery.getByRole('button', { name: 'Galerie schließen' }).click();

    await page.route('**/api/images/*/file', (route) => route.fulfill({ status: 404, body: 'Missing fixture' }));
    await galleryTile.click();
    await gallery.getByRole('alert').filter({ hasText: 'Die Bilddatei konnte nicht geöffnet werden.' }).waitFor();
    await gallery.getByRole('button', { name: 'Galerie schließen' }).click();
    await page.unroute('**/api/images/*/file');

    await page.getByRole('button', { name: 'Zurücksetzen', exact: true }).click();
    await waitForCount(85);
    await page.route('**/api/map/points?**', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Testfehler Karte"}' }));
    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
    await page.getByRole('alert').filter({ hasText: 'Testfehler Karte' }).waitFor();
    assert.equal(await markers.count(), 0, 'Failed marker request restored markers from another filter');
    await page.unroute('**/api/map/points?**');
    await page.getByRole('button', { name: 'Erneut versuchen', exact: true }).click();
    await waitForCount(29);
    await page.getByRole('alert').waitFor({ state: 'hidden' });

    await page.getByRole('button', { name: 'Zurücksetzen', exact: true }).click();
    await waitForCount(85);
    await page.route('**/api/map/images?**', (route) => {
      if (!new URL(route.request().url()).searchParams.has('before_id')) return route.continue();
      return route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Testfehler weitere Fotos"}' });
    });
    await page.getByRole('button', { name: 'Weitere Fotos laden', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Testfehler weitere Fotos' }).waitFor();
    assert.equal(await page.locator('.gps-page__photo').count(), 36);
    await page.unroute('**/api/map/images?**');
    await page.route('**/api/map/images?**', async (route) => {
      if (new URL(route.request().url()).searchParams.has('before_id')) {
        await new Promise((resolve) => setTimeout(resolve, 800));
      }
      await route.continue().catch(() => {});
    });
    await page.getByRole('button', { name: 'Weitere Fotos erneut laden', exact: true }).click();
    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
    await waitForCount(29);
    await page.waitForTimeout(900);
    assert.equal(await page.locator('.gps-page__photo').count(), 29);
    await page.unroute('**/api/map/images?**');
    await screenshot(page, 'map');
    console.log('PASS map flow: pagination, dates, region, marker, gallery, retry, stale requests');
  } finally {
    await page.close();
  }
}

async function existingFlow(browser, errors) {
  const page = await newPage(browser, 1440, 1000, errors);
  try {
    await page.route('**/api/images?**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Testfehler Bibliothek"}' }));
    await page.goto(`${webBase}/#/bilder`);
    await page.getByRole('alert').waitFor();
    await page.unroute('**/api/images?**');
    // Live library refresh can recover as soon as the route is removed.
    await page.getByRole('alert').getByRole('button').dispatchEvent('click').catch(() => {});
    await page.locator('[data-image-id]').first().waitFor();
    await page.getByRole('button', { name: 'Anna Beispiel', exact: true }).first().click();
    await page.waitForFunction(() => document.querySelectorAll('[data-image-id]').length === 4);

    await page.route('**/api/folders', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Testfehler Ordner"}' }));
    const folderTrigger = page.getByRole('button', { name: 'Ordner', exact: true }).first();
    await folderTrigger.click();
    const folderDialog = page.getByRole('dialog', { name: 'Ordner auswählen' });
    await folderDialog.getByRole('alert').waitFor();
    await page.unroute('**/api/folders');
    await folderDialog.getByRole('button', { name: /versuchen/ }).click();
    await page.locator('.folder-select-button').first().waitFor();
    await page.locator('.folder-select-button').first().click();
    assert.equal(await page.locator('.folder-select-button').first().getAttribute('aria-pressed'), 'true');
    const folderSearch = folderDialog.getByPlaceholder('Ordner oder Pfad suchen');
    await folderSearch.fill('Berlin');
    await assertFocusInDialog(page, folderDialog);
    await page.keyboard.press('Escape');
    await folderDialog.waitFor({ state: 'hidden' });
    assert.ok(await folderTrigger.evaluate((element) => element === document.activeElement), 'Folder dialog did not restore its trigger');

    const importTrigger = page.getByRole('button', { name: 'Bilder hinzufügen', exact: true });
    await importTrigger.click();
    const importDialog = page.getByRole('dialog', { name: 'Bilderordner hinzufügen' });
    await importDialog.waitFor();
    await page.waitForFunction(() => document.activeElement?.id === 'development-import-path', null, { timeout: 2500 }).catch(async () => {
      const active = await page.evaluate(() => ({ tag: document.activeElement?.tagName, id: document.activeElement?.id, className: document.activeElement?.className }));
      assert.fail(`Import path did not autofocus; active element: ${JSON.stringify(active)}`);
    });
    await assertTabTrap(page, importDialog);
    await page.keyboard.press('Escape');
    await importDialog.waitFor({ state: 'hidden' });
    assert.ok(await importTrigger.evaluate((element) => element === document.activeElement), 'Import dialog did not restore its trigger');
    const changelogTrigger = page.getByTitle('Änderungsprotokoll anzeigen');
    await page.route('**/api/changelog', (route) => route.fulfill({ status: 503, json: {} }));
    await changelogTrigger.click();
    const changelog = page.getByRole('dialog', { name: 'Änderungsprotokoll' });
    await changelog.waitFor();
    await changelog.getByRole('alert').waitFor();
    await assertFocusInDialog(page, changelog);
    await assertTabTrap(page, changelog);
    await page.unroute('**/api/changelog');
    await changelog.getByRole('button', { name: 'Erneut versuchen' }).click();
    await changelog.locator('.whats-new-release').first().waitFor();
    await page.keyboard.press('Escape');
    await changelog.waitFor({ state: 'hidden' });
    assert.ok(await changelogTrigger.evaluate((element) => element === document.activeElement), 'Changelog did not restore its trigger');

    await page.getByRole('button', { name: /Dateinamen/ }).click();
    await page.getByRole('button', { name: /Seite wählen/ }).waitFor();
    await page.getByRole('button', { name: /Seite wählen/ }).click();
    await page.getByRole('button', { name: '8 Dateinamen aktualisieren', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Auswahl aufheben', exact: true }).click();
    await page.getByRole('button', { name: /Benennungsschema ändern/ }).click();
    await page.waitForURL('**/einstellungen/dateinamen');
    await page.getByRole('button', { name: /Gesichter prüfen/ }).click();
    await page.getByRole('button', { name: /Neue Gesichter.*Benennen/ }).click();
    await page.getByRole('button', { name: /Personen korrigieren.*Bestätigte/ }).click();
    const cards = page.locator('.page-pane--active .cluster-person-browser__cluster-card[role="button"]');
    await cards.first().waitFor();
    const card = (await cards.count()) > 1 ? cards.nth(1) : cards.first();
    await card.focus();
    await page.keyboard.press('Enter');
    assert.ok(await card.evaluate((element) => element.classList.contains('neon-card--active')), 'Enter did not select cluster card');
    await page.getByRole('button', { name: 'Person bearbeiten' }).click();
    const personDialog = page.getByRole('dialog', { name: /Anna Beispiel|Ben Beispiel/ });
    await personDialog.waitFor();
    const personInput = personDialog.getByPlaceholder('Personenname');
    await personInput.fill('Neu Beispiel');
    await page.route('**/api/persons/*', (route) => route.request().method() === 'PATCH'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Testfehler Umbenennen"}' })
      : route.continue());
    await personDialog.getByRole('button', { name: 'Person umbenennen' }).click();
    await personDialog.getByRole('alert').filter({ hasText: 'Testfehler Umbenennen' }).waitFor();
    assert.equal(await personInput.inputValue(), 'Neu Beispiel');
    assert.ok(await personDialog.isVisible(), 'Failed rename closed the dialog');
    await page.unroute('**/api/persons/*');
    await page.waitForFunction(() => {
      const dialog = document.getElementById('person-manage-title')?.closest('[role=dialog]');
      return dialog && !dialog.querySelector('button.primary-button')?.disabled;
    });
    await page.keyboard.press('Escape');
    await personDialog.waitFor({ state: 'hidden' });

    await page.getByRole('button', { name: 'Gesichtsgruppe umbenennen' }).click();
    const renameDialog = page.getByRole('dialog', { name: 'Gesichtsgruppe umbenennen' });
    await renameDialog.waitFor();
    await renameDialog.locator('.cluster-assignment-form__input').fill('Smoke Gruppe');
    await page.keyboard.press('Enter');
    await renameDialog.waitFor({ state: 'hidden' });
    await page.getByText('Smoke Gruppe', { exact: true }).first().waitFor();

    await page.getByRole('button', { name: 'Person bearbeiten' }).click();
    await personDialog.waitFor();
    await personInput.fill('Neu Beispiel');
    await page.route('**/api/persons', (route) => route.abort('failed'));
    await personInput.press('Enter');
    await personDialog.waitFor({ state: 'hidden' });
    await page.getByText('Neu Beispiel', { exact: true }).first().waitFor();
    await page.unroute('**/api/persons');

    await page.getByRole('button', { name: /Aussortiert.*Unbekannt/ }).click();
    await screenshot(page, 'existing-flows');
    console.log('PASS existing flows: library/folder retry, person filter, import, rename, settings, review tabs');
  } finally {
    await page.close();
  }
}

async function liveWorkflowFlow(browser, errors) {
  const page = await newPage(browser, 1440, 1000, errors);
  const publishLibraryChange = (reason) => page.evaluate((reason) => {
    window.workflowTestEvents.dispatchEvent(new MessageEvent('clusters', {
      data: JSON.stringify({ reason }),
    }));
  }, reason);
  try {
    await page.addInitScript(() => {
      window.EventSource = class TestEventSource extends EventTarget {
        constructor() {
          super();
          window.workflowTestEvents = this;
          queueMicrotask(() => this.onopen?.(new Event('open')));
        }
        close() {}
      };
    });
    await page.goto(`${webBase}/#/bilder`);
    const firstPhoto = page.locator('.image-gallery-card').first();
    await firstPhoto.click();
    const gallery = page.getByRole('dialog', { name: 'Bildansicht' });
    const galleryTitle = gallery.locator('.gallery-title strong');
    await galleryTitle.waitFor();
    const originalTitle = await galleryTitle.textContent();
    await page.route('**/api/images?**', async (route) => {
      const query = new URL(route.request().url()).searchParams;
      if (query.get('offset') !== '0' || query.get('limit') === '1') return route.continue();
      const response = await route.fetch();
      const data = await response.json();
      data.items = [
        { ...data.items[0], id: 987654321, filename: 'Live neu importiert.jpg' },
        ...data.items.slice(0, -1),
      ];
      await route.fulfill({ response, json: data });
    });
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    const syntheticPhoto = page.locator('[data-image-id="987654321"]');
    await syntheticPhoto.waitFor();
    assert.equal(await galleryTitle.textContent(), originalTitle, 'Live refresh changed the active gallery photo');
    await page.unroute('**/api/images?**');
    await publishLibraryChange('database_import');
    await gallery.waitFor({ state: 'hidden' });
    await syntheticPhoto.waitFor({ state: 'detached' });
    await firstPhoto.click();
    await gallery.waitFor();

    await page.goto(`${webBase}/#/weltkarte`);
    await page.getByRole('status').filter({ hasText: /^85 Fotos$/ }).waitFor();
    await page.getByRole('button', { name: 'Weitere Fotos laden', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.gps-page__photo').length === 72);
    await page.locator('.gps-page__photo').nth(36).click();
    await gallery.waitFor();
    await page.route('**/api/map/images?**', async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), total: 84 } });
    });
    await publishLibraryChange('delete_image');
    await page.getByRole('status').filter({ hasText: /^84 Fotos$/ }).waitFor();
    assert.equal(await page.locator('.gps-page__photo').count(), 36, 'Old pages survived a library change');
    assert.ok(await gallery.isVisible(), 'Library refresh closed the open map gallery');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.activeElement?.id === 'gps-map-results');
    await page.unroute('**/api/map/images?**');
    await page.locator('.gps-page__photo').first().click();
    await gallery.waitFor();
    await publishLibraryChange('database_import');
    await gallery.waitFor({ state: 'hidden' });
    await page.goto(`${webBase}/#/bilder`);
    await firstPhoto.waitFor();
    assert.equal(await gallery.count(), 0, 'A hidden library gallery reopened after restore');

    const reviewClusters = [
      { cluster_id: 1, cluster_label: 'Benannte Gruppe', person_name: 'Anna', face_count: 4 },
      { cluster_id: 3, cluster_label: 'Offene Gruppe', person_name: null, face_count: 4 },
    ];
    await page.route('**/api/clusters-overview', (route) => route.fulfill({ json: {
      clusters: reviewClusters, review_groups: [], first_cluster: null,
    } }));
    await page.route('**/api/clusters', (route) => route.fulfill({ json: reviewClusters }));
    await page.goto(`${webBase}/#/gesichter-pruefen/gruppe/987654321`);
    await page.locator('.cluster-detail-topbar__entity--secondary').getByText('Offene Gruppe', { exact: true }).waitFor();
    const groupRequest = page.waitForRequest('**/api/face-review-groups/unknown_person/faces');
    await page.getByRole('button', { name: /Aussortiert.*Unbekannt/ }).click();
    await groupRequest;

    let total = 26;
    const candidates = Array.from({ length: 26 }, (_, index) => ({
      image_id: index + 1, location_id: index + 1, path: `/fixture/foto-${index + 1}.jpg`,
      current_filename: `foto-${index + 1}.jpg`, proposed_filename: `foto-${index + 1} Anna.jpg`,
      detected_person_names: ['Anna'], current_suffix_person_names: [],
    }));
    await page.route('**/api/image-renames/count?**', (route) => route.fulfill({ json: { total } }));
    await page.route('**/api/image-renames?**', (route) => {
      const query = new URL(route.request().url()).searchParams;
      const offset = Number(query.get('offset'));
      const limit = Number(query.get('limit'));
      return route.fulfill({ json: {
        items: candidates.slice(0, total).slice(offset, offset + limit),
        offset, limit, total: null, has_more: offset + limit < total, available_persons: ['Anna'],
      } });
    });
    await page.route('**/api/image-renames/apply', (route) => {
      total = 25;
      return route.fulfill({ json: { renamed_count: 1, skipped_count: 0, error_count: 0 } });
    });
    await page.goto(`${webBase}/#/dateinamen`);
    const pagination = page.locator('.rename-pagination--top');
    await pagination.getByText('Seite 1 von 2').waitFor();
    await pagination.getByRole('button', { name: 'Weiter' }).click();
    await page.getByRole('button', { name: 'Seite wählen (1)' }).click();
    await page.getByRole('button', { name: '1 Dateinamen aktualisieren', exact: true }).click();
    await pagination.getByText('Seite 1 von 1').waitFor();
    assert.equal(await page.locator('.rename-row').count(), 25);
    console.log('PASS live workflows: stable gallery, map refresh, missing review link, rename page correction');
  } finally {
    await page.close();
  }
}

async function settingsFlow(browser, errors) {
  const page = await newPage(browser, 1440, 1000, errors);
  try {
    await page.route('**/api/settings', (route) => route.fulfill({ status: 503 }));
    await page.goto(`${webBase}/#/einstellungen/darstellung`);
    await page.getByRole('alert').waitFor();
    await page.unroute('**/api/settings');
    await page.getByRole('button', { name: 'Erneut versuchen' }).click();
    const themes = page.getByRole('radiogroup', { name: 'Darstellung' });
    await themes.waitFor();
    const theme = (label) => themes.getByRole('radio').filter({ has: page.getByText(label, { exact: true }) });
    await theme('Hell').click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await theme('Dunkel').click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await theme('System').click();
    assert.equal(await theme('System').getAttribute('aria-checked'), 'true');

    await page.goto(`${webBase}/#/einstellungen/dateinamen`);
    const start = page.locator('.settings-format-presets[aria-label="Beginn des Personen-Anhangs"]');
    const join = page.locator('.settings-format-presets[aria-label="Verbindung zwischen Personennamen"]');
    await start.getByRole('button', { name: /Unterstrich/ }).click();
    await join.getByRole('button', { name: /Und/ }).click();
    const preview = page.locator('.filename-format-result > code');
    assert.equal(await preview.textContent(), 'Sommerurlaub_Kai und Regina.jpg');
    await page.getByRole('button', { name: 'Dateinamen-Regeln speichern' }).click();
    await page.getByText('Einstellungen für Personennamen im Dateinamen gespeichert.', { exact: true }).waitFor();
    const response = await page.request.get(`${apiBase}/settings`);
    assert.equal(response.status(), 200);
    const saved = await response.json();
    assert.equal(saved.filename_person_block_separator, '_');
    assert.equal(saved.filename_person_joiner, ' und ');
    await screenshot(page, 'settings');
    console.log('PASS settings: light/dark/system selection and filename preview/save');
  } finally {
    await page.close();
  }
}

async function assertMarkerTouchSize(page, context) {
  const hit = page.locator('.gps-map__marker-hit').first();
  await hit.waitFor();
  // The ResizeObserver updates SVG marker scale after a viewport change.
  await page.waitForFunction(() => {
    const hit = document.querySelector('.gps-map__marker-hit')?.getBoundingClientRect();
    const core = document.querySelector('.gps-map__marker-core')?.getBoundingClientRect();
    return hit && core && hit.width >= 43.9 && hit.height >= 43.9
      && core.width >= 23.9 && core.height >= 23.9;
  }, null, { timeout: 2500 }).catch(() => {});
  const hitBox = await hit.boundingBox();
  const coreBox = await page.locator('.gps-map__marker-core').first().boundingBox();
  assert.ok(hitBox && hitBox.width >= 43.9 && hitBox.height >= 43.9,
    `${context}: marker touch target is ${hitBox?.width.toFixed(1)}×${hitBox?.height.toFixed(1)}px (minimum 44×44px)`);
  assert.ok(coreBox && coreBox.width >= 23.9 && coreBox.height >= 23.9,
    `${context}: visible marker is ${coreBox?.width.toFixed(1)}×${coreBox?.height.toFixed(1)}px (minimum 24×24px)`);
  const overlaps = await page.locator('.gps-map__marker-hit').evaluateAll((hits) => {
    const boxes = hits.map((hit) => hit.getBoundingClientRect());
    return boxes.some((box, index) => boxes.slice(index + 1).some((other) =>
      box.left < other.right - 0.1 && other.left < box.right - 0.1 &&
      box.top < other.bottom - 0.1 && other.top < box.bottom - 0.1,
    ));
  });
  assert.equal(overlaps, false, `${context}: adjacent clusters have overlapping touch targets`);
}

async function responsiveFlow(browser, errors) {
  const page = await newPage(browser, 390, 844, errors);
  try {
    for (const route of ['weltkarte', 'bilder', 'dateinamen', 'gesichter-pruefen', 'einstellungen']) {
      await page.goto(`${webBase}/#/${route}`);
      await page.locator('.page-pane--active').waitFor();
      if (route === 'weltkarte') {
        await assertMarkerTouchSize(page, '390px world view');
        await page.getByRole('button', { name: 'Vergrößern' }).click();
        await assertMarkerTouchSize(page, '390px zoomed view');
        await page.setViewportSize({ width: 320, height: 844 });
        await assertMarkerTouchSize(page, '320px zoomed view');
        await page.setViewportSize({ width: 390, height: 844 });
      }
      await page.waitForTimeout(400);
      const width = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: window.innerWidth }));
      assert.ok(width.page <= width.viewport + 1, `${route}: ${width.page}px document overflows ${width.viewport}px viewport`);
      await screenshot(page, `mobile-${route}`);
    }
    console.log('PASS responsive: five pages fit 390px viewport');
  } finally {
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    await previewFlow(browser, errors);
    await mapFlow(browser, errors);
    await liveWorkflowFlow(browser, errors);
    await existingFlow(browser, errors);
    await settingsFlow(browser, errors);
    await responsiveFlow(browser, errors);
    assert.deepEqual(errors, [], `Browser page errors: ${errors.join('; ')}`);
    console.log('PASS browser smoke: no uncaught page errors');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
