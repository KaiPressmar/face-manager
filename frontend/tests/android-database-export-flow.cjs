const assert = require('node:assert/strict');

module.exports = async function androidDatabaseExportFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 390, 844, errors, { hasTouch: true, isMobile: true });
  try {
    await page.addInitScript(() => {
      window.exportCalls = [];
      window.setExportPhase = (phase) => {
        sessionStorage.setItem('test-export-phase', phase);
        window.dispatchEvent(new Event('face-manager:android-export'));
      };
      window.AndroidExport = {
        requestDatabaseExport: () => {
          window.exportCalls.push('current');
          window.setExportPhase('PREPARING');
        },
        requestPreviousDatabaseExport: () => {
          window.exportCalls.push('previous');
          window.setExportPhase('PREPARING');
        },
        getDatabaseExportStatus: () => sessionStorage.getItem('test-export-phase') ?? 'IDLE',
      };
    });
    await page.route('**/api/database/restore-backup', (route) => route.fulfill({ json: { available: true } }));
    const setPhase = (phase) => page.evaluate((value) => window.setExportPhase(value), phase);
    const card = page.locator('.settings-card').filter({ has: page.getByRole('heading', { name: 'Daten sichern und wiederherstellen' }) });
    const create = card.getByRole('button', { name: 'Sicherung erstellen', exact: true });
    const restore = card.getByRole('button', { name: 'Sicherung wiederherstellen', exact: true });
    const previous = card.getByRole('button', { name: 'Vorherige Bibliothek speichern', exact: true });

    await page.goto(`${webBase}/#/einstellungen/daten`);
    await previous.waitFor();
    await create.evaluate((button) => { button.click(); button.click(); });
    assert.deepEqual(await page.evaluate(() => window.exportCalls), ['current']);
    assert.ok(await create.isDisabled() && await restore.isDisabled() && await previous.isDisabled());
    await card.getByRole('status').filter({ hasText: 'Die Sicherung wird vorbereitet' }).waitFor();
    await setPhase('CHOOSING');
    await card.getByRole('status').filter({ hasText: 'Wähle auf deinem Gerät den Speicherort' }).waitFor();

    await page.reload();
    await card.getByRole('status').filter({ hasText: 'Wähle auf deinem Gerät den Speicherort' }).waitFor();
    assert.ok(await create.isDisabled());
    await setPhase('CANCELLED');
    await card.getByRole('status').filter({ hasText: 'Sicherung abgebrochen.' }).waitFor();
    assert.ok(await create.isEnabled());

    await previous.click();
    assert.deepEqual(await page.evaluate(() => window.exportCalls), ['previous']);
    await setPhase('WRITING');
    await card.getByRole('status').filter({ hasText: 'gewählten Ort' }).waitFor();
    assert.ok(await create.isDisabled());
    await setPhase('PARTIAL_FILE');
    await card.getByRole('alert').filter({ hasText: 'Die Sicherung ist unvollständig.' }).waitFor();
    assert.ok(await previous.isEnabled());

    await create.click();
    await setPhase('SAVED');
    await card.getByRole('status').filter({ hasText: 'Die Sicherung wurde gespeichert.' }).waitFor();
    assert.ok(await restore.isEnabled());
    console.log('PASS Android backup bridge: exclusive operation, picker recreation, cancel, incomplete file, retry and previous library');
  } finally {
    await page.close();
  }
};
