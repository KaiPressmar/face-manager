const assert = require('node:assert/strict');

const libraryId = 'source-library';
const oldPath = '/mnt/extern/Fotos';
const newPath = '/mnt/ersatz/Fotos';
const extraPath = '/mnt/sicherung/Fotos';
const originalPhoto = `${newPath}/vorher.jpg`;
const candidatePaths = [`${newPath}/Veranstaltung/Foto.jpg`, `${newPath}/Kopie/Foto.jpg`];

function source(id, folderPath, fields = {}) {
  return {
    id, folder_path: folderPath, auto_scan: true, auto_import: false, status: 'idle',
    last_scan_at: null, last_success_at: null, scanned_files: 0, new_files: 0,
    new_folders: 0, relinked_files: 0, unavailable_files: 0, conflicts: 0,
    error: null, pending_path: null, auto_import_blocked: false,
    ...fields,
  };
}

module.exports = async function importSourcesFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 320, 720, errors);
  try {
    await page.addInitScript(() => {
      const NativeEventSource = window.EventSource;
      window.EventSource = class extends NativeEventSource {
        constructor(url) {
          super(url);
          window.importSourcesEvents = this;
        }
      };
    });
    const lastSuccess = '2025-12-10T12:00:00Z';
    const sources = new Map([['known', source('known', oldPath, {
      status: 'offline', last_scan_at: '2025-12-11T12:00:00Z', last_success_at: lastSuccess,
    })]]);
    const actions = [];
    let finishScan = () => {};
    let finishRelink = () => {};
    let nativeResult = extraPath;
    await page.route('**/api/system/select-folder', (route) => route.fulfill({ json: { folder_path: nativeResult } }));
    await page.route((url) => url.pathname.startsWith('/api/import-sources'), async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const [, , , id, action] = url.pathname.split('/');
      if (request.method() === 'GET') {
        return route.fulfill({ json: { library_id: libraryId, sources: [...sources.values()] } });
      }
      const body = request.postDataJSON() ?? {};
      actions.push([id, action, body]);
      if (request.method() === 'DELETE') {
        assert.equal(url.searchParams.get('library_id'), libraryId);
        if (action === 'relink') {
          const current = sources.get(id);
          const updated = { ...current, status: 'idle', pending_path: null };
          sources.set(id, updated);
          return route.fulfill({ json: updated });
        }
        sources.delete(id);
        return route.fulfill({ status: 204 });
      }
      assert.equal(body.library_id, libraryId);
      if (!id) {
        const added = source('extra', body.folder_path);
        sources.set(added.id, added);
        return route.fulfill({ json: added });
      }
      const current = sources.get(id);
      let updated = current;
      if (action === 'scan') {
        updated = { ...current, status: 'queued' };
        finishScan = () => sources.set(id, { ...updated, status: 'completed', scanned_files: 9,
          last_scan_at: lastSuccess, last_success_at: lastSuccess,
          new_files: body.import_new ? 0 : 3, new_folders: 2 });
      } else if (action === 'relink') {
        if (body.confirm) {
          assert.equal(current.pending_path, body.folder_path);
          updated = { ...current, status: 'queued' };
          finishRelink = () => sources.set(id, { ...updated, status: 'completed', folder_path: body.folder_path, pending_path: null,
            conflicts: 1, conflict_candidates: [{ original_path: originalPhoto, candidate_paths: candidatePaths }] });
        } else {
          updated = { ...current, status: 'preview_ready', pending_path: body.folder_path, relinked_files: 8, unavailable_files: 2 };
        }
      } else if (action === 'resolve') {
        assert.equal(body.original_path, originalPhoto);
        assert.equal(body.new_path, candidatePaths[1]);
        updated = { ...current, status: 'queued' };
      } else if (request.method() === 'PATCH') {
        const { library_id, ...settings } = body;
        updated = { ...current, ...settings };
      }
      sources.set(id, updated);
      return route.fulfill({ json: updated });
    });

    await page.goto(`${webBase}/#/bilder`);
    const openFolders = page.getByRole('button', { name: 'Ordner verwalten', exact: true });
    await openFolders.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Bilderordner verwalten' });
    const remember = dialog.getByRole('button', { name: 'Ordner merken', exact: true });
    await remember.and(page.locator(':focus')).waitFor();
    assert.ok(await dialog.evaluate((element) => element.clientWidth === element.scrollWidth));
    const original = dialog.getByRole('listitem', { name: oldPath, exact: true });
    await original.getByText('Nicht erreichbar', { exact: true }).waitFor();
    assert.ok(await original.getByText('Zuletzt erfolgreich geprüft').isVisible());
    assert.equal(await original.getByText('Keine neuen Fotos').count(), 0);
    assert.equal(await original.getByRole('checkbox', { name: 'Neue Fotos automatisch importieren' }).isChecked(), false);
    await original.getByRole('checkbox', { name: 'Automatisch prüfen' }).click();
    await original.getByText(/Automatische Prüfung pausiert/).waitFor();
    assert.equal(await original.getByRole('checkbox', { name: 'Automatisch prüfen' }).isChecked(), false);
    await original.getByRole('button', { name: `Jetzt prüfen: ${oldPath}` }).press('Enter');
    await original.getByText(/Prüfung eingereiht/).waitFor();
    finishScan();
    await original.getByText('3 neue Fotos · 2 neue Unterordner', { exact: true }).waitFor();
    await original.getByRole('button', { name: `Neue Fotos importieren: ${oldPath}` }).click();
    await original.getByText(/Prüfung eingereiht/).waitFor();
    assert.equal(actions.at(-1)[2].import_new, true);
    finishScan();
    await original.getByText(/^Keine neuen Fotos/).waitFor();

    const changeLocation = original.getByRole('button', { name: `Speicherort ändern: ${oldPath}` });
    const path = dialog.getByRole('textbox', { name: 'Pfad zum Bilderordner' });
    await changeLocation.press('Enter');
    await path.and(page.locator(':focus')).waitFor();
    nativeResult = null;
    await dialog.getByRole('button', { name: 'Ordner auswählen', exact: true }).click();
    await dialog.getByText('Kein Ordner ausgewählt.').waitFor();
    await page.keyboard.press('Escape');
    await changeLocation.and(page.locator(':focus')).waitFor();
    await changeLocation.press('Enter');
    await path.fill(newPath);
    await dialog.getByRole('button', { name: 'Neuen Speicherort prüfen', exact: true }).click();
    await original.getByText('Neuer Speicherort', { exact: true }).waitFor();
    assert.ok(await original.getByText('8 verifizierte Fotos · 2 nicht verifizierte Dateien · 0 neue Fotos', { exact: true }).isVisible());
    await original.getByRole('button', { name: `Neuen Speicherort verwerfen: ${newPath}` }).click();
    await original.getByText('Neuer Speicherort', { exact: true }).waitFor({ state: 'hidden' });
    assert.equal(await dialog.getByRole('listitem', { name: newPath, exact: true }).count(), 0);
    await changeLocation.press('Enter');
    await path.fill(newPath);
    await dialog.getByRole('button', { name: 'Neuen Speicherort prüfen', exact: true }).click();
    await original.getByRole('button', { name: `Neuen Speicherort übernehmen: ${newPath}` }).press('Enter');
    await original.getByText(/Prüfung eingereiht/).waitFor();
    finishRelink();
    const moved = dialog.getByRole('listitem', { name: newPath, exact: true });
    await moved.waitFor();
    assert.equal(await original.count(), 0);
    await moved.getByText('Unklare Speicherorte prüfen', { exact: true }).click();
    const choices = moved.getByRole('group', { name: `Bisheriger Speicherort: ${originalPhoto}` });
    const verify = choices.getByRole('button', { name: `Verknüpfung prüfen: ${originalPhoto}` });
    assert.equal(await verify.isEnabled(), false);
    await choices.getByRole('radio', { name: candidatePaths[1], exact: true }).check();
    await verify.press('Enter');
    await moved.getByText(/Prüfung eingereiht/).waitFor();
    await moved.getByRole('button', { name: `Jetzt prüfen: ${newPath}` }).and(page.locator(':focus')).waitFor();

    nativeResult = extraPath;
    await remember.press('Enter');
    await dialog.getByRole('button', { name: 'Ordner auswählen', exact: true }).click();
    await dialog.getByRole('button', { name: 'Speichern', exact: true }).click();
    const extra = dialog.getByRole('listitem', { name: extraPath, exact: true });
    await extra.waitFor();
    await extra.getByRole('button', { name: `Aus der Verwaltung entfernen: ${extraPath}` }).click();
    await extra.getByRole('button', { name: 'Ordner entfernen', exact: true }).click();
    await extra.waitFor({ state: 'hidden' });
    await remember.and(page.locator(':focus')).waitFor();
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    await openFolders.and(page.locator(':focus')).waitFor();

    await checkWaitingImport(page);
    console.log('PASS import sources: discovery, retry, keyboard, automatic checks, native selection and verified relink');
  } finally {
    await page.close();
  }
};

async function checkWaitingImport(page) {
  const job = {
    id: 'offline-import', folder_path: oldPath, status: 'waiting_for_source', last_error: null,
    total_images: 8, processed_images: 2, finished_at: null,
  };
  const publish = () => page.evaluate((job) => window.importSourcesEvents.dispatchEvent(new MessageEvent('imports', {
    data: JSON.stringify({ jobs: [job], active_job_id: null, queued_count: 0, waiting_for_source_count: Number(job.status === 'waiting_for_source') }),
  })), job);
  await page.waitForFunction(() => window.importSourcesEvents);
  await publish();
  const activities = page.getByRole('button', { name: /Aktivitäten/ }).first();
  await activities.click();
  await page.evaluate(() => window.importSourcesEvents.dispatchEvent(new MessageEvent('background-activities', {
    data: JSON.stringify({ activities: [{ id: 'scan-1', kind: 'import_source_scan', status: 'running',
      processed: 6, total: 0, unavailable: 0, finished_at: null }] }),
  })));
  const scan = page.locator('.activity-task').filter({ hasText: 'Bilderordner prüfen' });
  await scan.getByText('6 Dateien geprüft', { exact: true }).waitFor();
  assert.equal(await scan.getByRole('link', { name: 'Bilder öffnen' }).getAttribute('href'), '#/bilder');
  const activity = page.locator('.activity-task').filter({ hasText: 'Bilder aus „Fotos“ hinzufügen' });
  await activity.getByText('Ordner nicht erreichbar', { exact: true }).waitFor();
  const requested = [];
  await page.route('**/api/imports/offline-import/*', (route) => {
    requested.push(new URL(route.request().url()).pathname.split('/').at(-1));
    return route.fulfill({ json: { accepted: true } });
  });
  for (const [label, action] of [['jetzt erneut versuchen', 'resume'], ['pausieren', 'pause'], ['abbrechen', 'cancel']]) {
    const response = page.waitForResponse(`**/api/imports/offline-import/${action}`);
    await activity.getByRole('button', { name: new RegExp(label, 'i') }).click();
    await response;
  }
  assert.deepEqual(requested, ['resume', 'pause', 'cancel']);
}
