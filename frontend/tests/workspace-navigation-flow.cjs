const assert = require('node:assert/strict');

const marker = (page) => page.evaluate(() => window.history.state.faceManagerWorkspace);
const waitForEntry = (page, id) => page.waitForFunction((entryId) => window.history.state?.faceManagerWorkspace?.id === entryId, id);

module.exports = async function workspaceNavigationFlow(browser, errors, newPage, webBase, storageBlocked = false) {
  const page = await newPage(browser, 1212, 799, errors);
  try {
    if (storageBlocked) await page.addInitScript(() => {
      Object.defineProperty(window, 'sessionStorage', {
        get() { throw new DOMException('Session storage is disabled', 'SecurityError'); },
      });
    });
    await page.route('**/api/changelog/current', (route) => route.fulfill({ json: {
      seen: false, fresh_install: true,
      versions: [{ version: '1.0.0', date: null, sections: [{ title: 'Neu', items: ['Neue Funktionen'] }] }],
    } }));
    await page.goto(`${webBase}/#/bilder`);
    const controls = page.getByRole('navigation', { name: 'Ansichtsverlauf' });
    const sidebar = page.getByRole('navigation', { name: 'Hauptnavigation' });
    const back = controls.getByRole('button', { name: 'Zurück', exact: true });
    const forward = controls.getByRole('button', { name: 'Vorwärts', exact: true });
    const reset = controls.getByRole('button', { name: 'Zur Ausgangsansicht' });
    await page.getByRole('button', { name: 'Einführung starten' }).waitFor();
    assert.equal(await page.getByRole('dialog').count(), 0, 'Fresh installation displayed automatic upgrade notes');
    const first = await marker(page);
    assert.equal(await back.isDisabled(), true);
    const pictures = page.locator('[data-workspace-page="people"] > .page-content');
    await pictures.locator('[data-image-id]').first().waitFor();
    const skipLink = page.getByRole('link', { name: 'Zum Hauptinhalt', exact: true });
    await skipLink.press('Enter');
    assert.ok(await page.getByRole('main').evaluate((element) => element === document.activeElement));
    assert.equal(new URL(page.url()).hash, '#/bilder', 'Skipping navigation changed the current route');
    assert.equal((await marker(page)).id, first.id, 'Skipping navigation added a history entry');
    const savedScroll = await pictures.evaluate((pane) => { pane.scrollTop = 400; return pane.scrollTop; });
    assert.ok(savedScroll > 150, 'Fixture does not have enough photos to test scroll restoration');
    await reset.click();
    const second = await marker(page);
    assert.notEqual(first.id, second.id);
    assert.equal(new URL(page.url()).hash, '#/bilder');
    await page.waitForFunction(() => document.querySelector('[data-workspace-page="people"] > .page-content').scrollTop === 0);
    await back.click();
    await waitForEntry(page, first.id);
    await page.waitForFunction((top) => Math.abs(document.querySelector('[data-workspace-page="people"] > .page-content').scrollTop - top) <= 2, savedScroll);
    await page.keyboard.press('Alt+ArrowRight');
    await waitForEntry(page, second.id);

    await sidebar.getByRole('button', { name: 'Personen', exact: true }).click();
    const review = await marker(page);
    await sidebar.getByRole('button', { name: 'Karte', exact: true }).click();
    const map = await marker(page);
    await back.click();
    await waitForEntry(page, review.id);
    await page.reload();
    await waitForEntry(page, review.id);
    await forward.waitFor();
    assert.equal(await forward.isEnabled(), true, 'Reload discarded Forward in the middle of history');
    const help = page.getByRole('button', { name: 'Hilfe', exact: true });
    await help.click();
    const dialog = page.getByRole('dialog', { name: 'Erste Schritte' });
    await dialog.waitFor();
    await page.evaluate(() => window.history.back());
    await dialog.waitFor({ state: 'hidden' });
    await waitForEntry(page, review.id);
    await page.waitForFunction(() => document.activeElement?.id === 'app-help-trigger');
    assert.equal(await forward.isEnabled(), true, 'Overlay Back discarded existing Forward');
    await forward.click();
    await waitForEntry(page, map.id);
    assert.equal(await dialog.count(), 0, 'Forward reopened a dismissed modal');

    await page.locator('#gps-map-results').getByRole('button', { name: 'In Bilder öffnen', exact: true }).click();
    await page.waitForFunction(() => history.state?.geoSelection?.bounds);
    const selection = await marker(page);
    await page.reload();
    await waitForEntry(page, selection.id);
    assert.equal(new URL(page.url()).hash, '#/bilder/auswahl');
    await page.getByRole('button', { name: 'Kartenausschnitt entfernen' }).waitFor();
    await back.click();
    await waitForEntry(page, map.id);

    await help.click();
    await dialog.waitFor();
    assert.equal(await page.evaluate(() => window.faceManagerBack()), true);
    await dialog.waitFor({ state: 'hidden' });
    assert.equal((await marker(page)).id, map.id);
    assert.equal(await page.evaluate(() => window.faceManagerBack()), true);
    await waitForEntry(page, review.id);

    await page.setViewportSize({ width: 320, height: 740 });
    assert.equal(await controls.evaluate((nav) => nav.scrollWidth <= nav.clientWidth), true);
    console.log('PASS workspace history: same-route reset, keyboard, browser overlay Back and Forward, native bridge, fresh install and 320px');

    await page.setViewportSize({ width: 1212, height: 799 });
    if (storageBlocked) {
      await page.evaluate(() => { location.hash = '#/einstellungen/dateinamen'; });
      await page.waitForFunction(() => history.state?.settingsSection === 'dateinamen');
      assert.equal(await forward.isEnabled(), false, 'Direct navigation did not discard the old forward branch');
      await page.evaluate(() => history.go(-2));
      await waitForEntry(page, second.id);
      await page.reload();
      await forward.click();
      await waitForEntry(page, review.id);
      await page.reload();
      assert.equal(await forward.isEnabled(), true, 'Reload discarded a forward entry created by a direct hash change');
      await forward.click();
      await page.waitForFunction(() => location.hash === '#/einstellungen/dateinamen');
    }
  } finally {
    await page.close();
  }
  await libraryIdentityFlow(browser, errors, newPage, webBase, storageBlocked);
};

async function libraryIdentityFlow(browser, errors, newPage, webBase, storageBlocked) {
  const page = await newPage(browser, 1212, 799, errors);
  let library = 'workspace-library-a';
  let verification;
  let releaseVerification;
  try {
    await page.addInitScript((blocked) => {
      if (blocked) Reflect.deleteProperty(window, 'sessionStorage');
      window.EventSource = class WorkspaceTestEvents extends EventTarget {
        static CONNECTING = 0;
        readyState = 1;
        constructor() {
          super();
          window.workspaceTestEvents = this;
          setTimeout(() => this.onopen?.(), 0);
        }
        close() { this.readyState = 2; }
      };
    }, storageBlocked);
    await page.route('**/api/library-identity', async (route) => {
      if (verification) await verification;
      return route.fulfill({ json: { library_id: library } });
    });
    const waitForLibrary = (id) => page.waitForFunction((libraryId) => history.state?.faceManagerWorkspace?.libraryId === libraryId, id);
    await page.goto(`${webBase}/#/einstellungen/dateinamen`);
    await waitForLibrary(library);
    assert.equal(new URL(page.url()).hash, '#/einstellungen/dateinamen', 'A valid first direct route was dropped before identity handshake');

    const first = await marker(page);
    library = 'workspace-library-b';
    verification = new Promise((resolve) => { releaseVerification = resolve; });
    await page.reload();
    await page.getByText('Bibliothek wird geprüft …', { exact: true }).waitFor();
    assert.equal(await page.locator('[data-workspace-page="settings"]').count(), 0, 'Saved workspace mounted before identity verification');
    releaseVerification();
    verification = undefined;
    await waitForLibrary(library);
    await page.getByText('Die Bibliothek wurde gewechselt. Die Ansicht wurde zurückgesetzt.', { exact: true }).waitFor();
    assert.equal(new URL(page.url()).hash, '#/einstellungen');
    assert.equal((await marker(page)).generation, first.generation + 1);

    const publish = (id, reason) => page.evaluate(({ library_id, reason }) => {
      window.workspaceTestEvents.dispatchEvent(new MessageEvent('clusters', { data: JSON.stringify({ library_id, reason }) }));
    }, { library_id: id, reason });
    await publish(library, 'database_import');
    assert.equal((await marker(page)).generation, first.generation + 1, 'Same-library restore message reset state twice');
    library = 'workspace-library-c';
    await publish(library, 'background_progress');
    await waitForLibrary(library);
    assert.equal((await marker(page)).generation, first.generation + 2, 'A missed restore reason did not reset the old library');

    // A snapshot from a skipped intermediate library arrives after a newer
    // library was verified by GET. The intermediate ID was never observed.
    const recheck = page.waitForResponse((response) => response.url().endsWith('/api/library-identity') && response.ok());
    await publish('workspace-library-unseen-intermediate', 'database_import');
    assert.equal((await marker(page)).libraryId, library, 'An unseen delayed SSE replaced the verified library');
    const verified = await (await recheck).json();
    assert.equal(verified.library_id, library);
    assert.equal((await marker(page)).generation, first.generation + 2);
    await publish(library, 'settings_update');
    assert.equal((await marker(page)).libraryId, library, 'A stale hint retired the genuine current library');

    const sidebar = page.getByRole('navigation', { name: 'Hauptnavigation' });
    const history = page.getByRole('navigation', { name: 'Ansichtsverlauf' });
    const oldSettings = await marker(page);
    await sidebar.getByRole('button', { name: 'Karte', exact: true }).click();
    library = 'workspace-library-d';
    await page.evaluate(() => {
      const source = window.workspaceTestEvents;
      source.readyState = 0;
      source.onerror();
      source.readyState = 1;
      source.onopen();
    });
    await waitForLibrary(library);
    assert.equal((await marker(page)).generation, first.generation + 3);

    await page.locator('#gps-map-results').getByRole('button', { name: 'In Bilder öffnen', exact: true }).click();
    const selection = await marker(page);
    await page.reload();
    await page.getByRole('button', { name: 'Kartenausschnitt entfernen' }).waitFor();
    assert.equal((await marker(page)).generation, selection.generation);

    verification = new Promise((resolve) => { releaseVerification = resolve; });
    await page.reload();
    await page.getByText('Bibliothek wird geprüft …', { exact: true }).waitFor();
    await page.evaluate(() => window.history.go(-2));
    await waitForEntry(page, oldSettings.id);
    releaseVerification();
    verification = undefined;
    await page.getByText('Bibliothek wird geprüft …', { exact: true }).waitFor({ state: 'hidden' });
    assert.equal((await marker(page)).generation, selection.generation, 'Going Back before verification changed the active library generation');
    const forward = history.getByRole('button', { name: 'Vorwärts', exact: true });
    await forward.click();
    await page.waitForFunction(() => location.hash === '#/weltkarte');
    await forward.click();
    await page.getByRole('button', { name: 'Kartenausschnitt entfernen' }).waitFor();

    await history.getByRole('button', { name: 'Zurück', exact: true }).click();
    await page.waitForFunction(() => location.hash === '#/weltkarte');
    library = 'workspace-library-e';
    await publish(library, 'database_import');
    await waitForLibrary(library);
    await history.getByRole('button', { name: 'Vorwärts', exact: true }).click();
    await page.getByText('Dieser Verlaufseintrag gehört zur vorherigen Bibliothek. Die Übersicht ist geöffnet.', { exact: true }).waitFor();
    assert.equal(new URL(page.url()).hash, '#/bilder');
    console.log('PASS library identity: startup guard, valid deep link, duplicate/missed and out-of-order SSE, reconnect');
  } finally {
    releaseVerification?.();
    await page.close();
  }
}
