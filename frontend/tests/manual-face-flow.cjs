const assert = require('node:assert/strict');

module.exports = async function manualFaceFlow(browser, errors, newPage, webBase, apiBase, screenshot) {
  const page = await newPage(browser, 1150, 820, errors);
  try {
    let suggestions = 0;
    await page.route('**/api/images/*/face-box-suggestions', async (route) => {
      suggestions += 1;
      await route.fulfill({ json: { items: [{ x: 0.43, y: 0.22, width: 0.2, height: 0.29 }] } });
    });
    await page.goto(`${webBase}/#/bilder`);
    const first = page.getByRole('button', { name: /in Galerie öffnen$/ }).first();
    await first.waitFor();
    const photo = page.getByRole('button', { name: await first.getAttribute('aria-label'), exact: true });
    await photo.click();
    const gallery = page.getByRole('dialog', { name: 'Bildansicht' });
    const mark = gallery.getByRole('button', { name: 'Gesicht markieren', exact: true });
    const editor = page.getByRole('dialog', { name: 'Gesicht markieren', exact: true });
    await mark.click();
    await editor.waitFor();
    assert.equal(suggestions, 0, 'Suggestions require an explicit action');
    await editor.getByRole('button', { name: 'Vorschläge anzeigen', exact: true }).click();
    await editor.getByRole('button', { name: /^Vorschlag 1/ }).click();
    assert.equal(suggestions, 1);
    await page.keyboard.press('Escape');
    await editor.waitFor({ state: 'hidden' });
    await mark.and(page.locator(':focus')).waitFor();
    assert.equal(await gallery.getByText('Gesicht gespeichert', { exact: true }).count(), 0);

    await mark.click();
    const surface = editor.getByLabel('Foto zum Markieren');
    await surface.waitFor();
    const area = await surface.boundingBox();
    assert.ok(area?.width > 100 && area?.height > 100);
    await page.mouse.move(area.x + area.width * 0.88, area.y + area.height * 0.59);
    await page.mouse.down();
    await page.mouse.move(area.x + area.width * 0.67, area.y + area.height * 0.25, { steps: 7 });
    await page.mouse.up();
    const name = editor.getByRole('combobox', { name: 'Person suchen oder neuen Namen eingeben' });
    await name.focus();
    await editor.getByRole('listbox').waitFor();
    await name.press('Escape');
    await editor.getByRole('listbox').waitFor({ state: 'hidden' });
    await editor.locator('.manual-face-rectangle').waitFor();
    await name.fill('Mira Markierung');
    await screenshot(page, 'manual-face-marking');
    const saved = page.waitForResponse((response) => response.request().method() === 'POST'
      && /\/api\/images\/\d+\/manual-faces$/.test(new URL(response.url()).pathname));
    await editor.getByRole('button', { name: '„Mira Markierung“ anlegen und zuweisen', exact: true }).click();
    const response = await saved;
    assert.equal(response.status(), 201, await response.text());
    const box = response.request().postDataJSON();
    assert.ok(Math.abs(box.x - 0.67) < 0.02 && Math.abs(box.width - 0.21) < 0.02);
    assert.ok(Math.abs(box.y - 0.25) < 0.02 && Math.abs(box.height - 0.34) < 0.02);
    const imageId = new URL(response.url()).pathname.split('/')[3];
    const detailResponse = await page.request.get(`${apiBase}/images/${imageId}/detail?resolve_geometry=true`);
    const detail = await detailResponse.json();
    assert.ok(detail.faces.some((face) => face.person_name === 'Mira Markierung' && face.is_manual));
    await editor.waitFor({ state: 'hidden' });
    await gallery.getByText('Mira Markierung', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await photo.and(page.locator(':focus')).waitFor();
    await photo.click();
    await gallery.getByText('Mira Markierung', { exact: true }).waitFor();
  } finally {
    await page.close();
  }

  const phone = await newPage(browser, 320, 680, errors, { hasTouch: true, isMobile: true });
  try {
    await phone.goto(`${webBase}/#/bilder`);
    await phone.getByRole('button', { name: /in Galerie öffnen$/ }).first().tap();
    await phone.getByRole('button', { name: 'Gesicht markieren', exact: true }).tap();
    const editor = phone.getByRole('dialog', { name: 'Gesicht markieren', exact: true });
    await editor.getByLabel('Foto zum Markieren').waitFor();
    const place = editor.getByRole('button', { name: 'Rahmen platzieren', exact: true });
    await place.tap();
    await editor.locator('.manual-face-rectangle').waitFor();
    await place.press('Enter');
    await editor.getByRole('combobox', { name: 'Person suchen oder neuen Namen eingeben' }).fill('Beispiel ohne Speicherung');
    assert.ok(await editor.evaluate((element) => element.scrollWidth <= element.clientWidth + 1), 'Manual face dialog overflows at 320px');
    await editor.getByRole('button', { name: 'Abbrechen', exact: true }).tap();
    await editor.waitFor({ state: 'hidden' });
  } finally {
    await phone.close();
  }
  console.log('PASS manual faces: explicit hint, cancel, reverse drag, persistence, gallery update and narrow touch/keyboard controls');
};
