#!/usr/bin/env node
/* Run through scripts/check-ui.sh so the API and Vite use an isolated fixture. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const albumLibraryFlow = require('./album-library-flow.cjs');
const androidDatabaseExportFlow = require('./android-database-export-flow.cjs');
const archiveAssignmentFlow = require('./archive-assignment-flow.cjs');
const compactReviewFlow = require('./compact-review-flow.cjs');
const helpTourFlow = require('./help-tour-flow.cjs');
const importSourcesFlow = require('./import-sources-flow.cjs');
const manualFaceFlow = require('./manual-face-flow.cjs');
const workspaceNavigationFlow = require('./workspace-navigation-flow.cjs');
const mapFaceliftFlow = require('./map-facelift-flow.cjs');
const { streetTileFixture } = require('./street-tile-fixture.cjs');
const personDirectoryFlow = require('./person-directory-flow.cjs');
const faceliftWorkspacesFlow = require('./facelift-workspaces-flow.cjs');
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

async function newPage(browser, width, height, errors, { persistentPreferences = false, ...options } = {}) {
  const page = await browser.newPage({ viewport: { width, height }, ...options });
  page.setDefaultTimeout(12000);
  page.on('pageerror', (error) => errors.push(error.message));
  // Android 12's system WebView in CI lacks Array.prototype.at.
  await page.addInitScript(() => Reflect.deleteProperty(Array.prototype, 'at'));
  if (!persistentPreferences) {
    // Each flow starts with fresh presentation preferences unless it exercises
    // installation-wide persistence against the actual test backend.
    const preferences = {};
    await page.route('**/api/ui-preferences', (route) => {
      if (route.request().method() === 'PATCH') {
        const { values, only_if_absent } = route.request().postDataJSON();
        for (const [key, value] of Object.entries(values)) {
          if (!only_if_absent || !Object.hasOwn(preferences, key)) preferences[key] = value;
        }
      }
      return route.fulfill({ json: preferences });
    });
  }
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
    const openTab = page.getByRole('button', { name: /^Ohne Namen/ }).and(page.locator('[aria-current="page"]'));
    await assign.click();
    await page.waitForURL(reviewHash);
    await openTab.waitFor();
    await page.locator('.cluster-workspace__sidebar').waitFor();
    assert.equal(await page.locator('.cluster-detail-topbar').isVisible(), false);
    await page.reload();
    await openTab.waitFor();
    await page.getByRole('button', { name: /^Personen korrigieren/ }).click();
    await page.goBack();
    await openTab.waitFor();
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
    let previews = 0;
    await page.route('**/api/database/restore-preview', async (route) => {
      previews += 1;
      if (previews === 1) {
        await firstBackup.ready;
        return route.fulfill({ status: 400, json: { detail: 'Die Testdatei ist keine gültige Sicherung.' } });
      }
      return route.fulfill({ json: { photos: 2, people: 1, albums: 1, checksum: 'preview-checksum' } });
    });
    await page.route('**/api/database/import', async (route) => {
      restores += 1;
      assert.equal(route.request().headers()['x-restore-sha256'], 'preview-checksum');
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
    const storage = backupCard.locator('details').filter({ hasText: 'Speicherort der Bibliothek' });
    assert.equal(await storage.getAttribute('open'), null);
    await storage.getByText('Speicherort der Bibliothek', { exact: true }).press('Enter');
    assert.notEqual(await storage.getAttribute('open'), null);
    const fileField = backupCard.locator('input[type="file"]');
    const upload = (body) => fileField.setInputFiles({ name: 'demo.sqlite', mimeType: 'application/octet-stream', buffer: Buffer.from(body) });
    await upload('invalid');
    await backupCard.getByRole('status').filter({ hasText: 'Die ausgewählte Sicherung wird geprüft' }).waitFor();
    assert.ok(await backupCard.getByRole('button', { name: 'Sicherung erstellen' }).isDisabled());
    firstBackup.release();
    await backupCard.getByRole('alert').filter({ hasText: 'Die Testdatei ist keine gültige Sicherung.' }).waitFor();
    assert.ok(await backupCard.getByRole('button', { name: 'Sicherung wiederherstellen' }).isEnabled());
    await upload('valid fixture');
    const restoreDialog = page.getByRole('dialog', { name: 'Bibliothek aus Sicherung wiederherstellen?' });
    await restoreDialog.getByText('2 Fotos · 1 Person · 1 Album').waitFor();
    await restoreDialog.getByRole('button', { name: 'Abbrechen' }).click();
    assert.equal(restores, 0);
    await upload('valid fixture');
    await restoreDialog.getByRole('button', { name: 'Bibliothek wiederherstellen' }).click();
    await backupCard.getByRole('status').filter({ hasText: 'Die Sicherung wurde wiederhergestellt.' }).waitFor();
    await backupCard.getByRole('button', { name: 'Vorherige Bibliothek herunterladen' }).waitFor();
    assert.equal(restores, 1);

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

    let targetedRetries = 0;
    await page.route('**/api/imports/partial-job/retry', (route) => {
      targetedRetries += 1;
      assert.equal(route.request().method(), 'POST');
      assert.equal(route.request().postData(), null);
      return route.fulfill({ status: 202, json: { id: 'partial-retry', status: 'queued' } });
    });
    await page.evaluate(() => {
      window.recoveryTestEvents.dispatchEvent(new MessageEvent('imports', { data: JSON.stringify({
        jobs: [{ id: 'partial-job', folder_path: '/fixtures/partial', status: 'completed', processed_images: 8,
          total_images: 8, finished_at: '2026-01-21T12:10:00Z', can_retry_issues: true,
          issue_counts: { directory_unavailable: 1, image_unreadable: 1 },
          issues: [{ path: '/fixtures/partial/Unterordner', kind: 'directory_unavailable' },
            { path: '/fixtures/partial/kaputt.jpg', kind: 'image_unreadable' }] }],
        running_count: 0, active_job_ids: [],
      }) }));
    });
    const issuesJob = page.locator('article.activity-task').filter({ hasText: 'Abgeschlossen mit Hinweisen' });
    await issuesJob.getByText('Betroffene Pfade ansehen', { exact: true }).click();
    await issuesJob.getByText('Ordner nicht erreichbar', { exact: true }).waitFor();
    await issuesJob.getByText('Datei kann nicht als Bild gelesen werden', { exact: true }).waitFor();
    await issuesJob.getByRole('button', { name: 'Ausgelassene Pfade erneut prüfen' }).click();
    await page.getByRole('status').filter({ hasText: 'Ausgelassene Pfade erneut zur Prüfung eingereiht.' }).waitFor();
    assert.equal(targetedRetries, 1);
    assert.deepEqual(retryPaths, ['/fixtures/cancelled', '/fixtures/cancelled']);
    await page.getByRole('button', { name: 'Aktivitäten schließen' }).click();
    assert.ok(await page.locator('.activity-center__trigger').evaluate((button) => button === document.activeElement));
    console.log('PASS recovery feedback: backup, whole folder retry, recorded skipped paths and focus return');
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
    const borders = page.locator('.gps-map__border');
    assert.equal(await borders.count(), 2);
    for (const border of await borders.all()) {
      assert.ok(await border.evaluate((path) => path.getTotalLength() > 0 &&
        getComputedStyle(path).pointerEvents === 'none' &&
        getComputedStyle(path).vectorEffect === 'non-scaling-stroke'));
    }
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
    await page.getByLabel('Zeitraum', { exact: true }).selectOption('custom');
    await page.getByLabel('Von', { exact: true }).fill('2025-01-01');
    await mapRequest;
    assert.equal(await markers.count(), 0, 'Markers from the previous filter stayed active during loading');
    await page.getByLabel('Bis', { exact: true }).fill('2024-12-31');
    await page.getByRole('alert').filter({ hasText: 'Enddatum' }).waitFor();
    assert.equal(await page.getByText('Karte wird aktualisiert …').count(), 0, 'Invalid dates left the map in a loading state');
    assert.equal(await markers.count(), 0);
    await page.unroute('**/api/map/points?**');
    await page.getByRole('button', { name: 'Filter zurücksetzen', exact: true }).click();
    await waitForCount(85);
    await markers.first().waitFor();
    await page.getByRole('button', { name: 'Weitere Fotos laden', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.gps-page__photo').length === 72);

    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
    await waitForCount(29);
    await borders.first().waitFor();
    await page.getByLabel('Zeitraum', { exact: true }).selectOption('custom');
    await page.getByLabel('Von', { exact: true }).fill('2025-01-01');
    await page.getByLabel('Bis', { exact: true }).fill('2025-12-31');
    await waitForCount(14);
    await page.getByLabel('Bis', { exact: true }).fill('2024-12-31');
    await page.getByRole('alert').filter({ hasText: 'Enddatum' }).waitFor();
    assert.equal(await page.locator('.gps-page__photo').count(), 0);
    await page.getByRole('button', { name: 'Filter zurücksetzen', exact: true }).click();
    await waitForCount(29);
    await page.getByRole('button', { name: 'Ganze Karte zeigen', exact: true }).click();
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
    assert.ok(markerCount > 2, 'Fixture marker needs multiple photos to test the viewer sequence and an uncached original');
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
      return url.pathname === '/api/map/points/selection' && url.searchParams.has('grid_columns');
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

    const originalFile = /\/api\/images\/\d+\/file(?:\?.*)?$/;
    await page.route(originalFile, (route) => route.fulfill({ status: 404, body: 'Missing fixture' }));
    // Open a photo whose original has not already been decoded in this document.
    await page.locator('.gps-page__photo').nth(2).click();
    await gallery.getByRole('alert').filter({ hasText: 'Die Bilddatei konnte nicht geöffnet werden.' }).waitFor();
    await gallery.getByRole('button', { name: 'Galerie schließen' }).click();
    await page.unroute(originalFile);

    const resetMapFilters = page.getByRole('button', { name: 'Filter zurücksetzen', exact: true });
    if (await resetMapFilters.count()) await resetMapFilters.click();
    const showWorld = page.getByRole('button', { name: 'Ganze Karte zeigen', exact: true });
    if (await showWorld.count()) await showWorld.click();
    await waitForCount(85);
    await page.route('**/api/map/points?**', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Testfehler Karte"}' }));
    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
    await page.getByRole('alert').filter({ hasText: 'Testfehler Karte' }).waitFor();
    assert.equal(await markers.count(), 0, 'Failed marker request restored markers from another filter');
    await page.unroute('**/api/map/points?**');
    await page.getByRole('button', { name: 'Erneut versuchen', exact: true }).click();
    await waitForCount(29);
    await page.getByRole('alert').waitFor({ state: 'hidden' });

    await showWorld.click();
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
    await page.getByRole('button', { name: 'Diese Seite auswählen (8)', exact: true }).click();
    assert.ok(await page.getByRole('button', { name: 'Umbenennung prüfen', exact: true }).isEnabled());
    await page.getByRole('button', { name: 'Auswahl aufheben', exact: true }).click();
    await page.getByRole('button', { name: /Benennungsschema ändern/ }).click();
    await page.waitForURL('**/einstellungen/dateinamen');
    await page.getByRole('navigation', { name: 'Hauptnavigation' }).getByRole('button', { name: 'Personen' }).click();
    await page.getByRole('button', { name: 'Prüfen', exact: true }).click();
    await page.getByRole('button', { name: /^Ohne Namen / }).click();
    await page.getByRole('button', { name: /^Personen korrigieren/ }).click();
    const cards = page.locator('.page-pane--active button.cluster-person-browser__cluster-card');
    await cards.first().waitFor();
    const card = (await cards.count()) > 1 ? cards.nth(1) : cards.first();
    await card.focus();
    await page.keyboard.press('Enter');
    await card.and(page.locator('[aria-pressed="true"]')).waitFor();
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

    await page.getByText('Aussortieren oder lösen', { exact: true }).click();
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

    await page.getByRole('button', { name: /^Aussortiert / }).click();
    await screenshot(page, 'existing-flows');
    console.log('PASS existing flows: library/folder retry, person filter, import, rename, settings, review tabs');
  } finally {
    await page.close();
  }
}

async function mapWithoutStorageFlow(browser, errors) {
  for (const storageBlocked of [false, true]) {
    const page = await newPage(browser, 1440, 1000, errors);
    try {
      await page.addInitScript((blocked) => {
        if (blocked) {
          Object.defineProperty(window, 'localStorage', {
            get() { throw new DOMException('Storage is disabled', 'SecurityError'); },
          });
        } else {
          Reflect.deleteProperty(window, 'localStorage');
        }
      }, storageBlocked);
      await page.goto(`${webBase}/#/weltkarte`);
      await page.getByRole('group', { name: 'Weltkarte mit Fotostandorten' }).waitFor();
      await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
      await page.getByText('Kartendarstellung', { exact: true }).click();
      const style = page.getByLabel('Kartenstil');
      const names = page.getByLabel('Ländernamen anzeigen');
      assert.equal(await style.inputValue(), 'standard');
      assert.equal(await names.isChecked(), false);
      await style.selectOption('landscape');
      await page.locator('.gps-map__svg--landscape').waitFor();
      await names.check();
      await page.locator('.gps-map__place-label--country').first().waitFor();
    } finally {
      await page.close();
    }
  }
  console.log('PASS map: defaults and display controls with missing or blocked localStorage');
}

async function onlineMapDetailsFlow(browser, errors) {
  const page = await newPage(browser, 1280, 920, errors);
  const requested = [];
  const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1uoAAAAASUVORK5CYII=', 'base64');
  let satelliteAvailable = false;
  try {
    await page.route('**/api/map/detail-tiles/**', (route) => {
      const url = new URL(route.request().url());
      const [, source, z, x, y] = url.pathname.match(/\/detail-tiles\/(street|landscape|satellite)\/(\d+)\/(\d+)\/(\d+)$/) ?? [];
      assert.ok(source && !url.search, 'Tile requests must contain only a source and grid coordinates');
      requested.push({ source, z: Number(z), x: Number(x), y: Number(y) });
      if (source === 'satellite' && !satelliteAvailable) return route.fulfill({ status: 503 });
      return route.fulfill({ status: 200, contentType: source === 'street' ? 'application/vnd.mapbox-vector-tile' : 'image/png',
        body: source === 'street' ? streetTileFixture() : image });
    });
    await page.goto(`${webBase}/#/weltkarte`);
    const maps = page.getByRole('group', { name: 'Weltkarte mit Fotostandorten' });
    const online = page.getByRole('checkbox', { name: 'Kartendetails online', exact: true });
    assert.equal(await online.isChecked(), false);
    await page.getByText('Kartendarstellung', { exact: true }).click();
    const style = page.getByLabel('Kartenstil', { exact: true });
    await style.selectOption('landscape');
    await online.check();
    await page.getByText('Orte aus meinen Fotos', { exact: true }).click();
    await page.getByRole('searchbox', { name: 'Fotoorte suchen' }).fill('Berlin');
    const berlin = page.getByRole('button', { name: /^Berlin \d+ Fotos/ });
    await berlin.waitFor();
    assert.equal(requested.length, 0, 'The world overview contacted a map provider');
    await online.uncheck();
    await berlin.click();
    await page.getByText(/Offline-Übersicht · Straßen/).waitFor();
    assert.equal(requested.length, 0, 'Zooming contacted a map provider before opt-in');
    await page.getByRole('img', { name: /^Maßstab .* (m|km)$/ }).waitFor();
    await online.check();
    const landscape = maps.locator('[data-map-raster-detail="landscape"] image').first();
    const roads = maps.locator('.gps-map__street-detail path').first();
    await landscape.waitFor();
    await roads.waitFor();
    await page.getByRole('link', { name: 'OpenFreeMap' }).waitFor();
    assert.ok(requested.some((tile) => tile.source === 'street' && tile.z >= 11));

    await style.selectOption('satellite');
    const retry = page.getByRole('button', { name: 'Kartendetails erneut laden' });
    await retry.waitFor();
    await roads.waitFor();
    await maps.locator('.gps-map__satellite[visibility="visible"]').waitFor({ state: 'attached' });
    satelliteAvailable = true;
    await retry.click();
    await maps.locator('[data-map-raster-detail="satellite"] image').first().waitFor();
    await page.getByRole('link', { name: /Copernicus/ }).waitFor();
    const satelliteRequests = requested.filter((tile) => tile.source === 'satellite');
    assert.ok(satelliteRequests.length > 0 && satelliteRequests.every((tile) => tile.z <= 13));

    await online.uncheck();
    await roads.waitFor({ state: 'detached' });
    await page.getByText(/Offline-Übersicht · Straßen/).waitFor();
    const count = requested.length;
    const before = await maps.getAttribute('viewBox');
    await maps.dblclick({ position: { x: 30, y: 30 } });
    assert.notEqual(await maps.getAttribute('viewBox'), before, 'Double click did not zoom in');
    assert.equal(requested.length, count);
    console.log('PASS online map details: opt-in, street geometry, both raster styles, offline fallback and retry');
  } finally {
    await page.close();
  }
}

async function durablePreferencesFlow(browser, errors) {
  const options = { persistentPreferences: true };
  const first = await newPage(browser, 1440, 1000, errors, options);
  const waitForPreferenceWrite = (page, status) => page.waitForResponse((response) =>
    response.url().endsWith('/api/ui-preferences') && response.request().method() === 'PATCH' && response.status() === status);
  try {
    await first.addInitScript(() => {
      localStorage.setItem('face-manager:map-style', 'satellite');
      localStorage.setItem('face-manager:map-country-names', 'false');
      localStorage.setItem('face-manager:image-grid-size', 'large');
      localStorage.setItem('face-manager:help-introduction-v1', 'seen');
    });
    const imported = waitForPreferenceWrite(first, 200);
    await first.goto(`${webBase}/#/weltkarte`);
    assert.deepEqual(await (await imported).json(), {
      map_style: 'satellite', map_country_names: false, image_grid_size: 'large', help_introduction_seen: true,
    });
    await first.getByText('Kartendarstellung', { exact: true }).click();
    const style = first.getByLabel('Kartenstil');
    assert.equal(await style.inputValue(), 'satellite');

    await first.route('**/api/ui-preferences', (route) => route.request().method() === 'PATCH'
      ? route.fulfill({ status: 503, json: {} }) : route.continue());
    const rejected = waitForPreferenceWrite(first, 503);
    await style.selectOption('landscape');
    await rejected;
    await first.locator('.gps-map__svg--landscape').waitFor();
    await first.unroute('**/api/ui-preferences');
    const retry = first.getByRole('alert').filter({ hasText: 'Die Ansichts- und Hilfeeinstellungen konnten nicht gespeichert werden.' })
      .getByRole('button', { name: 'Erneut versuchen', exact: true });
    await retry.waitFor();
    const [retried] = await Promise.all([waitForPreferenceWrite(first, 200), retry.click()]);
    assert.equal((await retried.json()).map_style, 'landscape');

    const labels = waitForPreferenceWrite(first, 200);
    await first.getByLabel('Ländernamen anzeigen').check();
    assert.equal((await (await labels).json()).map_country_names, true);
  } finally {
    await first.close();
  }

  const reopened = await newPage(browser, 1440, 1000, errors, options);
  try {
    await reopened.addInitScript(() => Reflect.deleteProperty(window, 'localStorage'));
    await reopened.goto(`${webBase}/#/weltkarte`);
    await reopened.locator('.gps-map__svg--landscape').waitFor();
    await reopened.getByText('Kartendarstellung', { exact: true }).click();
    assert.equal(await reopened.getByLabel('Ländernamen anzeigen').isChecked(), true);
    await reopened.getByRole('navigation', { name: 'Hauptnavigation' }).getByRole('button', { name: 'Bilder', exact: true }).click();
    await reopened.getByText('Ansicht', { exact: true }).click();
    assert.equal(await reopened.getByRole('slider', { name: 'Bildgröße im Raster' }).getAttribute('aria-valuetext'), 'Groß');
    assert.equal(await reopened.getByText('Möchtest du Face Manager kennenlernen?', { exact: true }).count(), 0);
  } finally {
    await reopened.close();
  }
  console.log('PASS preferences: import, failed save and retry, persistence across isolated browser contexts with missing storage');
}

async function mapSelectionAndJourneyFlow(browser, errors) {
  const page = await newPage(browser, 1440, 1000, errors);
  const localOrigin = new URL(webBase).origin;
  try {
    await page.route('**/*', (route) =>
      new URL(route.request().url()).origin === localOrigin ? route.fallback() : route.abort());
    await page.goto(`${webBase}/#/weltkarte`);
    const results = page.locator('#gps-map-results');
    await results.getByText('85 Fotos', { exact: true }).waitFor();
    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
    await page.getByText('Kartendarstellung', { exact: true }).click();
    await page.getByLabel('Ländernamen anzeigen').check();
    await page.locator('.gps-map__place-label--country').first().waitFor();
    const reliefLoaded = page.waitForResponse((response) => response.url().endsWith('/map/world-relief.webp') && response.ok());
    await page.getByLabel('Kartenstil').selectOption('landscape');
    await reliefLoaded;
    await page.locator('.gps-map__relief image').waitFor({ state: 'attached' });
    await page.getByLabel('Ländernamen anzeigen').uncheck();
    assert.equal(await page.locator('.gps-map__place-label--country').count(), 0);
    assert.equal(await page.locator('.gps-map__relief image').count(), 1);
    const satelliteLoaded = page.waitForResponse((response) => response.url().endsWith('/map/world-satellite.webp') && response.ok());
    await page.getByLabel('Kartenstil').selectOption('satellite');
    await satelliteLoaded;
    await page.locator('.gps-map__satellite[visibility="visible"]').waitFor({ state: 'attached' });
    await page.getByRole('link', { name: /NASA Earth Observatory/ }).waitFor();
    await page.getByLabel('Kartenstil').selectOption('landscape');
    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Welt');
    await results.getByText('85 Fotos', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Bereich wählen', exact: true }).click();
    await page.getByRole('button', { name: 'Sichtbaren Bereich wählen' }).click();
    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
    await results.getByText('85 Fotos', { exact: true }).waitFor();

    const exportButton = page.getByRole('button', { name: 'Fotobuchbild gestalten' });
    const exportBounds = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname === '/api/map/points/selection' && url.searchParams.get('grid_columns') === '18';
    });
    await exportButton.click();
    const book = page.getByRole('dialog', { name: 'Fotobuchbild erstellen' });
    await book.waitFor();
    const exportRequest = await exportBounds;
    const bounds = new URL(exportRequest.url()).searchParams;
    assert.equal(bounds.get('west'), '-180', 'Export uses the camera instead of the fixed selection');
    assert.equal(bounds.get('east'), '180');
    const bookPreview = book.getByRole('img', { name: 'Vorschau des Fotobuchbildes' });
    await bookPreview.waitFor();
    await book.getByLabel('Titel', { exact: true }).fill('Sommer & Meer');
    await book.getByText('Schrift und Layout', { exact: true }).click();
    await book.getByLabel('Unterzeile (optional)', { exact: true }).fill('Unser Fotoalbum');
    await book.getByText('Schrift und Layout', { exact: true }).click();
    await book.getByLabel('Format', { exact: true }).selectOption('portrait');
    await book.getByLabel('Farbwelt', { exact: true }).selectOption('coast');
    await book.getByText('Darstellung', { exact: true }).click();
    await book.getByLabel('Kartenstil', { exact: true }).selectOption('standard');
    const visibleBounds = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname === '/api/map/points/selection' && url.searchParams.get('grid_columns') === '18'
        && url.searchParams.get('west') !== '-180';
    });
    await book.getByLabel('Kartenbereich', { exact: true }).selectOption('viewport');
    assert.deepEqual((await visibleBounds).postDataJSON().selection.bounds, exportRequest.postDataJSON().selection.bounds,
      'Choosing the current viewport should still restrict exported photos to the fixed selection');
    await book.getByLabel('Bildausschnitt', { exact: true }).selectOption('wide');
    await book.getByLabel('Markerfarbe', { exact: true }).selectOption('ocean');
    await bookPreview.waitFor();
    await page.waitForFunction(() => document.querySelector('.gps-album-dialog__preview img')?.naturalWidth === 2480);
    const savedBook = page.waitForEvent('download');
    await book.getByRole('button', { name: 'PNG speichern', exact: true }).click();
    const image = fs.readFileSync(await (await savedBook).path());
    assert.equal(image.subarray(1, 4).toString(), 'PNG');
    assert.deepEqual([image.readUInt32BE(16), image.readUInt32BE(20)], [2480, 3508]);
    const densityChunk = image.indexOf(Buffer.from('pHYs'));
    assert.ok(densityChunk > 0);
    assert.equal(image.readUInt32BE(densityChunk + 4), 11811);
    await page.keyboard.press('Escape');
    await book.waitFor({ state: 'hidden' });
    assert.ok(await exportButton.evaluate((element) => element === document.activeElement));
    assert.equal(await page.getByLabel('Kartenstil').inputValue(), 'landscape');

    const opener = results.getByRole('button', { name: 'Galerie öffnen' });
    await opener.click();
    const grid = page.getByRole('dialog', { name: 'Galerie der Kartenauswahl' });
    await grid.waitFor();
    await assertTabTrap(page, grid);
    const lastTile = grid.locator('[data-gallery-index="35"]');
    await lastTile.click();
    const viewer = page.getByRole('dialog', { name: 'Bildansicht' });
    await viewer.getByText('Foto 36 von 85', { exact: true }).waitFor();
    await viewer.getByRole('button', { name: 'Nächstes Bild', exact: true }).click();
    await viewer.getByText('Foto 37 von 85', { exact: true }).waitFor();
    await viewer.getByRole('button', { name: 'Zurück zur Galerie' }).click();
    await grid.waitFor();
    await page.waitForFunction(() => document.activeElement?.getAttribute('data-gallery-index') === '36');
    const filtered = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === '/api/images' && url.searchParams.get('west') === '-180' && response.ok();
    });
    await grid.getByRole('button', { name: 'In Bilder öffnen' }).click();
    assert.equal((await (await filtered).json()).total, 85);
    await page.waitForURL('**/#/bilder/auswahl');
    await page.goBack();
    await results.getByText('85 Fotos', { exact: true }).waitFor();

    const stop = { id: '1', number: 1, section: 1, latitude: 52.52, longitude: 13.405, start: '2024-01-01T12:00:00', end: '2024-01-01T12:00:00', image_ids: [1], uncertain: false };
    const next = { ...stop, id: '2', number: 2, latitude: 48.85, longitude: 2.35, start: '2024-01-01T17:00:00', end: '2024-01-01T17:00:00', image_ids: [2] };
    const revisited = { ...stop, id: '4', number: 3, start: '2024-01-02T08:00:00', end: '2024-01-02T08:00:00', image_ids: [4] };
    let journeyRequests = 0;
    await page.route('**/api/map/journey?**', (route) => {
      journeyRequests += 1;
      const indexing = journeyRequests === 1;
      return route.fulfill({ json: {
        stops: [indexing ? stop : { ...stop, image_ids: [1, 3] }, next, revisited],
        connections: [{ from: stop, to: next, from_stop_id: stop.id, to_stop_id: next.id },
          { from: next, to: revisited, from_stop_id: next.id, to_stop_id: revisited.id }], photo_count: indexing ? 3 : 4,
        missing_location_count: 0, missing_time_count: 0, uncertain_count: 0, indexing,
      } });
    });
    await page.getByRole('button', { name: 'Verlauf', exact: true }).click();
    await page.locator('.gps-map__journey-line').first().waitFor();
    const activeMarker = page.getByRole('button', { name: '2 Stopps an dieser Stelle', exact: true });
    await activeMarker.press('Enter');
    const stopList = results.getByRole('list', { name: 'Chronologische Stopps' });
    assert.equal(await stopList.getByRole('button').count(), 2);
    await stopList.getByRole('button', { name: /^Stopp 1/ }).click();
    await results.getByRole('heading', { name: 'Fotos von Stopp 1' }).waitFor();
    assert.equal(await activeMarker.getAttribute('aria-pressed'), 'true');
    await activeMarker.scrollIntoViewIfNeeded();
    assert.equal(await activeMarker.evaluate((element) => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return document.elementFromPoint(x + width / 2, y + height / 2)
        ?.closest('.gps-map__journey-stop')?.getAttribute('aria-label');
    }), '2 Stopps an dieser Stelle');
    const arrows = await page.locator('.gps-map__journey-direction').evaluateAll((paths) =>
      paths.map((path) => path.getAttribute('transform')?.split(' scale')[0]));
    assert.equal(new Set(arrows).size, 2, 'Return route direction arrows overlap');
    await results.getByRole('button', { name: /Berlin-000/ }).waitFor();
    await exportButton.click();
    await book.getByLabel('Layout', { exact: true }).selectOption('list');
    await bookPreview.waitFor();
    const journeyArtwork = await bookPreview.evaluate((image) => fetch(image.src).then((response) => response.text()));
    assert.match(journeyArtwork, /Bei Berlin/);
    assert.match(journeyArtwork, /Bei Paris/, 'Selecting a stop restricted the whole journey export');
    await book.getByText('Route gestalten', { exact: true }).click();
    await book.getByRole('combobox', { name: /1\. Bei Berlin → 2\. Bei Paris/ }).selectOption('plane');
    await book.getByRole('combobox', { name: /2\. Bei Paris → 3\. Bei Berlin/ }).selectOption('train');
    await book.locator('.gps-album-dialog__preview[aria-busy="false"] img').waitFor();
    await bookPreview.waitFor();
    const customized = await bookPreview.evaluate((image) => fetch(image.src).then((response) => response.text()));
    assert.match(customized, /data-album-transport="plane"/);
    assert.match(customized, /data-album-transport="train"/);
    assert.match(customized, /Anreise: Flugzeug/);
    assert.match(customized, /Anreise: Zug/);
    await book.getByText('Route gestalten', { exact: true }).click();
    await screenshot(page, 'map-album');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Entwurf verwerfen', exact: true }).click();
    await book.waitFor({ state: 'hidden' });
    await screenshot(page, 'map-journey');
    await results.getByRole('button', { name: /Tokio-002/ }).waitFor();
    const exactSelection = page.waitForResponse((response) => response.url().includes('/api/images/search?') && response.ok());
    await results.getByRole('button', { name: 'In Bilder öffnen', exact: true }).click();
    const exact = await exactSelection;
    assert.deepEqual(exact.request().postDataJSON().selection.image_ids, [1, 3]);
    assert.equal((await exact.json()).total, 2);

    await page.route('**/api/map/points?**', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue().catch(() => {});
    });
    await page.goto(`${webBase}/#/weltkarte`);
    await page.reload();
    await page.getByRole('button', { name: 'Verlauf', exact: true }).click();
    await page.getByRole('heading', { name: 'Chronologische Stopps' }).waitFor();
    assert.equal(await page.locator('.gps-page__stat').count(), 0);
    assert.equal(await page.locator('.gps-page__collection').getByText('…', { exact: true }).count(), 0);
    console.log('PASS map workspace: offline styles, fullscreen cross-page gallery, filter transfer and exact journey stop photos');
  } finally {
    await page.close();
  }
}

async function pagedLibraryFacesFlow(browser, errors) {
  const page = await newPage(browser, 1024, 650, errors);
  const targetIndex = 44;
  const face = {
    id: 1, bbox_x: 60, bbox_y: 20, bbox_w: 10, bbox_h: 10,
    cluster_id: null, person_name: 'Name aus Liste', review_status: 'unknown_person',
  };
  const photos = Array.from({ length: 240 }, (_, index) => ({
    id: index + 1001, filename: `Foto ${index + 1}`, directory: '/fixture', image_path: `/fixture/${index + 1}.jpg`,
    content_hash: null, created_at: null, image_width: index === targetIndex ? null : 100, image_height: index === targetIndex ? null : 100,
    location_count: 1, locations: [], faces: index === targetIndex ? [face] : [],
  }));
  const target = photos[targetIndex];
  const image = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>';
  let detailRequests = 0;
  let refreshing = false;
  let releaseRefresh;
  const refreshGate = new Promise((resolve) => { releaseRefresh = resolve; });
  const refreshPages = [];
  try {
    await page.addInitScript(() => {
      window.EventSource = class TestEventSource extends EventTarget {
        constructor() {
          super();
          window.pagedLibraryEvents = this;
          queueMicrotask(() => this.onopen?.(new Event('open')));
        }
        close() {}
      };
    });
    await page.route('**/api/images?**', async (route) => {
      const params = new URL(route.request().url()).searchParams;
      const offset = Number(params.get('offset'));
      const limit = Number(params.get('limit'));
      if (refreshing && limit > 1) {
        refreshPages.push([offset, limit]);
        await refreshGate;
      }
      const items = photos.slice(offset, offset + limit).map((photo) =>
        refreshing && photo.id === 1205 ? { ...photo, filename: 'Aktualisiertes Foto' } : photo);
      return route.fulfill({ json: { items, offset, limit, total: photos.length, has_more: offset + items.length < photos.length, available_persons: [] } });
    });
    await page.route(/\/api\/images\/\d+\/(thumbnail|file)(\?.*)?$/, (route) =>
      route.fulfill({ contentType: 'image/svg+xml', body: image }));
    await page.route(`**/api/images/${target.id}/detail?resolve_geometry=true`, (route) => {
      detailRequests += 1;
      if (detailRequests === 1) return route.fulfill({ status: 503, json: {} });
      return route.fulfill({ json: {
        ...target, image_width: 100, image_height: 100,
        faces: [{ ...face, bbox_x: 20, person_name: 'Name aus Detail' }, { ...face, id: 2 }],
      } });
    });

    await page.goto(`${webBase}/#/bilder`);
    const cards = page.locator('[data-image-id]');
    await cards.first().waitFor();
    // The first card can appear before the masonry layout and scroll listener settle.
    const scrollUntil = (count) => page.waitForFunction((expected) => {
      const pane = document.querySelector('[data-workspace-page="people"] > .page-content');
      if (!pane) return false;
      if (pane.querySelectorAll('[data-image-id]').length >= expected) return true;
      pane.scrollTop = pane.scrollHeight;
      pane.dispatchEvent(new Event('scroll'));
      return false;
    }, count, { polling: 150 });
    await scrollUntil(targetIndex + 1);
    const card = page.locator(`[data-image-id="${target.id}"]`);
    await card.scrollIntoViewIfNeeded();
    const box = card.locator('.hologram-box');
    await box.waitFor();
    assert.equal(await box.evaluate((element) => element.style.left), '20%');
    assert.equal(await card.locator('.hologram-overlay').count(), 1);
    assert.equal(await card.locator('.hologram-label').textContent(), 'Als unbekannt aussortiert');
    await card.locator('.image-gallery-card__open').click();
    const gallery = page.getByRole('dialog', { name: 'Bildansicht' });
    await gallery.locator('.hologram-box').waitFor();
    assert.equal(await gallery.locator('.hologram-box').count(), 1);
    assert.equal(await gallery.locator('.hologram-box').evaluate((element) => element.style.left), '20%');
    assert.ok(detailRequests >= 2, 'Geometry was not retried after the transient failure');
    await gallery.getByRole('button', { name: 'Galerie schließen' }).click();

    await scrollUntil(photos.length);
    const pendingRefresh = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname === '/api/images' && url.searchParams.get('limit') === '200';
    });
    refreshing = true;
    await page.evaluate(() => {
      window.pagedLibraryEvents.dispatchEvent(new MessageEvent('clusters', { data: '{"reason":"assignment"}' }));
    });
    await pendingRefresh;
    const viewport = page.locator('[data-workspace-page="people"] .page-content');
    const desiredScroll = await viewport.evaluate((element) => {
      element.scrollTop = 460;
      return element.scrollTop;
    });
    assert.ok(desiredScroll > 100);
    releaseRefresh();
    await page.getByRole('button', { name: 'Aktualisiertes Foto in Galerie öffnen' }).waitFor();
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve)))));
    assert.ok(Math.abs(await viewport.evaluate((element) => element.scrollTop) - desiredScroll) <= 3,
      'The photo refresh returned to the scroll position from before the request');
    assert.deepEqual(refreshPages, [[0, 200], [200, 40]]);
    assert.equal(await cards.count(), photos.length);
    console.log('PASS paged library faces: lazy metadata, gallery, and scroll during refresh beyond 200 photos');
  } finally {
    releaseRefresh();
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
    const firstPhoto = page.locator('.image-gallery-card__open:enabled').first();
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
    const removedPhoto = await page.locator('.gps-page__photo').first().getAttribute('aria-label');
    const keptPhoto = await page.locator('.gps-page__photo').nth(36).getAttribute('aria-label');
    const keptTile = page.getByRole('button', { name: keptPhoto, exact: true });
    await keptTile.click();
    await gallery.waitFor();
    await page.route('**/api/map/images?**', async (route) => {
      const url = new URL(route.request().url());
      const limit = Number(url.searchParams.get('limit'));
      const firstPage = !url.searchParams.has('before_id');
      if (firstPage) url.searchParams.set('limit', String(limit + 1));
      const response = await route.fetch({ url: url.href });
      const result = await response.json();
      await route.fulfill({ response, json: { ...result, total: 84, items: firstPage ? result.items.slice(1) : result.items } });
    });
    await publishLibraryChange('delete_image');
    await page.getByRole('status').filter({ hasText: /^84 Fotos$/ }).waitFor();
    assert.equal(await page.locator('.gps-page__photo').count(), 72, 'Library refresh discarded the loaded extent');
    assert.equal(await page.getByRole('button', { name: removedPhoto, exact: true }).count(), 0, 'Library refresh kept a deleted photo');
    assert.ok(await gallery.isVisible(), 'Library refresh closed the open map gallery');
    await page.keyboard.press('Escape');
    await keptTile.and(page.locator(':focus')).waitFor();
    await page.unroute('**/api/map/images?**');
    await page.locator('.gps-page__photo').first().click();
    await gallery.waitFor();
    const restoredMap = gate();
    await page.route('**/api/map/images?**', async (route) => {
      await restoredMap.ready;
      await route.continue();
    }, { times: 1 });
    try {
      await publishLibraryChange('database_import');
      await gallery.waitFor({ state: 'hidden' });
      assert.equal(await page.locator('.gps-page__photo').count(), 0, 'Photos from the replaced library stayed visible');
    } finally {
      restoredMap.release();
    }
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
    await page.getByRole('alert').filter({ hasText: 'Diese Gesichtsgruppe ist nicht mehr verfügbar' }).waitFor();
    await page.waitForURL('**/#/gesichter-pruefen');
    const groupRequest = page.waitForRequest('**/api/face-review-groups/unknown_person/faces');
    await page.getByRole('button', { name: /^Aussortiert / }).click();
    await groupRequest;

    let total = 27;
    const candidates = Array.from({ length: 27 }, (_, index) => ({
      image_id: index + 1, location_id: index + 1, path: `/fixture/foto-${index + 1}.jpg`,
      current_filename: `foto-${index + 1}.jpg`, proposed_filename: `foto-${index + 1} Anna.jpg`,
      detected_person_names: ['Anna'], current_suffix_person_names: [],
    }));
    let renameCountRequests = 0;
    const renamePageRequests = [];
    await page.route('**/api/image-renames/count?**', (route) => {
      renameCountRequests += 1;
      return route.fulfill({ json: { total } });
    });
    const nextPageGate = gate();
    let holdNextPage = true;
    await page.route('**/api/image-renames?**', async (route) => {
      const query = new URL(route.request().url()).searchParams;
      const offset = Number(query.get('offset'));
      const limit = Number(query.get('limit'));
      renamePageRequests.push({ offset, limit });
      if (offset === 25 && holdNextPage) {
        holdNextPage = false;
        await nextPageGate.ready;
      }
      return route.fulfill({ json: {
        items: candidates.slice(0, total).slice(offset, offset + limit),
        offset, limit, total: null, has_more: offset + limit < total, available_persons: ['Anna'],
      } });
    });
    await page.route('**/api/image-renames/preview', (route) => {
      assert.deepEqual(route.request().postDataJSON().selected_paths, ['/fixture/foto-26.jpg', '/fixture/foto-27.jpg']);
      return route.fulfill({ json: {
        ready_count: 1, skipped_count: 1,
        items: [
          { path: '/fixture/foto-26.jpg', target_path: '/fixture/foto-26 Anna.jpg', reason: null, source_token: 'e'.repeat(64) },
          { path: '/fixture/foto-27.jpg', target_path: '/fixture/foto-27 Anna.jpg', reason: 'target_exists', source_token: null },
        ],
      } });
    });
    await page.route('**/api/image-renames/apply', (route) => {
      assert.deepEqual(route.request().postDataJSON().confirmed_targets, {
        '/fixture/foto-26.jpg': { target_path: '/fixture/foto-26 Anna.jpg', source_token: 'e'.repeat(64) },
      });
      total = 25;
      return route.fulfill({ json: { renamed: [], skipped: [], errors: [], renamed_count: 1, skipped_count: 0, error_count: 0 } });
    });
    await page.goto(`${webBase}/#/dateinamen`);
    const pagination = page.locator('.rename-pagination--top');
    await pagination.getByText('Seite 1 von 2').waitFor();
    try {
      const openingNextPage = page.waitForRequest((request) => {
        const url = new URL(request.url());
        return url.pathname === '/api/image-renames' && url.searchParams.get('offset') === '25';
      });
      await pagination.getByRole('button', { name: 'Weiter' }).click();
      await openingNextPage;
      const liveRefresh = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/image-renames');
      await publishLibraryChange('import_completed');
      // The live refresh uses the requested page once its foreground load finishes.
      nextPageGate.release();
      const liveOffset = new URL((await liveRefresh).url()).searchParams.get('offset');
      assert.equal(liveOffset, '25', 'Live refresh returned to the previous rename page');
    } finally {
      nextPageGate.release();
    }
    await page.getByRole('button', { name: 'Diese Seite auswählen (2)' }).click();
    await page.getByRole('button', { name: 'Umbenennung prüfen', exact: true }).click();
    const confirmRename = page.getByRole('dialog', { name: 'Dateinamen prüfen' });
    await confirmRename.getByText('foto-26 Anna.jpg', { exact: true }).waitFor();
    await confirmRename.getByText('Zielname bereits belegt', { exact: true }).waitFor();
    await confirmRename.getByRole('button', { name: 'Dateien jetzt umbenennen' }).click();
    await pagination.getByText('Seite 1 von 1').waitFor();
    assert.equal(await page.locator('.rename-row').count(), 25);

    await page.clock.install();
    const requestsBeforeResize = renamePageRequests.length;
    const countsBeforeResize = renameCountRequests;
    const resizedPage = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === '/api/image-renames' && url.searchParams.get('limit') === '50';
    });
    await pagination.getByLabel('Pro Seite').selectOption('50');
    await (await resizedPage).finished();
    await page.clock.runFor(1200);
    assert.deepEqual(renamePageRequests.slice(requestsBeforeResize), [{ offset: 0, limit: 50 }],
      'Changing the page size triggered an extra background reload');
    assert.equal(renameCountRequests, countsBeforeResize, 'Changing the page size refetched the unchanged count');
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
    const theme = (label) => themes.getByRole('radio', { name: label, exact: true });
    await theme('Hell').click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await theme('Hell').press('ArrowRight');
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    assert.ok(await theme('Dunkel').isChecked());
    await theme('System').click();
    assert.ok(await theme('System').isChecked());

    const themeSelect = page.getByRole('combobox', { name: 'Farbschema der Anwendung' });
    const failedSave = async (route) => {
      if (route.request().postDataJSON()?.ui_theme === 'dark') {
        await route.fulfill({ status: 503, json: { detail: 'Einstellung vorübergehend nicht verfügbar.' } });
      } else {
        await route.continue();
      }
    };
    await page.route('**/api/settings', failedSave);
    await themeSelect.selectOption('dark');
    assert.ok(await theme('Dunkel').isChecked());
    const retryTheme = page.getByRole('button', { name: 'Darstellung erneut speichern' });
    await retryTheme.waitFor();
    await page.unroute('**/api/settings', failedSave);
    const savedTheme = page.waitForResponse((response) => response.url().endsWith('/api/settings')
      && response.request().postDataJSON()?.ui_theme === 'dark');
    await retryTheme.click();
    assert.equal((await savedTheme).status(), 200);
    await themeSelect.selectOption('system');

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
    const ruleHeading = await page.locator('#filename-rule-start').boundingBox();
    assert.ok(ruleHeading && ruleHeading.width > 180, 'Filename rule heading is squeezed into the former number column');
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
    await page.getByText('85 Bilder', { exact: true }).first().waitFor();
    const nav = page.getByRole('navigation', { name: 'Hauptnavigation' });
    assert.ok(await nav.evaluate((element) => element.scrollWidth <= element.clientWidth));
    await page.locator('.topbar-import-button__label').waitFor();
    for (const width of [320, 390, 768, 901]) {
      await page.setViewportSize({ width, height: 568 });
      await assertReviewFits(page, [page.locator('.workspace-header')], `Compact app header at ${width}px`);
    }
    await page.setViewportSize({ width: 320, height: 568 });
    const filters = page.getByRole('button', { name: 'Filter und Sortierung', exact: true });
    assert.equal(await filters.getAttribute('aria-expanded'), 'false');
    assert.ok(await page.getByRole('button', { name: 'Fotos auswählen', exact: true }).isVisible());
    await filters.press('Enter');
    await page.getByRole('combobox', { name: 'Sortierung' }).waitFor();
    await filters.press('Enter');
    await page.getByRole('combobox', { name: 'Sortierung' }).waitFor({ state: 'hidden' });
    const more = nav.getByRole('button', { name: /^Mehr/ });
    await nav.getByRole('button', { name: 'Personen' }).evaluate((button) => {
      button.focus();
      button.click();
      // Simulate moving to the next navigation control before route restoration.
      button.closest('nav').querySelector('.workflow-sidebar__more').focus();
    });
    await page.getByRole('heading', { name: 'Personen', exact: true }).waitFor();
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    assert.ok(await more.evaluate((button) => document.activeElement === button), 'Navigation restored focus after the user moved it');
    await page.keyboard.press('Enter');
    await nav.getByRole('button', { name: 'Dateinamen' }).waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await more.getAttribute('aria-expanded'), 'false');
    await more.press('Enter');
    await nav.getByRole('button', { name: 'Dateinamen' }).click();
    await page.getByRole('heading', { name: 'Dateinamen', exact: true }).waitFor();
    const fileSelection = page.getByRole('region', { name: 'Dateiauswahl' });
    const bulk = fileSelection.getByRole('button', { name: 'Auswahl', exact: true });
    assert.equal(await bulk.getAttribute('aria-expanded'), 'false');
    await bulk.press('Enter');
    await fileSelection.getByRole('button', { name: /Diese Seite auswählen/ }).waitFor();
    await bulk.press('Enter');
    const preview = await fileSelection.getByRole('button', { name: 'Umbenennung prüfen' }).boundingBox();
    assert.ok(preview && preview.y >= 0 && preview.y + preview.height <= 568, 'Rename review requires scrolling at 320×568');
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
    const section = page.getByRole('combobox', { name: 'Einstellungsbereich' });
    await section.selectOption('daten');
    await page.waitForURL('**/#/einstellungen/daten');
    await page.waitForFunction(() => {
      const switcher = document.querySelector('.settings-compact-navigation')?.getBoundingClientRect();
      const data = document.getElementById('settings-daten')?.getBoundingClientRect();
      return switcher && data && data.top >= switcher.bottom && data.top < innerHeight * 0.6;
    });
    await section.selectOption('erkennung');
    await page.waitForURL('**/#/einstellungen/erkennung');
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
    const open = card.getByRole('button', { name: 'Vorschlag für Anna Beispiel prüfen', exact: true });
    await open.waitFor();
    assert.equal(await card.getByRole('checkbox').count(), 0, 'Ordinary rows must not enter batch mode by default');
    previewCrop.release();
    const viewports = [[320, 568], [390, 844], [768, 1024], [1280, 800]];
    for (const [width, height] of viewports) {
      await page.setViewportSize({ width, height });
      await assertReviewFits(page, [inbox, card], `Review inbox at ${width}×${height}`);
    }

    await open.press('Enter');
    const dialog = page.getByRole('region', { name: 'Gruppe prüfen', exact: true });
    const apply = dialog.getByRole('button', { name: /Gesicht(?:er)? zuordnen$/ });
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
    await apply.scrollIntoViewIfNeeded();
    const bounds = await apply.boundingBox();
    assert.ok(bounds && bounds.y >= -1 && bounds.y + bounds.height <= 361,
      `Detail action is unreachable at keyboard-height viewport: ${JSON.stringify(bounds)}`);
    assert.ok(await apply.evaluate((element) => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return element.contains(document.elementFromPoint(x + width / 2, y + height / 2));
    }), 'Another element covers the detail action at keyboard-height viewport');
    await apply.focus();
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
  await dialog.getByText(`${selection.length} von ${proposal.face_ids.length} ausgewählt`, { exact: true }).waitFor();
}

async function denseJourneyReview(browser, errors) {
  const page = await newPage(browser, 1420, 1000, errors);
  const scope = page.getByLabel('Verlaufsbereich', { exact: true });
  const results = page.locator('#gps-map-results');
  try {
    await page.goto(`${webBase}/#/weltkarte`);
    await page.getByRole('button', { name: 'Verlauf', exact: true }).click();
    const search = results.getByRole('searchbox', { name: 'Stoppliste durchsuchen' });
    await search.fill('Paris');
    await results.getByRole('button', { name: /Bei Paris/ }).first().waitFor();
    assert.ok(await results.getByRole('button', { name: /Bei Paris/ }).count() >= 8);
    await search.clear();
    await scope.selectOption('Europa');
    await page.getByLabel('Zeitraum', { exact: true }).selectOption('custom');
    await page.getByLabel('Von', { exact: true }).fill('2024-07-13');
    const delivery = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname.endsWith('/map/journey') && url.searchParams.get('to_date') === '2024-07-13'
        && url.searchParams.has('south') && response.ok();
    });
    await page.getByLabel('Bis', { exact: true }).fill('2024-07-13');
    const journey = await (await delivery).json();
    assert.deepEqual(journey.stops.map(({ image_ids, section }) => [image_ids.length, section]), [[5, 1], [5, 1], [5, 2]]);
    assert.equal(journey.connections.length, 1, 'Area filter bridged the omitted Marrakech visit');
    await page.getByText('3 Stopps · 15 Fotos', { exact: true }).waitFor();
    await results.getByText('Neuer Abschnitt im gewählten Bereich', { exact: true }).waitFor();
    const map = page.getByRole('group', { name: 'Weltkarte mit Fotostandorten' });
    await map.press('ArrowRight');
    assert.equal(await scope.inputValue(), 'Europa', 'Panning changed the fixed area filter');
    await page.getByRole('button', { name: 'Filterbereich zeigen' }).click();
    await results.getByRole('button', { name: /Bei Lyon/ }).click();
    await results.getByRole('heading', { name: 'Fotos von Stopp 2' }).waitFor();
    await results.getByText('5 Fotos', { exact: true }).waitFor();
    await screenshot(page, 'reise-europa');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('group', { name: 'Kartenansicht' }).getByRole('button', { name: 'Stopps', exact: true }).click();
    await page.getByRole('button', { name: 'Fotobuchbild gestalten' }).click();
    const album = page.getByRole('dialog', { name: 'Fotobuchbild erstellen' });
    await album.getByLabel('Layout', { exact: true }).selectOption('list');
    await album.getByLabel('Format', { exact: true }).selectOption({ label: 'Eigene Größe' });
    await album.getByLabel('Breite (px)', { exact: true }).fill('2400');
    await album.getByLabel('Höhe (px)', { exact: true }).fill('1600');
    await album.getByText('Bildgröße und Auflösung', { exact: true }).click();
    await album.getByLabel('Druckauflösung', { exact: true }).selectOption('240');
    await album.getByText('Eigene Farben', { exact: true }).click();
    await album.getByLabel('Akzentfarbe auswählen', { exact: true }).fill('#6b2540');
    const preview = album.locator('.gps-album-dialog__preview[aria-busy="false"] img');
    await preview.waitFor();
    const artwork = await preview.evaluate((image) => fetch(image.src).then((response) => response.text()));
    assert.match(artwork, /#6b2540/);
    assert.match(artwork, /Bei Paris/);
    assert.match(artwork, /Bei Marseille/);
    assert.doesNotMatch(artwork, /Marrakech/);
    assert.equal(await album.evaluate((element) => element.scrollWidth - element.clientWidth), 0);
    const download = page.waitForEvent('download');
    await album.getByRole('button', { name: 'PNG speichern', exact: true }).click();
    const png = fs.readFileSync(await (await download).path());
    assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], [2400, 1600]);
    assert.equal(png.readUInt32BE(png.indexOf(Buffer.from('pHYs')) + 4), 9449);
    await page.keyboard.press('Escape');
    await album.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Kartenfilter', exact: true }).click();
    await scope.selectOption('Asien');
    await page.getByRole('group', { name: 'Kartenansicht' }).getByRole('button', { name: 'Stopps', exact: true }).click();
    await results.getByText('Keine Stopps in diesem Bereich und Zeitraum.', { exact: true }).waitFor();
    await results.getByRole('button', { name: 'Bereich entfernen' }).click();
    await page.getByText('4 Stopps · 20 Fotos', { exact: true }).waitFor();
    await page.setViewportSize({ width: 1420, height: 1000 });
    await scope.selectOption('Europa');
    await page.getByRole('button', { name: 'Bereich wählen', exact: true }).click();
    await map.scrollIntoViewIfNeeded();
    const box = await map.boundingBox();
    const [mapX, mapY, mapWidth, mapHeight] = (await map.getAttribute('viewBox')).split(' ').map(Number);
    const lyonX = box.x + box.width * ((4.8357 + 180) / 360 * 1000 - mapX) / mapWidth;
    const lyonY = box.y + box.height * ((90 - 45.764) / 180 * 500 - mapY) / mapHeight;
    await page.mouse.move(lyonX - 16, lyonY - 16);
    await page.mouse.down();
    await page.mouse.move(lyonX + 16, lyonY + 16, { steps: 8 });
    await page.mouse.up();
    await page.getByText('1 Stopp · 5 Fotos', { exact: true }).waitFor();
    assert.equal(await scope.inputValue(), 'custom');
    await scope.selectOption('Europa');
    await page.getByLabel('Bis', { exact: true }).fill('2026-09-22');
    await page.getByLabel('Von', { exact: true }).fill('2026-09-22');
    await page.getByLabel('Stoppabstand', { exact: true }).selectOption('city');
    await page.getByText('7 Stopps · 35 Fotos', { exact: true }).waitFor();
    await results.getByRole('list', { name: 'Chronologische Stopps' }).getByRole('button').nth(1).click();
    const inferredPhoto = results.getByRole('button', { name: 'Reise-09-02-3-Stephansplatz.jpg öffnen' });
    await inferredPhoto.waitFor();
    const includeInferred = results.getByRole('checkbox', { name: 'Passende Fotos ohne Standort einbeziehen' });
    await includeInferred.uncheck();
    await page.getByText('7 Stopps · 34 Fotos', { exact: true }).waitFor();
    await inferredPhoto.waitFor({ state: 'hidden' });
    await includeInferred.check();
    await inferredPhoto.waitFor();
    await results.getByText('Nach Aufnahmezeit zugeordnet', { exact: true }).waitFor();
    await results.getByRole('button', { name: 'Stopp auf Karte zeigen', exact: true }).click();
    const cityView = (await map.getAttribute('viewBox')).split(' ').map(Number);
    assert.ok(cityView[2] < 1, 'City stop did not zoom below a district-wide view');
    await page.getByRole('button', { name: 'Bereich wählen', exact: true }).click();
    await page.getByRole('button', { name: 'Sichtbaren Bereich wählen', exact: true }).click();
    await page.getByText('5 Stopps · 25 Fotos', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Fotobuchbild gestalten' }).click();
    await album.getByText('Route gestalten', { exact: true }).click();
    const suggestion = album.getByRole('button', { name: /Zu Fuß für Etappe .* übernehmen/ }).first();
    await suggestion.waitFor();
    const draftLeg = album.getByRole('group', { name: 'Verkehrsmittel pro Etappe' }).getByRole('combobox').first();
    assert.equal(await draftLeg.inputValue(), '', 'Transport suggestion was applied without confirmation');
    await suggestion.click();
    assert.equal(await draftLeg.inputValue(), 'walk');
    await album.locator('.gps-album-dialog__preview[aria-busy="false"] img').waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Entwurf verwerfen', exact: true }).click();
    await album.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Bereichsfilter entfernen' }).click();
    await page.getByText('7 Stopps · 35 Fotos', { exact: true }).waitFor();
    await page.getByText('Orte aus meinen Fotos', { exact: true }).click();
    await page.getByRole('searchbox', { name: 'Fotoorte suchen' }).fill('Berlin');
    await page.getByRole('button', { name: 'Berlin als Heimatort wählen' }).click();
    await page.getByText('Reisen entdecken', { exact: true }).click();
    const discovery = page.locator('details').filter({ has: page.getByText('Reisen entdecken', { exact: true }) });
    await discovery.getByRole('button', { name: /5 Stopps/ }).click();
    await page.getByText('5 Stopps · 25 Fotos', { exact: true }).waitFor();
    await discovery.getByLabel('Anreise einbeziehen').check();
    await page.getByText('6 Stopps · 30 Fotos', { exact: true }).waitFor();
    await discovery.getByLabel('Rückreise einbeziehen').check();
    await page.getByText('7 Stopps · 35 Fotos', { exact: true }).waitFor();
    await discovery.getByLabel('Rückreise einbeziehen').uncheck();
    await discovery.getByLabel('Anreise einbeziehen').uncheck();
    await page.getByText('5 Stopps · 25 Fotos', { exact: true }).waitFor();
    await discovery.getByRole('button', { name: 'Als Reisealbum speichern' }).click();
    await page.getByLabel('Albumname', { exact: true }).fill('Wien zu Fuß');
    await page.getByRole('button', { name: 'Album erstellen', exact: true }).click();
    await page.getByLabel('Album', { exact: true }).selectOption({ label: 'Wien zu Fuß' });
    await page.getByText('5 Stopps · 25 Fotos', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Fotobuchbild gestalten' }).click();
    await album.getByLabel('Titel', { exact: true }).fill('Wiener Spaziergang');
    await album.getByRole('button', { name: 'Entwurf im Album speichern', exact: true }).click();
    await album.getByText('Entwurf im Album gespeichert.', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await album.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Fotobuchbild gestalten' }).click();
    assert.equal(await album.getByLabel('Titel', { exact: true }).inputValue(), 'Wiener Spaziergang');
    console.log('PASS dense journey fixture: 9 trips, inferred photo, city and transport hints, separate transfers, journey album and draft reload');
  } finally {
    await page.close();
  }
}

async function backgroundActivitiesFlow(browser, errors) {
  const page = await newPage(browser, 1280, 800, errors);
  try {
    await page.addInitScript(() => {
      window.EventSource = class ActivityEvents extends EventTarget {
        constructor() { super(); window.activityTestEvents = this; }
        close() {}
      };
    });
    await page.goto(`${webBase}/#/bilder`);
    const trigger = page.getByRole('button', { name: /^Aktivitäten/ });
    const popover = page.getByRole('region', { name: 'Aktivitäten', exact: true });
    await trigger.hover();
    await trigger.focus();
    assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
    const publish = (status, processed, unavailable) => page.evaluate(({ status, processed, unavailable }) => {
      window.activityTestEvents.dispatchEvent(new MessageEvent('background-activities', {
        data: JSON.stringify({ activities: [{ id: 'metadata-test', kind: 'photo-metadata', status, processed,
          total: 10, unavailable, finished_at: status === 'running' ? null : new Date().toISOString() }] }),
      }));
    }, { status, processed, unavailable });
    await publish('running', 3, 0);
    await page.getByRole('button', { name: 'Aktivitäten (1 Aufgabe)', exact: true }).waitFor();
    assert.equal(await popover.count(), 0, 'Background task opened the panel without a click');
    await trigger.press('Enter');
    const task = popover.locator('article').filter({ hasText: 'Fotostandorte und Aufnahmezeiten prüfen' });
    await task.getByText('3 von 10 Fotos geprüft', { exact: true }).waitFor();
    assert.equal(await task.getByRole('progressbar').getAttribute('aria-valuenow'), '30');
    assert.equal(await task.getByRole('button').count(), 0);
    await publish('completed', 10, 2);
    await task.getByText('10 geprüfte Fotos · 2 Dateien nicht erreichbar', { exact: true }).waitFor();
    await task.getByRole('button', { name: /aus der Historie löschen/ }).focus();
    await page.keyboard.press('Escape');
    assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
    assert.ok(await trigger.evaluate((item) => item === document.activeElement));
    console.log('PASS background activities: quiet trigger, accessible progress, completion and focus return');
  } finally {
    await page.close();
  }
}

async function freshInstallFlow(browser, errors) {
  const page = await newPage(browser, 320, 680, errors);
  try {
    await page.route('**/api/changelog/current', (route) => route.fulfill({
      json: { seen: false, fresh_install: true, versions: [{ version: '0.17.1', sections: [] }] },
    }));
    await page.route('**/api/changelog/current/acknowledge', (route) => route.fulfill({ json: { seen: true } }));
    const acknowledgement = page.waitForResponse((response) => response.url().endsWith('/api/changelog/current/acknowledge') && response.ok());
    await page.goto(`${webBase}/#/bilder`);
    await page.getByRole('heading', { name: 'Bilder', exact: true }).waitFor();
    await acknowledgement;
    await page.waitForFunction(() => !document.querySelector('[aria-labelledby="whats-new-title"]'));
    assert.equal(await page.getByRole('dialog', { name: /Neu in Face Manager/ }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 320);
    console.log('PASS first start at 320px: opens the library and dismisses release notes');
  } finally {
    await page.close();
  }
}

async function suggestionRefreshFlow(browser, errors) {
  const page = await newPage(browser, 1580, 740, errors);
  const proposals = Array.from({ length: 15 }, (_, index) => ({
    cluster_id: index + 1, person_id: 1, person_name: `Person ${index + 1}`,
    confidence: 0.95, support_count: 1, face_count: 1, recommended: true,
    proposal_token: `proposal-${index + 1}`, face_ids: [601],
    preview_face_ids: [601], reference_face_ids: [],
  }));
  let pending = false;
  let accepted;
  try {
    await page.route('**/api/person-suggestions', (route) => route.fulfill({
      headers: { 'X-Person-Suggestions-Pending': pending ? '1' : '0', 'X-Assignment-Library-Id': 'fixture' },
      json: pending ? [] : proposals.filter((item) => item.cluster_id !== accepted),
    }));
    await page.route('**/api/review-suggestions', (route) => route.fulfill({ json: [] }));
    await page.route(/\/api\/faces\/601\/crop\?/, (route) => route.fulfill({
      contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>',
    }));
    await page.route('**/api/person-suggestions/accept', (route) => {
      accepted = route.request().postDataJSON().assignments[0].cluster_id;
      pending = true;
      return route.fulfill({ json: { accepted_count: 1 } });
    });

    await page.goto(`${webBase}/#/gesichter-pruefen`);
    const counter = page.getByRole('button', { name: /^Vorschläge / }).locator('b');
    const row = page.getByRole('button', { name: 'Vorschlag für Person 14 prüfen', exact: true });
    const survivor = page.getByRole('button', { name: 'Vorschlag für Person 15 prüfen', exact: true });
    const detail = page.getByRole('region', { name: 'Gruppe prüfen' });
    const action = detail.getByRole('button', { name: /Gesicht(?:er)? zuordnen$/ });
    const scroller = page.locator('[data-workspace-page="review"] .page-content');
    await row.scrollIntoViewIfNeeded();
    const startingScroll = await scroller.evaluate((element) => element.scrollTop);
    assert.ok(startingScroll > 100);
    await row.click();
    await action.and(detail.locator('button:enabled')).waitFor();
    const pendingResponse = page.waitForResponse((response) => response.url().endsWith('/api/person-suggestions')
      && response.headers()['x-person-suggestions-pending'] === '1');
    await action.click();
    await (await pendingResponse).finished();
    await page.getByText('Vorschläge werden aktualisiert …', { exact: true }).waitFor();
    await survivor.waitFor();
    assert.equal(await counter.textContent(), '14');
    assert.equal(await page.getByRole('heading', { name: 'Keine Vorschläge offen' }).count(), 0);
    await page.waitForFunction(() => document.querySelector('[data-workspace-page="review"] .page-content').scrollTop > 100);
    await survivor.click();
    await action.waitFor();
    assert.ok(await action.isDisabled(), 'A recalculation did not block the next focused suggestion');

    pending = false;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await action.and(detail.locator('button:enabled')).waitFor();
    await detail.getByRole('button', { name: 'Zu den Vorschlägen' }).click();
    assert.equal(await counter.textContent(), '14');
    assert.equal(await row.count(), 0);
    await page.waitForFunction(() => document.querySelector('[data-workspace-page="review"] .page-content').scrollTop > 100);
    console.log('PASS suggestion refresh: completed card removed, pending list retained, count and scroll stable');
  } finally {
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const flows = process.env.UI_SMOKE_JOURNEY_ONLY === '1' ? {
      denseJourneyReview,
      backgroundActivitiesFlow,
    } : {
      workspaceNavigation: (browser, errors) => workspaceNavigationFlow(browser, errors, newPage, webBase, true),
      durablePreferencesFlow,
      mapWithoutStorageFlow,
      onlineMapDetailsFlow,
      previewFlow,
      firstAssignmentFlow,
      collectSourcesFlow,
      localRecoveryFlow,
      androidDatabaseExport: (browser, errors) => androidDatabaseExportFlow(browser, errors, newPage, webBase),
      mapFlow,
      mapSelectionAndJourneyFlow,
      archiveAssignment: (browser, errors) => archiveAssignmentFlow(browser, errors, newPage, webBase),
      compactReview: (browser, errors) => compactReviewFlow(browser, errors, newPage, webBase),
      pagedLibraryFacesFlow,
      liveWorkflowFlow,
      personDirectory: (browser, errors) => personDirectoryFlow(browser, errors, newPage, webBase),
      existingFlow,
      settingsFlow,
      previewCacheSettingsFlow,
      responsiveFlow,
      reviewInboxFlow,
      suggestionRefreshFlow,
      albumLibrary: (browser, errors) => albumLibraryFlow(browser, errors, newPage, webBase),
      importSources: (browser, errors) => importSourcesFlow(browser, errors, newPage, webBase),
      helpTour: (browser, errors) => helpTourFlow(browser, errors, newPage, webBase),
      mapFacelift: (browser, errors) => mapFaceliftFlow(browser, errors, newPage, webBase, apiBase, screenshot),
      faceliftWorkspaces: (browser, errors) => faceliftWorkspacesFlow(browser, errors, newPage, webBase),
      freshInstallFlow,
      manualFace: (browser, errors) => manualFaceFlow(browser, errors, newPage, webBase, apiBase, screenshot),
    };
    for (const [name, flow] of Object.entries(flows)) {
      console.log(`START browser smoke: ${name}`);
      await flow(browser, errors);
    }
    assert.deepEqual(errors, [], `Browser page errors: ${errors.join('; ')}`);
    console.log('PASS browser smoke: no uncaught page errors');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
