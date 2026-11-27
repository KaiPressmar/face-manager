const assert = require('node:assert/strict');

module.exports = async function compactReviewFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 320, 568, errors);
  const review = '/#/gesichter-pruefen';
  const list = page.getByRole('complementary', { name: 'Gesichtsgruppen und Suche' });
  const detail = page.locator('.cluster-detail-topbar');
  const pane = page.locator('[data-workspace-page="review"] > .page-content');
  const back = page.getByRole('button', { name: 'Zur Gruppenliste' });
  const clusters = Array.from({ length: 35 }, (_, index) => ({
    cluster_id: index + 1, cluster_label: `Testgruppe ${index + 1}`, person_name: null,
    face_count: index === 29 ? 48 : 1,
  }));
  clusters.push({ cluster_id: 100, cluster_label: 'Bestätigte Testgruppe', person_name: 'Andrea Beispiel', face_count: 1 });
  const makeFace = (id, clusterId, status = 'active') => ({
    id, image_id: 1, cluster_id: clusterId, review_status: status, person_name: null,
    bbox_x: 5, bbox_y: 5, bbox_w: 25, bbox_h: 25,
  });
  const archiveFaces = [makeFace(6100, 200, 'unknown_person'), makeFace(6101, null, 'unknown_person')];
  const archive = { group_key: 'unknown_person', label: 'Unbekannte Personen', face_count: 2, cluster_count: 2 };
  const notFace = { group_key: 'not_face', label: 'Keine Gesichter', face_count: 1, cluster_count: 1 };
  const libraryId = 'compact-review-fixture';
  const ensureReflow = async () => {
    const widths = await pane.evaluate((element) => [
      document.documentElement.scrollWidth - window.innerWidth,
      element.scrollWidth - element.clientWidth,
    ]);
    assert.ok(widths.every((width) => width <= 1), `Review overflowed horizontally: ${widths}`);
  };
  const waitForScroll = (top) => page.waitForFunction((saved) =>
    Math.abs(document.querySelector('[data-workspace-page="review"] > .page-content').scrollTop - saved) < 3, top);
  const writes = [];

  try {
    await page.route('**/api/clusters-overview', (route) => route.fulfill({ json: {
      clusters, review_groups: [archive, notFace], first_cluster: null,
    } }));
    await page.route('**/api/clusters', (route) => route.fulfill({ json: clusters }));
    await page.route(/\/api\/clusters\/\d+\/faces$/, (route) => {
      const id = Number(new URL(route.request().url()).pathname.split('/')[3]);
      const cluster = clusters.find(({ cluster_id }) => cluster_id === id);
      return route.fulfill({ json: {
        ...cluster, assignment_library_id: libraryId,
        faces: Array.from({ length: cluster.face_count }, (_, index) => makeFace(id * 100 + index + 1, id)),
      } });
    });
    await page.route('**/api/face-review-groups', (route) => route.fulfill({ json: [archive, notFace] }));
    await page.route('**/api/face-review-groups/unknown_person/faces', (route) => route.fulfill({
      json: { ...archive, faces: archiveFaces, assignment_library_id: libraryId },
    }));
    await page.route('**/api/face-review-groups/not_face/faces', (route) => route.fulfill({
      json: { ...notFace, faces: [makeFace(6201, 201, 'not_face')], assignment_library_id: libraryId },
    }));
    await page.route('**/api/persons', (route) => route.fulfill({ json: [{ id: 1, name: 'Andrea Beispiel' }] }));
    await page.route('**/api/faces/batch', (route) => {
      writes.push(route.request().postDataJSON());
      return route.fulfill({ json: { status: 'ok', cluster_id: 100 } });
    });
    await page.route(/\/api\/faces\/\d+\/crop\?/, (route) => route.fulfill({
      contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>',
    }));

    await page.goto(`${webBase}${review}/ohne-namen`);
    await list.waitFor();
    const firstGroupButton = list.getByRole('button', { name: /^Testgruppe 1 / });
    await firstGroupButton.waitFor();
    assert.ok(await firstGroupButton.evaluate((button) => {
      const { x, y, width, height } = button.getBoundingClientRect();
      return button.contains(document.elementFromPoint(x + width / 2, y + height / 2));
    }),
      'First group requires scrolling in the initial 320×568 list');
    await page.getByRole('button', { name: 'Änderungsverlauf', exact: true }).waitFor();
    const enlargedText = await page.addStyleTag({ content: 'html { font-size: 200% }' });
    await ensureReflow();
    await enlargedText.evaluate((element) => element.remove());
    const opener = list.getByRole('button', { name: /^Testgruppe 30 / });
    await opener.scrollIntoViewIfNeeded();
    const listScroll = await pane.evaluate((element) => element.scrollTop);
    assert.ok(listScroll > 0);
    assert.equal(await detail.count(), 0);
    await ensureReflow();
    await opener.press('Enter');
    await page.waitForURL('**/#/gesichter-pruefen/gruppe/30');
    await back.and(page.locator(':focus')).waitFor();
    assert.equal(await list.isVisible(), false);
    await page.locator('.cluster-group-card').first().waitFor();
    assert.equal(await page.locator('.cluster-group-card').count(), 1);
    await page.locator('.cluster-group-card__header').scrollIntoViewIfNeeded();
    await page.getByRole('checkbox', { name: 'Gesicht 3001 auswählen' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Als unbekannt aussortieren' }).count(), 0);
    await page.getByText('Aussortieren oder lösen', { exact: true }).click();
    await page.getByRole('button', { name: 'Als unbekannt aussortieren' }).waitFor();
    await page.getByText('Aussortieren oder lösen', { exact: true }).click();
    await ensureReflow();

    await pane.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    const lastFace = page.getByRole('checkbox', { name: 'Gesicht 3048 auswählen' });
    await lastFace.press('Space');
    await page.getByRole('button', { name: 'Auswahl bearbeiten (1)', exact: true }).click();
    const selectedAssignment = page.getByRole('combobox', { name: 'Person suchen oder neuen Namen eingeben' });
    await selectedAssignment.and(page.locator(':focus')).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Auswahl aufheben', exact: true }).click();
    await pane.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    await lastFace.waitFor();
    const detailScroll = await pane.evaluate((element) => element.scrollTop);
    assert.ok(detailScroll > 1000);
    await back.click();
    await page.waitForURL('**/#/gesichter-pruefen/ohne-namen');
    await opener.and(page.locator(':focus')).waitFor();
    await waitForScroll(listScroll);
    await page.goBack();
    await page.waitForURL('**/#/gesichter-pruefen/gruppe/30');
    await waitForScroll(detailScroll);
    await page.goForward();
    await opener.and(page.locator(':focus')).waitFor();
    await waitForScroll(listScroll);

    for (const viewport of [{ width: 390, height: 740 }, { width: 768, height: 600 }, { width: 1024, height: 640 }]) {
      await page.setViewportSize(viewport);
      await page.goto(`${webBase}${review}/gruppe/30`);
      const firstFace = page.getByRole('checkbox', { name: 'Gesicht 3001 auswählen' });
      await firstFace.waitFor();
      const bounds = await firstFace.boundingBox();
      assert.ok(bounds && bounds.y >= 0 && bounds.y + bounds.height <= viewport.height,
        `First face requires scrolling in ${viewport.width}×${viewport.height}`);
      await ensureReflow();
    }

    await page.setViewportSize({ width: 1250, height: 520 });
    await back.click();
    await page.getByRole('searchbox', { name: 'Gesichtsgruppe suchen' }).fill('Testgruppe 30');
    await page.getByRole('button', { name: /^Personen korrigieren/ }).click();
    assert.equal(await page.getByRole('searchbox', { name: 'Person suchen' }).inputValue(), '');
    const person = list.getByRole('button', { name: /^Andrea Beispiel/ });
    await person.waitFor();
    if (await person.getAttribute('aria-expanded') === 'false') await person.press('Enter');
    await list.getByRole('button', { name: /^Bestätigte Testgruppe/ }).click();
    await back.waitFor();
    await page.getByRole('button', { name: 'Person bearbeiten' }).waitFor();
    assert.equal(await list.isVisible(), false);

    await back.click();
    await page.getByRole('button', { name: /^Aussortiert/ }).click();
    await list.getByRole('button', { name: /^Einzelne Gesichter/ }).click();
    const ungrouped = page.locator('[data-section-key="ungrouped"]');
    await ungrouped.waitFor();
    assert.equal(await page.locator('[data-section-key="cluster-200"]').count(), 0);
    const assignment = page.getByRole('combobox', { name: 'Person suchen oder neuen Namen eingeben' });
    assert.equal(await assignment.count(), 0, 'Ungrouped archive faces must be explicitly selected');
    await ungrouped.getByRole('button', { name: 'Alle auswählen', exact: true }).click();
    await assignment.fill('Andrea Beispiel');
    const archiveWrite = page.waitForResponse('**/api/faces/batch');
    await page.getByRole('button', { name: '„Andrea Beispiel“ zuweisen', exact: true }).click();
    await archiveWrite;
    assert.deepEqual(writes.at(-1)?.face_ids, [6101]);
    assert.equal(writes.at(-1)?.expected_library_id, libraryId);
    await back.click();
    await list.getByRole('button', { name: /^Gruppe 200/ }).click();
    await page.getByText('Ganze Gruppe · 1 Gesicht', { exact: true }).waitFor();

    await page.setViewportSize({ width: 1280, height: 800 });
    await list.waitFor();
    await detail.waitFor();
    assert.equal(await back.count(), 0);
    await page.goto(`${webBase}${review}/kategorie/not_face`);
    await page.getByRole('checkbox', { name: 'Gesicht 6201 auswählen' }).waitFor();
    assert.equal(await page.getByRole('checkbox', { name: 'Gesicht 6100 auswählen' }).count(), 0);
    console.log('PASS compact review: 320px, short window, group scope, scroll, focus, history and desktop split');
  } finally {
    await page.close();
  }
};
