const assert = require('node:assert/strict');

async function albumLibraryFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1280, 900, errors);
  const cards = page.locator('[data-image-id]');
  const filters = page.getByRole('region', { name: 'Filter und Sortierung' });
  const library = page.getByRole('dialog', { name: 'Fotoalben' });
  const picker = page.getByRole('dialog', { name: 'Zum Album hinzufügen' });
  const createDialog = page.getByRole('dialog', { name: 'Album erstellen' });
  const gallery = page.getByRole('dialog', { name: 'Bildansicht' });
  const albumButton = page.getByRole('button', { name: 'Alben', exact: true });
  const selectButton = page.getByRole('button', { name: 'Fotos auswählen', exact: true });
  const photoIds = () => cards.evaluateAll((items) => items.map((item) => Number(item.dataset.imageId)));
  const create = async (name) => {
    await picker.getByRole('textbox', { name: 'Albumname' }).fill(name);
    await picker.getByRole('button', { name: 'Album erstellen', exact: true }).click();
    await picker.waitFor({ state: 'hidden' });
  };
  const selectCard = (index) => cards.nth(index).getByRole('button', { name: / auswählen$/ }).click();
  const edit = (name) => library.getByRole('button', { name: `Album ${name} bearbeiten`, exact: true }).click();

  try {
    const totalResponse = await page.request.get(`${webBase}/api/images?limit=1&all_photos=true`);
    assert.ok(totalResponse.ok());
    const { total } = await totalResponse.json();
    await page.goto(`${webBase}/#/bilder`);
    await cards.nth(1).waitFor();
    const chosen = (await photoIds()).slice(0, 2).sort((left, right) => left - right);

    await selectButton.click();
    await selectCard(0);
    await selectCard(1);
    await page.getByRole('status').filter({ hasText: '2 ausgewählt' }).waitFor();
    await page.getByRole('button', { name: 'Zum Album hinzufügen', exact: true }).click();
    await create('Sommerwege');
    await albumButton.click();
    await library.getByRole('button', { name: 'Album Sommerwege anzeigen', exact: true }).click();
    await filters.getByText('2', { exact: true }).waitFor();
    assert.deepEqual((await photoIds()).sort((left, right) => left - right), chosen);

    await cards.first().getByRole('button', { name: /in Galerie öffnen/ }).click();
    await gallery.getByRole('button', { name: 'Zum Album', exact: true }).click();
    await picker.getByRole('combobox', { name: 'Zielalbum' }).selectOption('');
    await create('Nur ein Foto');
    await gallery.getByRole('status').filter({ hasText: 'Im Album „Nur ein Foto“ gespeichert' }).waitFor();
    await gallery.getByRole('button', { name: 'Galerie schließen' }).click();

    await selectButton.click();
    await selectCard(1);
    await page.getByRole('button', { name: 'Als Titelbild festlegen' }).click();
    await page.getByRole('status').filter({ hasText: 'Titelbild gespeichert.' }).waitFor();
    await selectButton.click();
    await selectCard(0);
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'Aus Album entfernen', exact: true }).click();
    await filters.getByText('1', { exact: true }).waitFor();

    await page.setViewportSize({ width: 320, height: 315 });
    await albumButton.click();
    await edit('Sommerwege');
    const renameField = library.getByRole('textbox', { name: 'Albumname' });
    await renameField.fill('Ungespeicherter Name');
    const newAlbum = library.getByRole('button', { name: 'Neues Album', exact: true });
    const createName = createDialog.getByRole('textbox', { name: 'Albumname' });
    for (const closeWithKeyboard of [true, false]) {
      await newAlbum.focus();
      const previousScroll = await library.evaluate((element) => {
        element.scrollTop = 90;
        return element.scrollTop;
      });
      assert.ok(previousScroll > 0, 'Expected the short album library to scroll');
      await page.keyboard.press('Enter');
      await createName.and(page.locator(':focus')).waitFor();
      await library.waitFor({ state: 'hidden' });
      assert.equal(await createName.inputValue(), '');
      await createName.fill('Nicht speichern');
      if (closeWithKeyboard) await page.keyboard.press('Escape');
      else await createDialog.getByRole('button', { name: 'Schließen', exact: true }).click();
      await createDialog.waitFor({ state: 'hidden' });
      await newAlbum.and(page.locator(':focus')).waitFor();
      assert.equal(await renameField.inputValue(), 'Ungespeicherter Name');
      assert.equal(await library.evaluate((element) => element.scrollTop), previousScroll);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await library.getByRole('textbox', { name: 'Albumname' }).fill('Sommerstreifzug');
    await library.getByRole('button', { name: 'Name speichern' }).click();
    await edit('Sommerstreifzug');
    await library.getByRole('button', { name: 'Album löschen …', exact: true }).click();
    await library.getByRole('button', { name: 'Album endgültig löschen' }).click();
    await library.waitFor({ state: 'hidden' });
    await filters.getByText(`${total.toLocaleString('de-DE')} Bilder`, { exact: true }).waitFor();

    await page.setViewportSize({ width: 320, height: 720 });
    await albumButton.click();
    await library.getByRole('button', { name: 'Album Nur ein Foto anzeigen', exact: true }).waitFor();
    assert.equal(await library.evaluate((item) => item.scrollWidth - item.clientWidth), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0);
    await newAlbum.click();
    await createName.and(page.locator(':focus')).waitFor();
    await createName.fill('Leeres Testalbum');
    await createName.press('Enter');
    await createDialog.waitFor({ state: 'hidden' });
    await library.waitFor({ state: 'hidden' });
    await page.getByRole('status').filter({ hasText: 'Album „Leeres Testalbum“ gespeichert.' }).waitFor();
    console.log('PASS albums: selection, fullscreen addition, cover, edits, nested creation and focus at 320px');
  } finally {
    const response = await page.request.get(`${webBase}/api/albums`);
    if (response.ok()) {
      const { albums } = await response.json();
      for (const { id, name } of albums) {
        if (['Sommerwege', 'Sommerstreifzug', 'Nur ein Foto', 'Leeres Testalbum'].includes(name)) {
          await page.request.delete(`${webBase}/api/albums/${id}`);
        }
      }
    }
    await page.close();
  }
}

module.exports = albumLibraryFlow;
