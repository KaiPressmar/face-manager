const assert = require('node:assert/strict');

module.exports = async function archiveAssignmentFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1280, 800, errors);
  const postBodies = [];
  const makeFace = (id, clusterId) => ({
    id, image_id: 1, cluster_id: clusterId, person_name: null, review_status: 'unknown_person',
    bbox_x: 5, bbox_y: 5, bbox_w: 25, bbox_h: 25,
  });
  let faces = [makeFace(41101, 4101), makeFace(41102, 4101), makeFace(41103, 4102), makeFace(41104, null)];
  const libraryId = '1f22bc7ef4e74c758ddb7ab393d3d1b46';
  const summary = () => ({
    group_key: 'unknown_person', label: 'Unbekannte Personen', face_count: faces.length,
    cluster_count: new Set(faces.map((face) => face.cluster_id ?? face.id)).size,
  });
  try {
    await page.route('**/api/clusters-overview', (route) => route.fulfill({ json: {
      clusters: [], review_groups: [summary()], first_cluster: null,
    } }));
    await page.route('**/api/clusters', (route) => route.fulfill({ json: [] }));
    await page.route('**/api/face-review-groups', (route) => route.fulfill({ json: [summary()] }));
    await page.route('**/api/face-review-groups/unknown_person/faces', (route) => route.fulfill({
      json: { ...summary(), faces, assignment_library_id: libraryId },
    }));
    await page.route('**/api/persons', (route) => route.fulfill({ json: [{ id: 1, name: 'Andrea' }, { id: 2, name: 'Zoe' }] }));
    await page.route('**/api/faces/batch', (route) => {
      const payload = route.request().postDataJSON();
      postBodies.push(payload);
      faces = faces.filter((face) => !payload.face_ids.includes(face.id));
      return route.fulfill({ json: { status: 'ok', cluster_id: 999 } });
    });

    await page.goto(`${webBase}/#/gesichter-pruefen/kategorie/unknown_person`);
    const assignment = page.getByRole('combobox', { name: 'Person suchen oder neuen Namen eingeben' });
    const section = page.locator('[data-section-key="cluster-4102"]');
    await section.waitFor();
    assert.equal(await assignment.count(), 0, 'Archive initially allowed assigning a whole status queue');
    assert.ok(await page.getByRole('button', { name: 'Erneut prüfen', exact: true }).isDisabled());

    await page.getByRole('button', { name: /^Gruppe 4102\b/ }).click();
    await assignment.waitFor();
    assert.equal(await assignment.inputValue(), '');
    assert.ok(await page.getByRole('button', { name: 'Person zuweisen', exact: true }).isDisabled());
    await page.getByText('Ganze Gruppe · 1 Gesicht', { exact: true }).waitFor();
    await assignment.fill('Andrea');
    const groupAssigned = page.waitForResponse('**/api/faces/batch');
    await page.getByRole('button', { name: '„Andrea“ zuweisen', exact: true }).click();
    await groupAssigned;
    assert.deepEqual(postBodies[0], {
      action: 'assign_person', face_ids: [41103], person_name: 'Andrea',
      expected_faces: [{ id: 41103, cluster_id: 4102, review_status: 'unknown_person', person_name: null }],
      expected_library_id: libraryId,
    });
    await section.waitFor({ state: 'hidden' });
    await assignment.waitFor({ state: 'hidden' });

    const ungrouped = page.locator('[data-section-key="ungrouped"]');
    await page.getByRole('button', { name: /^Einzelne Gesichter\b/ }).click();
    assert.equal(await assignment.count(), 0, 'Ungrouped archive faces cannot be an implicit group');
    await ungrouped.getByRole('button', { name: 'Alle auswählen', exact: true }).click();
    await assignment.fill('Zoe');
    const selectionAssigned = page.waitForResponse('**/api/faces/batch');
    await page.getByRole('button', { name: '„Zoe“ zuweisen', exact: true }).click();
    await selectionAssigned;
    assert.deepEqual(postBodies[1], {
      action: 'assign_person', face_ids: [41104], person_name: 'Zoe',
      expected_faces: [{ id: 41104, cluster_id: null, review_status: 'unknown_person', person_name: null }],
      expected_library_id: libraryId,
    });
    assert.deepEqual(faces.map((face) => face.id), [41101, 41102]);
    await page.getByRole('button', { name: /^Gruppe 4101\b/ }).click();
    const groupRestored = page.waitForResponse('**/api/faces/batch');
    await page.getByRole('button', { name: 'Erneut prüfen', exact: true }).click();
    await groupRestored;
    assert.deepEqual(postBodies[2], {
      action: 'restore_to_manual_review', face_ids: [41101, 41102], expected_library_id: libraryId,
      expected_faces: [41101, 41102].map((id) => ({ id, cluster_id: 4101, review_status: 'unknown_person', person_name: null })),
    });
    console.log('PASS archive assignment and restore: explicit group scope and exact guarded selections');
  } finally {
    await page.close();
  }
  await mixedLibraryAssignment(browser, errors, newPage, webBase);
  await reviewRecoveryFlow(browser, errors, newPage, webBase);
};

async function mixedLibraryAssignment(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1280, 800, errors);
  const clusters = [4201, 4202].map((id) => ({
    cluster_id: id, cluster_label: `Testgruppe ${id}`, person_name: null, face_count: 1,
  }));
  const details = clusters.map((cluster) => ({ ...cluster, faces: [{
    id: cluster.cluster_id + 100, image_id: 1, cluster_id: cluster.cluster_id, review_status: 'active',
    person_name: null, bbox_x: 5, bbox_y: 5, bbox_w: 25, bbox_h: 25,
  }] }));
  let writes = 0;
  try {
    await page.route('**/api/clusters-overview', (route) => route.fulfill({ json: {
      clusters, review_groups: [], first_cluster: details[0], assignment_library_id: 'before-restore',
    } }));
    await page.route('**/api/clusters', (route) => route.fulfill({ json: clusters }));
    await page.route('**/api/face-review-groups', (route) => route.fulfill({ json: [] }));
    await page.route('**/api/clusters/4201/faces', (route) => route.fulfill({
      json: { ...details[0], assignment_library_id: 'before-restore' },
    }));
    await page.route('**/api/clusters/4202/faces', (route) => route.fulfill({
      json: { ...details[1], assignment_library_id: 'after-restore' },
    }));
    await page.route('**/api/persons', (route) => route.fulfill({ json: [{ id: 1, name: 'Andrea' }] }));
    await page.route('**/api/faces/batch', (route) => {
      writes += 1;
      return route.fulfill({ status: 409, json: { detail: 'Unerwartete Zuweisungsanfrage' } });
    });

    await page.goto(`${webBase}/#/gesichter-pruefen/ohne-namen`);
    for (const cluster of clusters) {
      await page.locator(`[data-section-key="cluster-${cluster.cluster_id}"]`)
        .getByRole('button', { name: 'Alle auswählen', exact: true }).click();
    }
    await page.getByText('2 Gesichter markiert', { exact: true }).waitFor();
    await page.getByRole('combobox', { name: 'Person suchen oder neuen Namen eingeben' }).fill('Andrea');
    await page.getByRole('button', { name: '„Andrea“ zuweisen', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'unterschiedlichen Bibliotheksständen' }).waitFor();
    assert.equal(writes, 0, 'A mixed-library selection reached the mutation endpoint');
    console.log('PASS assignment epochs: a mixed-library selection cannot be submitted');
  } finally {
    await page.close();
  }
}

async function reviewRecoveryFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1280, 800, errors);
  const cluster = { cluster_id: 4301, cluster_label: 'Beständige Gruppen-ID', person_name: null, face_count: 1 };
  const archive = { group_key: 'not_face', label: 'Keine Gesichter', face_count: 1, cluster_count: 1 };
  const makeFace = (id, clusterId, status) => ({
    id, image_id: 1, cluster_id: clusterId, person_name: null, review_status: status,
    bbox_x: 5, bbox_y: 5, bbox_w: 25, bbox_h: 25,
  });
  let library = 'review-before-restore';
  let groupFaceId = 4302;
  const retryAllowed = (scope) => page.evaluate((key) => Boolean(window.reviewTestRetries[key]), scope);
  const retry = async (button, scope) => {
    await button.evaluate((element, key) => element.addEventListener('click', () => {
      window.reviewTestRetries[key] = true;
    }, { once: true }), scope);
    await button.click();
  };
  try {
    await page.addInitScript(() => {
      window.reviewTestRetries = {};
      window.EventSource = class ReviewTestEvents extends EventTarget {
        static CONNECTING = 0;
        readyState = 1;
        constructor() {
          super();
          window.reviewTestEvents = this;
          setTimeout(() => this.onopen?.(), 0);
        }
        close() { this.readyState = 2; }
      };
    });
    await page.route('**/api/library-identity', (route) => route.fulfill({ json: { library_id: library } }));
    await page.route('**/api/clusters-overview', async (route) => !await retryAllowed('sidebar')
      ? route.fulfill({ status: 503, json: {} })
      : route.fulfill({ json: { clusters: [cluster], review_groups: [archive], first_cluster: null } }));
    await page.route('**/api/clusters', async (route) => !await retryAllowed('sidebar')
      ? route.fulfill({ status: 503, json: {} }) : route.fulfill({ json: [cluster] }));
    await page.route('**/api/face-review-groups', (route) => route.fulfill({ json: [archive] }));
    await page.route('**/api/clusters/4301/faces', async (route) => !await retryAllowed('group')
      ? route.fulfill({ status: 503, json: {} })
      : route.fulfill({ json: { ...cluster, faces: [makeFace(groupFaceId, cluster.cluster_id, 'active')], assignment_library_id: library } }));
    await page.route('**/api/face-review-groups/not_face/faces', async (route) => !await retryAllowed('archive')
      ? route.fulfill({ status: 503, json: {} })
      : route.fulfill({ json: { ...archive, faces: [makeFace(4309, null, 'not_face')], assignment_library_id: library } }));
    let actualWrite;
    await page.route('**/api/faces/batch', (route) => {
      actualWrite = route.request().postDataJSON();
      return route.fulfill({ json: { status: 'ok', updated_count: 1 } });
    });

    await page.goto(`${webBase}/#/gesichter-pruefen/ohne-namen`);
    await page.getByRole('alert').filter({ hasText: 'Die Gesichtsgruppen konnten nicht geladen werden' }).waitFor();
    assert.equal(await page.getByText('Keine offenen Gesichtsgruppen.', { exact: true }).count(), 0);
    await retry(page.getByRole('button', { name: 'Gesichtsgruppen erneut laden', exact: true }), 'sidebar');
    const retryGroup = page.getByRole('button', { name: 'Gesichter erneut laden', exact: true });
    await retryGroup.waitFor();
    await retry(retryGroup, 'group');
    await page.getByRole('checkbox', { name: 'Gesicht 4302 auswählen' }).waitFor();

    await page.evaluate(() => { location.hash = '#/gesichter-pruefen/kategorie/not_face'; });
    const retryArchive = page.getByRole('button', { name: 'Archivgesichter erneut laden', exact: true });
    await retryArchive.waitFor();
    assert.equal(await page.getByText('Keine Gesichtsgruppen in diesem Bereich.', { exact: true }).count(), 0);
    await retry(retryArchive, 'archive');
    await page.getByRole('checkbox', { name: 'Gesicht 4309 auswählen' }).waitFor();

    await page.evaluate(() => { location.hash = '#/einstellungen/daten'; });
    await page.locator('[data-workspace-page="settings"].page-pane--active').waitFor();
    library = 'review-after-restore';
    groupFaceId = 4303;
    await page.evaluate((libraryId) => {
      window.reviewTestEvents.dispatchEvent(new MessageEvent('clusters', {
        data: JSON.stringify({ reason: 'database_import', library_id: libraryId }),
      }));
    }, library);
    await page.waitForFunction((libraryId) => history.state?.faceManagerWorkspace?.libraryId === libraryId, library);
    await page.evaluate(() => { location.hash = '#/gesichter-pruefen/ohne-namen'; });
    await page.getByRole('checkbox', { name: 'Gesicht 4303 auswählen' }).press('Space');
    assert.equal(await page.getByRole('checkbox', { name: 'Gesicht 4302 auswählen' }).count(), 0);
    await page.getByText('Aussortieren oder lösen', { exact: true }).click();
    const removed = page.waitForResponse('**/api/faces/batch');
    await page.getByRole('button', { name: 'Aus Gesichtsgruppe lösen', exact: true }).click();
    await removed;
    assert.deepEqual(actualWrite, {
      action: 'remove_from_cluster', cluster_id: 4301, face_ids: [4303], expected_library_id: library,
      expected_faces: [{ id: 4303, cluster_id: 4301, review_status: 'active', person_name: null }],
    });
    console.log('PASS review recovery: list and detail errors are retriable, hidden review cache clears on restore');
  } finally {
    await page.close();
  }
}
