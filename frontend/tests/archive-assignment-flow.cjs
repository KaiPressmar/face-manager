const assert = require('node:assert/strict');

module.exports = async function archiveAssignmentFlow(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1100, 800, errors);
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
    console.log('PASS archive assignment: no default person, explicit group scope, exact guarded selection');
  } finally {
    await page.close();
  }
  await mixedLibraryAssignment(browser, errors, newPage, webBase);
};

async function mixedLibraryAssignment(browser, errors, newPage, webBase) {
  const page = await newPage(browser, 1100, 800, errors);
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
