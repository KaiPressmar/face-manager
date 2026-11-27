const assert = require('node:assert/strict');

const reviewRoute = '#/gesichter-pruefen';
const personName = 'Anna Beispiel';

function action(scope, name) {
  const options = { name, exact: typeof name === 'string' };
  return scope.getByRole('button', options).or(scope.getByRole('link', options));
}

async function assertFits(page, workspace) {
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    'The person workspace introduced horizontal page scroll');
  assert.ok(await workspace.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
    'The person workspace does not fit its available width');
}

module.exports = async function personDirectoryFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1024, 600, errors, { colorScheme: 'dark' });
  let assignmentWrites = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/faces/batch') {
      assignmentWrites += 1;
    }
  });
  try {
    await page.addInitScript(() => {
      localStorage.setItem('face-manager:help-introduction-v1', 'seen');
      window.EventSource = class ObservedEventSource extends window.EventSource {
        constructor(...args) {
          super(...args);
          window.directoryTestEvents = this;
        }
      };
    });
    let signalPageRequest;
    let releasePage;
    const pageRequested = new Promise((resolve) => { signalPageRequest = resolve; });
    const pendingPage = new Promise((resolve) => { releasePage = resolve; });
    const directoryRequest = (url) => url.pathname === '/api/person-directory' && url.searchParams.get('search') === '';
    await page.route(directoryRequest, async (route) => {
      const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
      if (offset === 0) {
        return route.fulfill({ json: {
          items: [{ id: 1, name: personName, photo_count: 1 }, { id: 2, name: 'Ben Beispiel', photo_count: 1 }],
          offset: 0, limit: 2, total: 3, has_more: true,
        } });
      }
      if (signalPageRequest) {
        signalPageRequest();
        signalPageRequest = undefined;
        await pendingPage;
      }
      return route.fulfill({ json: {
        items: [{ id: 3, name: 'Carla Beispiel', photo_count: 1 }], offset: 2, limit: 2, total: 3, has_more: false,
      } });
    });
    await page.goto(`${webBase}/#/bilder`);
    const nav = page.getByRole('navigation', { name: 'Hauptnavigation' });
    const history = page.getByRole('navigation', { name: 'Ansichtsverlauf' });
    const back = action(history, 'Zurück');
    const forward = action(history, 'Vorwärts');
    const workspace = page.locator('[data-workspace-page="review"] > .page-content');
    await action(nav, 'Personen').press('Enter');
    await page.waitForFunction(() => window.history.state?.reviewView === 'directory');
    const directoryHash = new URL(page.url()).hash;
    assert.notEqual(directoryHash, reviewRoute, 'The main Personen entry bypassed the directory');

    const search = workspace.getByRole('searchbox', { name: 'Person suchen' });
    const portrait = action(workspace, /^Anna Beispiel\b/);
    await portrait.waitFor();
    await action(workspace, 'Weitere Personen laden').click();
    await pageRequested;
    const refresh = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return directoryRequest(url) && url.searchParams.get('offset') === '0';
    });
    try {
      await page.evaluate(() => window.directoryTestEvents.dispatchEvent(new MessageEvent('clusters', {
        data: JSON.stringify({ reason: 'assign_person' }),
      })));
      await refresh;
    } finally {
      releasePage();
    }
    await action(workspace, /^Carla Beispiel\b/).waitFor();
    await portrait.waitFor();
    await page.unroute(directoryRequest);
    await search.fill('Diese Person gibt es nicht');
    await workspace.getByText(/Keine Personen.*gefunden/).waitFor();
    assert.equal(await portrait.count(), 0, 'An unmatched search still showed a portrait');
    await action(workspace, 'Suche leeren').click();
    await portrait.waitFor();
    await search.fill('Anna');
    await portrait.waitFor();
    await action(workspace, /^Ben Beispiel\b/).waitFor({ state: 'hidden' });
    await assertFits(page, workspace);

    await portrait.press('Enter');
    await page.waitForURL(`**/${reviewRoute}/person/1`);
    const profileHash = new URL(page.url()).hash;
    const profileName = workspace.getByRole('heading', { name: personName, exact: true });
    await profileName.waitFor();
    const original = workspace.getByRole('button', { name: /^Foto öffnen:/ }).first();
    await original.press('Enter');
    const gallery = page.getByRole('dialog', { name: 'Bildansicht', exact: true });
    const fullPhoto = gallery.locator('img.gallery-image').first();
    await fullPhoto.waitFor();
    await page.waitForFunction((photo) => photo.complete && photo.naturalWidth > 0, await fullPhoto.elementHandle());
    assert.ok(await gallery.evaluate((element) => element.contains(document.activeElement)));
    await page.keyboard.press('Escape');
    await gallery.waitFor({ state: 'hidden' });
    await original.and(page.locator(':focus')).waitFor();

    const correct = action(workspace, 'Zuordnungen korrigieren');
    await correct.press('Enter');
    await page.waitForURL(`**/${profileHash}/korrigieren`);
    await workspace.getByRole('heading', { name: `Zuordnungen von ${personName}` }).waitFor();
    const face = workspace.getByRole('checkbox', { name: /^Gesicht .* auswählen$/ }).first();
    await face.waitFor();
    const assignment = workspace.getByRole('combobox');
    assert.equal(await assignment.count(), 0, 'Correction offered assignment before choosing any faces');
    assert.equal(await face.isChecked(), false, 'Correction preselected a face that was already assigned');
    await face.press('Space');
    assert.ok(await face.isChecked());
    await assignment.waitFor();
    await assertFits(page, workspace);

    await back.press('Enter');
    await page.waitForURL(`**/${profileHash}`);
    await profileName.waitFor();
    await correct.and(page.locator(':focus')).waitFor();
    await back.press('Enter');
    await page.waitForURL(`**/${directoryHash}`);
    await portrait.and(page.locator(':focus')).waitFor();
    assert.equal(await search.inputValue(), 'Anna', 'Back discarded the directory search');
    assert.equal(assignmentWrites, 0, 'Previewing a correction wrote to the library');

    await forward.press('Enter');
    await page.waitForURL(`**/${profileHash}`);
    await page.setViewportSize({ width: 320, height: 568 });
    await page.reload();
    await profileName.waitFor();
    await assertFits(page, workspace);
    await back.press('Enter');
    await page.waitForURL(`**/${directoryHash}`);
    await search.waitFor();
    await assertFits(page, workspace);

    await page.goto(`${webBase}/${reviewRoute}`);
    await action(workspace, /^Vorschläge\b/).waitFor();
    assert.equal(await search.count(), 0, 'A bare legacy review link entered the directory');
    console.log('PASS person directory: search, profile photo, correction scope, focus, history, reload and compact layout');
  } finally {
    await page.close();
  }
};
