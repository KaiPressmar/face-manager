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
  await page.addInitScript((base) => { window.FACE_MANAGER_API_BASE = base; }, apiBase);
  page.on('pageerror', (error) => errors.push(error.message));
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

async function mapFlow(browser, errors) {
  const page = await newPage(browser, 1440, 1000, errors);
  const waitForCount = (count) => page.waitForFunction(
    (expected) => document.querySelector('.gps-page__results-head > span')?.textContent === `${expected} Fotos`, count,
  );
  try {
    await page.goto(`${webBase}/#/weltkarte`);
    await waitForCount(85);
    assert.equal(await page.locator('.gps-page__photo').count(), 36);
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

    await page.locator('.gps-map__marker').filter({ hasText: '15' }).first().press('Enter');
    await waitForCount(15);
    const galleryTile = page.locator('.gps-page__photo').first();
    await galleryTile.click();
    const gallery = page.getByRole('dialog', { name: 'Bildansicht' });
    await gallery.waitFor();
    await assertFocusInDialog(page, gallery);
    await assertTabTrap(page, gallery);
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

    await page.getByRole('button', { name: 'Zurücksetzen', exact: true }).click();
    await waitForCount(85);
    await page.route('**/api/map/points?**', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Testfehler Karte"}' }));
    await page.getByLabel('Kartenausschnitt', { exact: true }).selectOption('Europa');
    await page.getByRole('alert').filter({ hasText: 'Testfehler Karte' }).waitFor();
    await page.unroute('**/api/map/points?**');
    await page.getByRole('button', { name: 'Erneut versuchen', exact: true }).click();
    await waitForCount(29);
    await page.getByRole('alert').waitFor({ state: 'hidden' });

    await page.getByRole('button', { name: 'Zurücksetzen', exact: true }).click();
    await waitForCount(85);
    await page.route('**/api/map/images?**', async (route) => {
      if (new URL(route.request().url()).searchParams.get('offset') === '36') {
        await new Promise((resolve) => setTimeout(resolve, 800));
      }
      await route.continue().catch(() => {});
    });
    await page.getByRole('button', { name: 'Weitere Fotos laden', exact: true }).click();
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
    await changelogTrigger.click();
    const changelog = page.getByRole('dialog', { name: 'Änderungsprotokoll' });
    await changelog.waitFor();
    await assertFocusInDialog(page, changelog);
    await assertTabTrap(page, changelog);
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
    await page.getByRole('button', { name: /Aussortiert.*Unbekannt/ }).click();
    await screenshot(page, 'existing-flows');
    console.log('PASS existing flows: library/folder retry, person filter, import, rename, settings, review tabs');
  } finally {
    await page.close();
  }
}

async function settingsFlow(browser, errors) {
  const page = await newPage(browser, 1440, 1000, errors);
  try {
    await page.goto(`${webBase}/#/einstellungen/darstellung`);
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
    await mapFlow(browser, errors);
    await existingFlow(browser, errors);
    await settingsFlow(browser, errors);
    await responsiveFlow(browser, errors);
    assert.deepEqual(errors, [], `Browser page errors: ${errors.join('; ')}`);
    console.log('PASS browser smoke: no uncaught page errors');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
