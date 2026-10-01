const test = require('node:test');
const assert = require('node:assert/strict');
const load = require('./load-typescript.cjs');
const { navigationHash, parseNavigationHash } = load('src/utils/navigation.ts');

test('all library pages survive reload via hash routes', () => {
  for (const page of ['people', 'map', 'renaming', 'review', 'settings']) {
    assert.deepEqual(parseNavigationHash(navigationHash({ page })), { page });
  }
  assert.equal(navigationHash({ page: 'map' }), '#/weltkarte');
});

test('review and settings deep links preserve their targets', () => {
  for (const target of [
    { page: 'review', clusterId: 42 },
    { page: 'review', groupKey: 'unknown_person' },
    { page: 'review', groupKey: 'not_face' },
    { page: 'settings', settingsSection: 'dateinamen' },
    { page: 'settings', settingsSection: 'updates' },
  ]) assert.deepEqual(parseNavigationHash(navigationHash(target)), target);
});

test('malformed group IDs never silently navigate to another group', () => {
  for (const id of ['1oops', '1.5', '-1', '0', 'Infinity', '9007199254740992', '1e2', '']) {
    assert.deepEqual(parseNavigationHash(`#/gesichter-pruefen/gruppe/${id}`), { page: 'review' });
  }
  assert.deepEqual(parseNavigationHash('#/unknown'), { page: 'people' });
});
