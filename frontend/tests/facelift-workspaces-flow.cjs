const assert = require('node:assert/strict');

module.exports = async function faceliftWorkspacesFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1212, 799, errors);
  try {
    await checkPhotoFilter(page, webBase);
    await checkPeopleHistory(page, webBase);
    console.log('PASS facelift workspaces: stale photo guard, archived overlay route and persisted undo/redo');
  } finally {
    await page.close();
  }
};

async function checkPhotoFilter(page, webBase) {
  await page.goto(`${webBase}/#/bilder`);
  const gridCards = page.locator('[data-image-id]');
  await gridCards.first().waitFor();
  const person = page.locator('.filter-bar__chips button.person-filter__chip').filter({ hasText: /Beispiel/ }).first();
  await person.waitFor();
  let release;
  const responseGate = new Promise((resolve) => { release = resolve; });
  let intercept = true;
  await page.route('**/api/images?**', async (route) => {
    if (!intercept || !new URL(route.request().url()).searchParams.has('persons')) return route.continue();
    await responseGate;
    await route.fulfill({ status: 503, json: { detail: 'Test: Verbindung unterbrochen' } });
  });
  try {
    await person.click();
    await page.getByText('Bilder werden aktualisiert …', { exact: true }).waitFor();
    assert.ok(await gridCards.first().locator('.image-gallery-card__open').isDisabled());
    assert.ok(await page.getByRole('button', { name: 'Fotos auswählen', exact: true }).isDisabled());
  } finally {
    release();
  }
  await page.getByText(/Der letzte geladene Stand bleibt sichtbar/).waitFor();
  assert.ok(await gridCards.first().locator('.image-gallery-card__open').isDisabled());
  const retry = page.getByRole('button', { name: 'Erneut laden', exact: true });
  await retry.focus();
  intercept = false;
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => Boolean(document.querySelector('[data-image-id] .image-gallery-card__open:not(:disabled)')));
  await page.getByRole('button', { name: 'Filter zurücksetzen', exact: true }).click();
  const choosePhotos = page.getByRole('button', { name: 'Fotos auswählen', exact: true });
  await choosePhotos.click();
  const selectedCard = gridCards.first();
  const selectedId = await selectedCard.getAttribute('data-image-id');
  await selectedCard.locator('.image-gallery-card__open').click();
  await page.getByText('1 ausgewählt', { exact: true }).waitFor();
  const sidebar = page.getByRole('navigation', { name: 'Hauptnavigation' });
  await sidebar.getByRole('button', { name: 'Personen', exact: true }).click();
  await sidebar.getByRole('button', { name: 'Bilder', exact: true }).click();
  const sameCard = page.locator(`[data-image-id="${selectedId}"] .image-gallery-card__open`);
  await sameCard.and(page.locator('[aria-pressed="true"]')).waitFor();
  const nav = page.getByRole('navigation', { name: 'Ansichtsverlauf' });
  await nav.getByRole('button', { name: 'Zur Ausgangsansicht' }).click();
  await choosePhotos.waitFor();
  assert.equal(await page.getByText('1 ausgewählt', { exact: true }).count(), 0);
  await nav.getByRole('button', { name: 'Zurück', exact: true }).click();
  await sameCard.and(page.locator('[aria-pressed="true"]')).waitFor();

  for (const holdRetry of [true, false]) {
    const photosPane = page.locator('[data-workspace-page="people"] > .page-content');
    const savedScroll = await photosPane.evaluate((pane) => {
      pane.scrollTop = 350;
      return pane.scrollTop;
    });
    assert.ok(savedScroll > 0);
    const waitForSavedScroll = () => page.waitForFunction((scroll) => Math.abs(
      document.querySelector('[data-workspace-page="people"] > .page-content').scrollTop - scroll,
    ) < 3, savedScroll);
    await sidebar.getByRole('button', { name: 'Karte', exact: true }).click();
    let releaseResume;
    let releaseRetry;
    const resumeGate = new Promise((resolve) => { releaseResume = resolve; });
    const retryGate = new Promise((resolve) => { releaseRetry = resolve; });
    const matchesResume = (url) => url.pathname === '/api/images' &&
      url.searchParams.get('offset') === '0' && Number(url.searchParams.get('limit')) > 1;
    let attempts = 0;
    await page.route(matchesResume, async (route) => {
      await (attempts++ === 0 ? resumeGate : retryGate);
      await route.continue();
    }, { times: holdRetry ? 2 : 1 });
    try {
      const pendingResume = page.waitForRequest((request) => matchesResume(new URL(request.url())));
      await nav.getByRole('button', { name: 'Zurück', exact: true }).click();
      await pendingResume;
      assert.ok(await sameCard.isDisabled());
      await waitForSavedScroll();
      const interruptedScroll = await photosPane.evaluate(async (pane) => {
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        pane.scrollTop = 0;
        return pane.scrollTop;
      });
      assert.equal(interruptedScroll, 0);
      await nav.getByRole('button', { name: 'Vorwärts', exact: true }).click();
      await page.locator('[data-workspace-page="map"].page-pane--active').waitFor();
      releaseResume();

      const pendingRetry = page.waitForRequest((request) => matchesResume(new URL(request.url())));
      await nav.getByRole('button', { name: 'Zurück', exact: true }).click();
      await pendingRetry;
      if (holdRetry) {
        assert.ok(await sameCard.isDisabled());
        // The previous visual offset was zero when the interrupted view was
        // left. The saved offset must already win while the same grid is shown.
        await waitForSavedScroll();
      }
    } finally {
      releaseResume();
      releaseRetry();
    }
    await sameCard.and(page.locator('[aria-pressed="true"]')).waitFor();
    await waitForSavedScroll();
  }
}

async function checkPeopleHistory(page, webBase) {
  await page.goto(`${webBase}/#/gesichter-pruefen/ohne-namen`);
  const firstFace = page.locator('.cluster-group-card--active .face-grid-tile').first();
  await firstFace.waitFor();
  const checkbox = firstFace.getByRole('checkbox');
  const faceId = Number((await checkbox.getAttribute('aria-label')).match(/Gesicht (\d+) auswählen/)[1]);
  const openOriginal = firstFace.getByRole('button', { name: `Originalfoto zu Gesicht ${faceId} öffnen` });
  await openOriginal.press('Enter');
  const gallery = page.getByRole('dialog', { name: 'Bildansicht' });
  await gallery.waitFor();
  assert.equal(await checkbox.isChecked(), false);
  await page.keyboard.press('Escape');
  await gallery.waitFor({ state: 'hidden' });
  await openOriginal.and(page.locator(':focus')).waitFor();
  await checkbox.check({ force: true });
  const owner = page.getByRole('combobox', { name: 'Person suchen oder neuen Namen eingeben' });
  await owner.fill('Facelift Arbeitsprobe');
  const actionWritten = page.waitForResponse('**/api/faces/batch');
  await page.getByRole('button', { name: '„Facelift Arbeitsprobe“ anlegen und zuweisen', exact: true }).click();
  const saved = await actionWritten;
  assert.equal(saved.status(), 200, await saved.text());
  const assignment = (await saved.json()).history_action;
  assert.equal(assignment.face_count, 1);
  const undo = page.getByRole('button', { name: 'Rückgängig', exact: true });
  const redo = page.getByRole('button', { name: 'Wiederholen', exact: true });
  await undo.click();
  await page.getByRole('status').filter({ hasText: 'Rückgängig gemacht: 1 Gesicht „Facelift Arbeitsprobe“ zugeordnet.' }).waitFor();
  await redo.click();
  await page.getByRole('status').filter({ hasText: 'Wiederholt: 1 Gesicht „Facelift Arbeitsprobe“ zugeordnet.' }).waitFor();
  await undo.click();
  await page.getByRole('status').filter({ hasText: 'Rückgängig gemacht: 1 Gesicht „Facelift Arbeitsprobe“ zugeordnet.' }).waitFor();

  await page.reload();
  const restoredCheck = page.getByRole('checkbox', { name: `Gesicht ${faceId} auswählen`, exact: true });
  await restoredCheck.press('Space');
  await page.getByText('1 Gesicht markiert', { exact: true }).waitFor();
  await page.getByText('Aussortieren oder lösen', { exact: true }).click();
  const archiveWritten = page.waitForResponse('**/api/faces/batch');
  await page.getByRole('button', { name: 'Als unbekannt aussortieren', exact: true }).click();
  const archived = await archiveWritten;
  assert.equal(archived.status(), 200, await archived.text());
  assert.equal((await archived.json()).history_action.action, 'mark_unknown_person');

  await page.goto(`${webBase}/#/bilder`);
  await page.getByText('Ausgesonderte Gesichter', { exact: true }).click();
  await page.getByRole('button', { name: 'Als unbekannt aussortiert', exact: true }).click();
  const badge = page.locator(`[data-face-id="${faceId}"]`).getByRole('button', { name: 'Als unbekannt aussortiert im Archiv öffnen' });
  await badge.click();
  await page.waitForURL(/#\/gesichter-pruefen\/kategorie\/unknown_person\/gruppe\/\d+$/);
  await page.locator('.cluster-group-card--active')
    .getByRole('checkbox', { name: `Gesicht ${faceId} auswählen`, exact: true }).waitFor();
  await undo.click();
  await page.getByRole('status').filter({ hasText: 'Rückgängig gemacht: 1 Gesicht als unbekannt aussortiert.' }).waitFor();
}
