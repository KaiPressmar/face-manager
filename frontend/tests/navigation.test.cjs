const test = require('node:test');
const assert = require('node:assert/strict');
const load = require('./load-typescript.cjs');
const {
  navigationHash, parseNavigationHash, workspaceHistoryMarker, workspaceHistoryState, workspaceSelectionLimited,
  MAX_HISTORY_PHOTO_IDS, MAX_NATIVE_FORWARD_ENTRIES,
} = load('src/utils/navigation.ts');

test('all library pages survive reload via hash routes', () => {
  for (const page of ['people', 'map', 'renaming', 'review', 'settings']) {
    assert.deepEqual(parseNavigationHash(navigationHash({ page })), { page });
  }
  assert.equal(navigationHash({ page: 'map' }), '#/weltkarte');
});

test('review and settings deep links preserve their targets', () => {
  for (const target of [
    { page: 'review', clusterId: 42 },
    { page: 'review', reviewView: 'open' },
    { page: 'review', reviewView: 'suggestions' },
    { page: 'review', reviewView: 'people' },
    { page: 'review', reviewView: 'archive' },
    { page: 'review', reviewView: 'shared' },
    { page: 'review', groupKey: 'unknown_person' },
    { page: 'review', groupKey: 'not_face' },
    { page: 'review', groupKey: 'not_face', archiveClusterId: 87 },
    { page: 'settings', settingsSection: 'dateinamen' },
    { page: 'settings', settingsSection: 'updates' },
  ]) assert.deepEqual(parseNavigationHash(navigationHash(target)), target);
});

test('browser metadata identifies each workspace and bounds durable selection size', () => {
  const ordinary = { from_date: '2025-01-01', album_id: 'a'.repeat(32), bounds: { west: 0, east: 1, south: 2, north: 3 } };
  const route = { page: 'people', geoSelection: { ...ordinary, image_ids: [2, 100, 301] } };
  const first = { id: 'a', position: 1, generation: 5, hasForward: true, nativeForwardEntries: ['b'] };
  const second = { id: 'b', position: 2, generation: 5 };
  const firstState = workspaceHistoryState(route, first);
  const secondState = workspaceHistoryState(route, second);
  assert.deepEqual(workspaceHistoryMarker(firstState), first);
  assert.deepEqual(workspaceHistoryMarker(secondState), second);
  assert.deepEqual(parseNavigationHash(navigationHash(route), firstState), { ...route, geoSelectionEpoch: first.generation });
  const oversized = { page: 'people', geoSelection: { ...ordinary, image_ids: Array.from({ length: MAX_HISTORY_PHOTO_IDS + 1 }, (_, index) => index + 1) } };
  const limitedState = workspaceHistoryState(oversized, second);
  assert.equal(workspaceSelectionLimited(limitedState), true);
  assert.deepEqual(parseNavigationHash(navigationHash(oversized), limitedState), { page: 'people', geoSelection: ordinary, geoSelectionEpoch: second.generation });
  assert.equal('geoSelection' in workspaceHistoryState({ page: 'people' }, first), false);
  assert.equal(workspaceHistoryMarker({ faceManagerWorkspace: { ...first, position: -1 } }), undefined);
  assert.equal(workspaceHistoryMarker({ faceManagerWorkspace: { ...first, generation: 2.5 } }), undefined);
  assert.equal(workspaceHistoryMarker({ faceManagerWorkspace: { ...first, hasForward: 'true' } }), undefined);
  assert.equal(workspaceHistoryMarker({ faceManagerWorkspace: { ...first, nativeForwardEntries: [23] } }), undefined);
  assert.equal(workspaceHistoryMarker({ faceManagerWorkspace: {
    ...first, nativeForwardEntries: Array(MAX_NATIVE_FORWARD_ENTRIES + 1).fill('b'),
  } }), undefined);
});

test('image selection survives browser history without putting photo IDs in the URL', () => {
  const entry = { page: 'people', geoSelection: { image_ids: [2, 100], from_date: '2025-06-01' }, geoSelectionEpoch: 3 };
  const hash = navigationHash(entry);
  assert.equal(hash, '#/bilder/auswahl');
  assert.deepEqual(parseNavigationHash(hash, entry), entry);
  assert.deepEqual(parseNavigationHash(hash), { page: 'people' });
  assert.deepEqual(parseNavigationHash('#/bilder', entry), { page: 'people' });
});

test('malformed group IDs never silently navigate to another group', () => {
  for (const id of ['1oops', '1.5', '-1', '0', 'Infinity', '9007199254740992', '1e2', '']) {
    assert.deepEqual(parseNavigationHash(`#/gesichter-pruefen/gruppe/${id}`), { page: 'review' });
  }
  assert.deepEqual(parseNavigationHash('#/unknown'), { page: 'people' });
});

test('inherited object keys are not application routes', () => {
  for (const name of ['constructor', '__proto__', 'toString']) {
    assert.deepEqual(parseNavigationHash(`#/${name}`), { page: 'people' });
  }
});
