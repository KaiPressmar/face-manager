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

async function newPage(browser, width, height, errors, options = {}) {
  const page = await browser.newPage({ viewport: { width, height }, ...options });
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
    let firstOriginalUnavailable = true;
    await page.route('**/api/images/*/thumbnail', (route) => {
      if (!firstPreview) return route.continue();
      firstPreview = false;
      return route.fulfill({ status: 422, body: 'Invalid preview fixture' });
    });
    await page.route(/\/api\/images\/\d+\/file(?:\?.*)?$/, (route) => {
      if (!firstOriginalUnavailable) return route.continue();
      firstOriginalUnavailable = false;
      return route.fulfill({ status: 404, body: 'Original is offline' });
    });
    await page.goto(`${webBase}/#/bilder`);
    const tile = page.locator('[data-image-id]').first();
    await tile.waitFor();
    await page.getByText('Vorschau nicht verfügbar', { exact: true }).waitFor();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-image-id] img'))
      .some((image) => image.naturalWidth > 0 && image.src.includes('/thumbnail')));
    const failedTile = page.locator('[data-image-id]').filter({ hasText: 'Vorschau nicht verfügbar' });
    await failedTile.getByRole('button', { name: /Vorschau von .* erneut laden/ }).click();
    await page.locator('[data-image-id] img[src*="retry="]').waitFor();
    await page.getByText('Vorschau nicht verfügbar', { exact: true }).waitFor({ state: 'hidden' });
    assert.deepEqual(originals, [], 'The library fetched full-size photos before opening a viewer');
    const opener = tile.locator('.image-gallery-card__open');
    await opener.click();
    const dialog = page.getByRole('dialog', { name: 'Bildansicht' });
    await dialog.getByRole('alert').filter({ hasText: 'Die lokale Vorschau wird angezeigt.' }).waitFor();
    await dialog.locator('img.gallery-image--preview').waitFor();
    await dialog.getByRole('button', { name: 'Original erneut laden' }).click();
    await page.waitForFunction(() => {
      const image = document.querySelector('img.gallery-image[src*="/file?retry="]');
      return image?.naturalWidth > 0 && image.getClientRects().length > 0;
    });
    await page.locator('img.gallery-image').waitFor();
    await page.waitForFunction(() => document.querySelector('img.gallery-image').naturalWidth > 0);
    assert.equal(originals.length, 2, 'Opening and retrying should request only the active original');
    assert.equal(new URL(originals[0]).pathname, new URL(originals[1]).pathname, 'Retry changed the active photo');
    const firstOriginal = await page.locator('img.gallery-image').getAttribute('src');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction((src) => {
      const current = document.querySelector('img.gallery-image')?.getAttribute('src');
      return Boolean(current) && current !== src;
    }, firstOriginal);
    await page.keyboard.press('Escape');
    await page.getByRole('dialog', { name: 'Bildansicht' }).waitFor({ state: 'hidden' });
    await opener.and(page.locator(':focus')).waitFor();
    await opener.press('Enter');
    await dialog.waitFor();
    const touchPage = await newPage(browser, 390, 844, errors, { hasTouch: true, isMobile: true });
    try {
      await touchPage.goto(`${webBase}/#/bilder`);
      await touchPage.locator('.image-gallery-card__open').first().tap();
      await touchPage.getByRole('dialog', { name: 'Bildansicht' }).waitFor();
    } finally {
      await touchPage.close();
    }
    console.log('PASS photos: pointer/touch/keyboard opening, preview retry, offline preview, original retry and focus return');
  } finally {
    await page.close();
  }
}

async function firstAssignmentFlow(browser, errors) {
  const page = await newPage(browser, 900, 680, errors);
  const reviewHash = /#\/gesichter-pruefen\/ohne-namen$/;
  try {
    await page.route('**/api/images?**', async (route) => {
      const response = await route.fetch();
      const images = await response.json();
      await route.fulfill({ response, json: { ...images, available_persons: ['Unbekannt'] } });
    });
    await page.goto(`${webBase}/#/bilder`);
    const assign = page.getByRole('button', { name: 'Personen zuordnen', exact: true });
    const openTab = page.getByRole('button', { name: /^Ohne Namen/ }).and(page.locator('[aria-current="true"]'));
    await assign.click();
    await page.waitForURL(reviewHash);
    await openTab.waitFor();
    await page.locator('.cluster-detail-topbar').waitFor();
    await page.reload();
    await openTab.waitFor();
    await page.getByRole('button', { name: /^Personen korrigieren/ }).click();
    await page.goBack();
    await assign.click();
    await page.waitForURL(reviewHash);
    await openTab.waitFor();
    console.log('PASS first assignment: local route, refresh, back and already-mounted review page');
  } finally {
    await page.close();
  }
}

async function localRecoveryFlow(browser, errors) {
  const page = await newPage(browser, 390, 844, errors);
  const firstBackup = gate();
  const firstEnqueue = gate();
  let restores = 0;
  const retryPaths = [];
  try {
    await page.addInitScript(() => {
      window.EventSource = class TestEventSource extends EventTarget {
        constructor() {
          super();
          window.recoveryTestEvents = this;
          queueMicrotask(() => this.onopen?.(new Event('open')));
        }
        close() {}
      };
    });
    await page.route('**/api/database/import', async (route) => {
      restores += 1;
      if (restores === 1) {
        await firstBackup.ready;
        return route.fulfill({ status: 400, json: { detail: 'Die Testdatei ist keine gültige Sicherung.' } });
      }
      return route.fulfill({ status: 204 });
    });
    await page.route('**/api/imports', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      retryPaths.push(route.request().postDataJSON().folder_path);
      if (retryPaths.length === 1) {
        await firstEnqueue.ready;
        return route.fulfill({ status: 400, json: { detail: 'Der Testordner ist noch nicht erreichbar.' } });
      }
      return route.fulfill({ status: 202, json: { id: 'retry-job', status: 'queued' } });
    });
    await page.goto(`${webBase}/#/einstellungen/daten`);
    const backupCard = page.locator('.settings-card').filter({ has: page.getByRole('heading', { name: 'Daten sichern und wiederherstellen' }) });
    const fileField = backupCard.locator('input[type="file"]');
    const upload = (body) => fileField.setInputFiles({ name: 'demo.sqlite', mimeType: 'application/octet-stream', buffer: Buffer.from(body) });
    await upload('invalid');
    await backupCard.getByRole('status').filter({ hasText: 'Die Sicherung wird geprüft und wiederhergestellt' }).waitFor();
    assert.ok(await backupCard.getByRole('button', { name: 'Sicherung erstellen' }).isDisabled());
    firstBackup.release();
    await backupCard.getByRole('alert').filter({ hasText: 'Die Testdatei ist keine gültige Sicherung.' }).waitFor();
    assert.ok(await backupCard.getByRole('button', { name: 'Sicherung wiederherstellen' }).isEnabled());
    await upload('valid fixture');
    await backupCard.getByRole('status').filter({ hasText: 'Die Sicherung wurde wiederhergestellt.' }).waitFor();
    assert.equal(restores, 2);

    await page.evaluate(() => {
      window.recoveryTestEvents.dispatchEvent(new MessageEvent('imports', { data: JSON.stringify({
        jobs: [{ id: 'cancelled-job', folder_path: '/fixtures/cancelled', status: 'cancelled', processed_images: 4,
          total_images: 8, finished_at: '2026-01-21T12:00:00Z', last_error: '/fixtures/cancelled/kaputt.jpg: cannot identify image file' }],
        running_count: 0, active_job_ids: [],
      }) }));
    });
    await page.locator('.activity-center__trigger').click();
    const historyJob = page.locator('article.activity-task').filter({ hasText: 'Abgebrochen' });
    await historyJob.getByText('„kaputt.jpg“ konnte nicht als Bild gelesen werden und wurde übersprungen.').waitFor();
    const retry = historyJob.getByRole('button', { name: 'Ordner erneut prüfen' });
    await retry.evaluate((button) => { button.click(); button.click(); });
    firstEnqueue.release();
    await page.getByRole('alert').filter({ hasText: 'Der Testordner ist noch nicht erreichbar.' }).waitFor();
    assert.deepEqual(retryPaths, ['/fixtures/cancelled']);
    await retry.click();
    await page.getByRole('status').filter({ hasText: 'Ordner erneut zur Prüfung eingereiht.' }).waitFor();
    assert.deepEqual(retryPaths, ['/fixtures/cancelled', '/fixtures/cancelled']);
    await historyJob.getByText('Abgebrochen', { exact: true }).waitFor();
    await retry.waitFor({ state: 'hidden' });
    console.log('PASS recovery feedback: backup pending/error/retry, cancelled job retry with double-click protection');
  } finally {
    firstBackup.release();
    firstEnqueue.release();
    await page.close();
  }
}

async function collectSourcesFlow(browser, errors) {
  const page = await newPage(browser, 390, 844, errors);
  const queuedPaths = [];
  try {
    await page.route('**/api/images?**', (route) => {
      const query = new URL(route.request().url()).searchParams;
      return route.fulfill({ json: {
        items: [], total: 0, offset: Number(query.get('offset')), limit: Number(query.get('limit')),
        has_more: false, available_persons: [],
      } });
    });
    await page.route('**/api/imports', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const path = route.request().postDataJSON().folder_path;
      queuedPaths.push(path);
      if (path === '/fixtures/usb' && queuedPaths.length === 2) {
        return route.fulfill({ status: 422, json: { detail: 'Das Testlaufwerk fehlt.' } });
      }
      return route.fulfill({ status: 202, json: { id: path, status: 'queued' } });
    });
    await page.goto(`${webBase}/#/bilder`);
    await page.getByRole('heading', { name: 'Deine Fotos an einem Ort' }).waitFor();
    await page.getByRole('button', { name: 'Bilderordner auswählen' }).click();
    const dialog = page.getByRole('dialog', { name: 'Bilderordner hinzufügen' });
    const pathInput = dialog.getByRole('textbox', { name: 'Pfad zum Bilderordner' });
    await pathInput.fill('/fixtures/fotos');
    await pathInput.press('Enter');
    await pathInput.fill('/fixtures/usb');
    await pathInput.press('Enter');
    await dialog.getByRole('button', { name: '2 Ordner importieren' }).click();
    await dialog.getByText('Das Testlaufwerk fehlt.').waitFor();
    assert.equal(await dialog.getByText('/fixtures/fotos', { exact: true }).count(), 0);
    await dialog.getByRole('button', { name: 'Ausstehende Ordner erneut versuchen' }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(queuedPaths, ['/fixtures/fotos', '/fixtures/usb', '/fixtures/usb']);
    console.log('PASS collecting photos: onboarding, two sources, partial import retry');
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
    await page.getByRole('navigation', { name: 'Hauptnavigation' }).getByRole('button', { name: 'Personen' }).click();
    await page.getByRole('button', { name: /Ohne Namen.*Benennen/ }).click();
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
  // Keep the initial gallery test separate from speculative grid pagination.
  const page = await newPage(browser, 1100, 650, errors);
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
    const firstPhoto = page.locator('.image-gallery-card__open').first();
    await firstPhoto.press('Enter');
    const gallery = page.getByRole('dialog', { name: 'Bildansicht' });
    const galleryTitle = gallery.locator('.gallery-title strong');
    await galleryTitle.waitFor();
    const originalTitle = await galleryTitle.textContent();
    await page.bringToFront();
    await page.waitForFunction(() => document.visibilityState === 'visible' && document.hasFocus());
    const isVisiblePageRefresh = (url) => {
      const request = new URL(url);
      return request.pathname === '/api/images' && request.searchParams.get('offset') === '0'
        && request.searchParams.get('limit') !== '1';
    };
    await page.route('**/api/images?**', async (route) => {
      if (!isVisiblePageRefresh(route.request().url())) return route.continue();
      const response = await route.fetch();
      const data = await response.json();
      data.items = [
        { ...data.items[0], id: 987654321, filename: 'Live neu importiert.jpg' },
        ...data.items.slice(0, -1),
      ];
      await route.fulfill({ json: data });
    });
    await publishLibraryChange('import_completed');
    const syntheticPhoto = page.locator('[data-image-id="987654321"]');
    await syntheticPhoto.waitFor();
    assert.equal(await galleryTitle.textContent(), originalTitle, 'Live refresh changed the active gallery photo');
    await page.unroute('**/api/images?**');
    await publishLibraryChange('database_import');
    await gallery.waitFor({ state: 'hidden' });
    await syntheticPhoto.waitFor({ state: 'detached' });
    await firstPhoto.press('Enter');
    await gallery.waitFor();

    await page.setViewportSize({ width: 1440, height: 1000 });
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
    const confirmRename = page.getByRole('dialog', { name: 'Dateinamen prüfen' });
    await confirmRename.getByRole('button', { name: 'Dateien jetzt umbenennen' }).click();
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

async function previewCacheSettingsFlow(browser, errors) {
  const page = await newPage(browser, 320, 765, errors, { hasTouch: true, isMobile: true });
  const firstWrite = gate();
  const firstSave = gate();
  const baseSettings = await (await page.request.get(`${apiBase}/settings`)).json();
  const settings = {
    ...baseSettings,
    preview_cache_limit_mib: 768,
    preview_cache_limit_mib_default: 768,
    preview_cache_limit_error: false,
  };
  const writes = [];
  try {
    await page.route('**/api/settings', async (route) => {
      if (route.request().method() === 'PUT') {
        const payload = route.request().postDataJSON();
        writes.push(payload);
        if (writes.length === 1) {
          firstWrite.release();
          await firstSave.ready;
        }
        settings.preview_cache_limit_mib = payload.preview_cache_limit_mib;
        settings.preview_cache_limit_error = false;
      }
      await route.fulfill({ json: settings });
    });
    await page.goto(`${webBase}/#/einstellungen/daten`);
    const card = page.getByRole('region', { name: 'Vorschau-Cache' });
    const choice = card.getByRole('combobox', { name: 'Cache-Größe' });
    const save = card.getByRole('button', { name: 'Cache-Einstellung speichern' });
    await choice.waitFor();
    assert.equal(await choice.inputValue(), '768');
    assert.deepEqual(await choice.locator('option').allTextContents(),
      ['Standard (768 MiB)', '2 GiB', '8 GiB', '32 GiB', 'Eigene Größe', 'Ohne Grenze']);
    assert.ok(await save.isDisabled());
    await choice.press('End');
    assert.equal(await choice.inputValue(), 'unlimited');
    await card.getByText(/Cache kann auf diesem Gerät weiter wachsen/).waitFor();
    assert.deepEqual(writes, []);
    await save.press('Enter');
    await card.getByRole('status').filter({ hasText: /wird gespeichert/ }).waitFor();
    await firstWrite.ready;
    assert.ok(await save.isDisabled());
    assert.ok(await choice.isDisabled());
    await save.evaluate((button) => button.form.requestSubmit());
    assert.equal(writes.length, 1);
    firstSave.release();
    await card.getByRole('status').filter({ hasText: /wurde gespeichert/ }).waitFor();
    assert.deepEqual(writes, [{ preview_cache_limit_mib: null }]);

    await page.reload();
    await choice.waitFor();
    assert.equal(await choice.inputValue(), 'unlimited');
    await choice.selectOption('custom');
    await card.getByText(/nach 90 Tagen.*auch wenn noch Platz ist/).waitFor();
    const size = card.getByRole('spinbutton', { name: 'Eigene Größe in MiB' });
    for (const invalid of ['127', '128.5', '1048577', '']) {
      await size.fill(invalid);
      assert.equal(await size.getAttribute('aria-invalid'), 'true');
      assert.ok(await save.isDisabled());
    }
    assert.equal(writes.length, 1);
    await size.fill('1048576');
    assert.equal(await size.getAttribute('aria-invalid'), 'false');
    await size.fill('128');
    await save.tap();
    await card.getByRole('status').filter({ hasText: /wurde gespeichert/ }).waitFor();
    assert.deepEqual(writes[1], { preview_cache_limit_mib: 128 });
    await page.reload();
    await choice.waitFor();
    assert.equal(await choice.inputValue(), 'custom');
    assert.equal(await size.inputValue(), '128');
    assert.ok(await card.evaluate((element) => {
      const { right, left } = element.getBoundingClientRect();
      return element.scrollWidth <= element.clientWidth + 1 && left >= 0 && right <= innerWidth + 1;
    }));

    settings.preview_cache_limit_mib = null;
    settings.preview_cache_limit_error = true;
    await page.reload();
    await card.getByRole('alert').filter({ hasText: /automatische Bereinigung ist pausiert/ }).waitFor();
    assert.equal(await choice.inputValue(), 'unlimited');
    await save.tap();
    await card.getByRole('status').filter({ hasText: /wurde gespeichert/ }).waitFor();
    assert.deepEqual(writes[2], { preview_cache_limit_mib: null });
    assert.equal(await card.getByRole('alert').count(), 0);
    assert.ok(await save.isDisabled());
    console.log('PASS preview cache: explicit save, no overlap, keyboard, 320px, custom limits, reload and repair');
  } finally {
    firstSave.release();
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
    await page.setViewportSize({ width: 320, height: 568 });
    await page.goto(`${webBase}/#/bilder`);
    await page.locator('[data-image-id]').first().waitFor();
    await page.getByText('85', { exact: true }).first().waitFor();
    const nav = page.getByRole('navigation', { name: 'Hauptnavigation' });
    assert.ok(await nav.evaluate((element) => element.scrollWidth <= element.clientWidth));
    await page.locator('.topbar-import-button__label').waitFor();
    await nav.getByRole('button', { name: 'Personen' }).click();
    await page.getByRole('heading', { name: 'Gesichter prüfen', exact: true }).waitFor();
    const more = nav.getByRole('button', { name: /^Mehr/ });
    await more.press('Enter');
    await nav.getByRole('button', { name: 'Dateinamen' }).waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await more.getAttribute('aria-expanded'), 'false');
    await more.press('Enter');
    await nav.getByRole('button', { name: 'Dateinamen' }).click();
    await page.getByRole('heading', { name: 'Dateinamen', exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });

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

async function assertReviewFits(page, regions, context) {
  const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  const { width } = page.viewportSize();
  assert.ok(documentWidth <= width + 1, `${context}: ${documentWidth}px document overflows ${width}px viewport`);
  for (const region of regions) {
    const { content, inner, left, right } = await region.evaluate((element) => {
      const { left, right } = element.getBoundingClientRect();
      return { content: element.scrollWidth, inner: element.clientWidth, left, right };
    });
    assert.ok(content <= inner + 1 && left >= -1 && right <= width + 1,
      `${context}: region has ${content}px content, ${inner}px inner width, bounds ${left}–${right}px`);
  }
}

function gate() {
  let release;
  const ready = new Promise((resolve) => { release = resolve; });
  return { ready, release };
}

async function reviewInboxFlow(browser, errors) {
  const page = await newPage(browser, 390, 844, errors);
  const initialListing = gate();
  const previewCrop = gate();
  const detailCrops = gate();
  const proposal = {
    cluster_id: 3, person_id: 1, person_name: 'Anna Beispiel',
    confidence: 0.91, best_distance: 0.15, runner_up_margin: 0.35,
    support_count: 5, face_count: 6, support_ratio: 5 / 6, recommended: true,
    proposal_token: 'first-snapshot', face_ids: [601, 602, 603, 604, 605],
    preview_face_ids: [601, 602], reference_face_ids: [701, 702],
  };
  const updated = {
    ...proposal, proposal_token: 'current-snapshot',
    face_ids: [601, 602, 603, 604, 606], preview_face_ids: [601, 606],
  };
  const acceptRequests = [];
  let listing = [proposal];
  try {
    await page.route('**/api/person-suggestions', async (route) => {
      await initialListing.ready;
      await route.fulfill({ json: listing });
    });
    await page.route('**/api/review-suggestions', (route) => route.fulfill({ json: [] }));
    await page.route(/\/api\/faces\/(?:60[1-6]|70[12])\/crop\?/, async (route) => {
      if (/\/601\//.test(route.request().url())) await previewCrop.ready;
      if (/\/60[3-5]\//.test(route.request().url())) await detailCrops.ready;
      await route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#456"/></svg>',
      });
    });
    await page.route('**/api/person-suggestions/accept', async (route) => {
      const request = route.request().postDataJSON();
      acceptRequests.push(request);
      if (request.assignments?.[0]?.proposal_token !== updated.proposal_token) {
        listing = [updated];
        await route.fulfill({ status: 409, json: {
          detail: 'Der Vorschlag hat sich geändert. Lade ihn neu und prüfe die Auswahl erneut.',
        } });
        return;
      }
      listing = [];
      await route.fulfill({ json: { status: 'ok', accepted_count: 1 } });
    });

    await page.goto(`${webBase}/#/gesichter-pruefen`);
    const inbox = page.locator('.review-inbox');
    await inbox.getByRole('status').filter({ hasText: /Vorschläge werden geladen/ }).waitFor();
    assert.equal(await inbox.getByRole('article').count(), 0);
    assert.equal(await inbox.getByRole('button', { name: /bestätigen|zuordnen/i }).count(), 0);
    initialListing.release();

    const card = inbox.getByRole('article', { name: 'Personenvorschlag für Anna Beispiel' });
    const open = card.getByRole('button', { name: 'Gruppe prüfen', exact: true });
    await open.waitFor();
    assert.ok(await card.getByRole('button', { name: 'Auswahl zuordnen', exact: true }).isDisabled(),
      'Inbox allowed submission while a preview crop was still loading');
    previewCrop.release();
    const viewports = [[320, 568], [390, 844], [768, 1024], [1280, 800]];
    for (const [width, height] of viewports) {
      await page.setViewportSize({ width, height });
      await assertReviewFits(page, [inbox, card], `Review inbox at ${width}×${height}`);
    }

    await open.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Gruppe prüfen', exact: true });
    const apply = dialog.getByRole('button', { name: 'Auswahl zuordnen', exact: true });
    const faceBoxes = dialog.getByRole('checkbox', { name: /^Gesicht \d+ auswählen$/ });
    await faceBoxes.nth(4).waitFor();
    assert.equal(await faceBoxes.count(), proposal.face_ids.length, 'Detail omitted faces from the current proposal snapshot');
    await dialog.getByText('6 in der Gruppe · 5 im Vorschlag', { exact: true }).waitFor();
    assert.ok(await apply.isDisabled(), 'Detail allowed submission while crop images were still loading');
    assert.deepEqual(acceptRequests, []);
    detailCrops.release();
    await apply.and(dialog.locator('button:enabled')).waitFor();

    for (const [width, height] of [...viewports, [640, 400], [390, 360]]) {
      await page.setViewportSize({ width, height });
      await assertReviewFits(page, [dialog], `Review detail at ${width}×${height}`);
    }
    // The entering page briefly transforms the fixed dialog's containing block.
    // Check the actual paint order once the responsive layout has settled.
    await page.waitForFunction(() => {
      const brand = document.querySelector('.sidebar__brand-mark');
      if (!brand) return false;
      const { x, y, width, height } = brand.getBoundingClientRect();
      return Boolean(document.elementFromPoint(x + width / 2, y + height / 2)?.closest('.review-person-detail-backdrop'));
    }, undefined, { timeout: 3000 });
    await apply.scrollIntoViewIfNeeded();
    const bounds = await apply.boundingBox();
    assert.ok(bounds && bounds.y >= -1 && bounds.y + bounds.height <= 361,
      `Detail action is unreachable at keyboard-height viewport: ${JSON.stringify(bounds)}`);
    assert.ok(await apply.evaluate((element) => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return element.contains(document.elementFromPoint(x + width / 2, y + height / 2));
    }), 'Another element covers the detail action at keyboard-height viewport');
    await assertFocusInDialog(page, dialog);
    await assertTabTrap(page, dialog);
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    await open.and(page.locator(':focus')).waitFor();

    await page.setViewportSize({ width: 768, height: 1024 });
    await open.click();
    await selectReviewFaces(dialog, proposal, []);
    assert.ok(await apply.isDisabled(), 'Empty selection could be submitted');
    await selectReviewFaces(dialog, proposal, [1, 3]);
    await apply.click();
    await dialog.getByText(/Der Vorschlag hat sich geändert/).waitFor();
    assert.deepEqual(acceptRequests, [{
      assignments: [{ person_id: 1, cluster_id: 3, proposal_token: proposal.proposal_token, face_ids: [601, 603], scope: 'detail' }],
    }]);
    assert.ok(await apply.isDisabled(), 'Stale proposal could be sent again without being reloaded');

    await dialog.getByRole('button', { name: 'Aktuellen Vorschlag laden' }).click();
    await selectReviewFaces(dialog, updated, [5]);
    await apply.click();
    await inbox.getByRole('heading', { name: 'Keine Vorschläge offen' }).waitFor();
    assert.deepEqual(acceptRequests[1], {
      assignments: [{ person_id: 1, cluster_id: 3, proposal_token: updated.proposal_token, face_ids: [606], scope: 'detail' }],
    });
    assert.equal(acceptRequests.length, 2, 'Refreshing a stale proposal sent an unexpected mutation');
    console.log('PASS review inbox: 320/390/768/1280px, short viewport, snapshot, subset, load guard, stale retry, keyboard focus');
  } finally {
    initialListing.release();
    previewCrop.release();
    detailCrops.release();
    await page.close();
  }
}

async function selectReviewFaces(dialog, proposal, selection) {
  for (let number = 1; number <= proposal.face_ids.length; number += 1) {
    const checkbox = dialog.getByRole('checkbox', { name: `Gesicht ${number} auswählen`, exact: true });
    await checkbox.and(dialog.locator('input:enabled')).waitFor();
    await checkbox.setChecked(selection.includes(number));
  }
  await dialog.getByText(`${selection.length} von ${proposal.face_count} Gesichtern ausgewählt`, { exact: true }).waitFor();
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    await previewFlow(browser, errors);
    await firstAssignmentFlow(browser, errors);
    await collectSourcesFlow(browser, errors);
    await localRecoveryFlow(browser, errors);
    await mapFlow(browser, errors);
    await liveWorkflowFlow(browser, errors);
    await existingFlow(browser, errors);
    await settingsFlow(browser, errors);
    await previewCacheSettingsFlow(browser, errors);
    await responsiveFlow(browser, errors);
    await reviewInboxFlow(browser, errors);
    assert.deepEqual(errors, [], `Browser page errors: ${errors.join('; ')}`);
    console.log('PASS browser smoke: no uncaught page errors');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
