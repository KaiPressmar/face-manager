const assert = require('node:assert/strict');

const invitationText = 'Möchtest du Face Manager kennenlernen?';
const tourName = /^(Erste Schritte|Face Manager kennenlernen)$/;
const photoStep = 'Bilder hinzufügen';
const peopleStep = 'Gesichtern Namen geben';

const getHelp = (page) => page.getByRole('button', { name: 'Hilfe', exact: true });
const getTour = (page) => page.getByRole('dialog', { name: tourName });
const hash = (page) => new URL(page.url()).hash;
const focused = (locator) => locator.evaluate((element) => element === document.activeElement);

async function checkDesktopTour(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1280, 900, errors);
  const help = getHelp(page);
  const tour = getTour(page);
  const invitation = page.getByText(invitationText, { exact: true });
  const firstStep = tour.getByRole('heading', { name: photoStep, exact: true });
  const secondStep = tour.getByRole('heading', { name: peopleStep, exact: true });
  const next = tour.getByRole('button', { name: 'Weiter', exact: true });
  const back = tour.getByRole('button', { name: 'Zurück', exact: true });
  const modalCount = () => page.locator('[role="dialog"][aria-modal="true"]').count();

  try {
    await page.route('**/api/updates/check?**', (route) => route.fulfill({
      json: { enabled: true, current_version: '0.16.0', update_available: false, check_interval_seconds: 3600 },
    }));
    await page.goto(`${webBase}/#/bilder`);
    await invitation.waitFor();
    assert.equal(await tour.count(), 0, 'The first invitation opened a modal');
    await help.focus();
    assert.ok(await focused(help), 'The invitation trapped focus');
    await page.getByRole('button', { name: 'Später', exact: true }).click();
    await invitation.waitFor({ state: 'hidden' });
    assert.equal(hash(page), '#/bilder', 'Dismissing the invitation changed the page');

    await page.reload();
    await page.getByRole('heading', { name: 'Bilder', exact: true }).waitFor();
    await help.waitFor();
    assert.equal(await invitation.count(), 0, 'The dismissed invitation returned after reload');

    await help.press('Enter');
    await firstStep.waitFor();
    assert.ok(await tour.evaluate((element) => element.contains(document.activeElement)), 'The tour left focus outside the dialog');
    const topic = tour.getByRole('combobox', { name: 'Thema', exact: true });
    await topic.focus();
    await topic.selectOption({ label: 'Fotos auf der Karte finden' });
    await tour.getByRole('heading', { name: 'Fotos auf der Karte finden', exact: true }).waitFor();
    assert.equal(hash(page), '#/bilder', 'Switching the help topic changed the page');
    assert.ok(await focused(topic), 'Switching topics interrupted the topic control');
    await topic.selectOption({ label: photoStep });
    await firstStep.waitFor();
    const enabledControls = tour.locator('button:visible:enabled, a[href]:visible');
    await enabledControls.first().focus();
    await page.keyboard.press('Shift+Tab');
    assert.ok(await focused(enabledControls.last()), 'Shift+Tab did not wrap inside the tour');
    await page.keyboard.press('Tab');
    assert.ok(await focused(enabledControls.first()), 'Tab did not wrap inside the tour');

    await next.press('Enter');
    await secondStep.waitFor();
    assert.ok(await focused(secondStep), 'The next step did not announce its heading through focus');
    assert.equal(hash(page), '#/bilder', 'Next unexpectedly navigated away');
    await back.press('Enter');
    await firstStep.waitFor();
    assert.ok(await focused(firstStep), 'The previous step did not focus its heading');
    assert.equal(hash(page), '#/bilder', 'Back unexpectedly navigated away');

    await page.keyboard.press('Escape');
    await tour.waitFor({ state: 'hidden' });
    assert.ok(await focused(help), 'Closing the tour did not restore focus to Help');
    assert.equal(hash(page), '#/bilder', 'Closing the tour changed the page');
    assert.equal(await invitation.count(), 0, 'Closing the tour showed the dismissed invitation again');

    await help.click();
    await firstStep.waitFor();
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('face-manager:update-available', {
      detail: {
        enabled: true, current_version: '0.16.0', latest_version: '99.0.0',
        update_available: true, check_interval_seconds: 3600,
      },
    })));
    await page.getByTitle('Verfügbares Update anzeigen', { exact: true }).waitFor();
    assert.equal(await modalCount(), 1, 'The arriving update interrupted the tour');
    await tour.getByRole('button', { name: 'Bilder hinzufügen', exact: true }).click();
    const importDialog = page.getByRole('dialog', { name: 'Bilderordner hinzufügen', exact: true });
    await importDialog.waitFor();
    assert.equal(await modalCount(), 1, 'The queued update interrupted the import dialog');
    assert.equal(hash(page), '#/bilder');
    await importDialog.getByRole('button', { name: 'Ordnerauswahl schließen', exact: true }).click();
    const update = page.getByRole('dialog', { name: 'Face Manager 99.0.0', exact: true });
    await update.waitFor();
    assert.equal(await modalCount(), 1);
    await update.getByRole('button', { name: 'Update-Hinweis schließen', exact: true }).click();

    await help.click();
    await firstStep.waitFor();
    await next.click();
    await secondStep.waitFor();
    await tour.getByRole('button', { name: 'Personen öffnen', exact: true }).click();
    await page.waitForURL(/#\/gesichter-pruefen(?:\/[^?#]*)?$/);
    await tour.waitFor({ state: 'hidden' });
    assert.equal(await invitation.count(), 0);
    await help.press('Enter');
    await secondStep.waitFor();
    assert.match(hash(page), /^#\/gesichter-pruefen/);
    await page.keyboard.press('Escape');
    await tour.waitFor({ state: 'hidden' });

    await page.goto(`${webBase}/#/einstellungen/erkennung`);
    await help.press('Enter');
    await tour.getByRole('heading', { name: 'Zuordnungen und Einstellungen sichern', exact: true }).waitFor();
    await topic.selectOption({ label: 'Fortschritt und Hilfe finden' });
    await tour.getByRole('heading', { name: 'Fortschritt und Hilfe finden', exact: true }).waitFor();
    const activities = page.getByRole('region', { name: 'Aktivitäten', exact: true });
    assert.equal(await activities.count(), 0);
    await tour.getByRole('button', { name: 'Aktivitäten öffnen', exact: true }).click();
    await activities.waitFor();
    assert.equal(hash(page), '#/einstellungen/erkennung');
  } finally {
    await page.close();
  }
}

async function checkTouchTour(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 320, 680, errors, { hasTouch: true, isMobile: true });
  const tour = getTour(page);
  const help = getHelp(page);
  const verifyWidth = async () => {
    const viewport = await page.evaluate(() => [document.documentElement.clientWidth, document.documentElement.scrollWidth]);
    assert.deepEqual(viewport, [320, 320], 'The mobile help caused horizontal scrolling');
    assert.ok(await tour.evaluate((element) => element.scrollWidth <= element.clientWidth), 'The tour content overflowed its dialog');
  };

  try {
    await page.goto(`${webBase}/#/bilder`);
    await page.getByText(invitationText, { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Einführung starten', exact: true }).tap();
    await tour.getByRole('heading', { name: photoStep, exact: true }).waitFor();
    await verifyWidth();
    await tour.getByRole('button', { name: 'Weiter', exact: true }).tap();
    await tour.getByRole('heading', { name: peopleStep, exact: true }).waitFor();
    await verifyWidth();
    assert.equal(hash(page), '#/bilder');

    const next = tour.getByRole('button', { name: 'Weiter', exact: true });
    const finish = tour.getByRole('button', { name: 'Fertig', exact: true });
    for (let index = 0; index < 12 && await next.isVisible(); index += 1) {
      await next.tap();
      await verifyWidth();
      assert.equal(hash(page), '#/bilder');
    }
    await finish.tap();
    await tour.waitFor({ state: 'hidden' });
    await help.tap();
    await tour.waitFor();
    await verifyWidth();

    const instructions = tour.locator('li').first();
    const fontSize = () => instructions.evaluate((element) => parseFloat(getComputedStyle(element).fontSize));
    const normalSize = await fontSize();
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    assert.ok(await fontSize() >= normalSize * 1.95, 'The guide instructions did not scale with the user font size');
    await verifyWidth();
    await next.tap();
    await tour.getByRole('heading', { name: peopleStep, exact: true }).waitFor();
    await verifyWidth();
    await tour.getByRole('button', { name: 'Schließen', exact: true }).tap();
    await tour.waitFor({ state: 'hidden' });
  } finally {
    await page.close();
  }
}

async function helpTourFlow(browser, errors, newPage, webBase) {
  await checkDesktopTour(browser, errors, newPage, webBase);
  await checkTouchTour(browser, errors, newPage, webBase);
  console.log('PASS help tour: invitation, keyboard focus, explicit actions, 320px layout and 200% text');
}

module.exports = helpTourFlow;
