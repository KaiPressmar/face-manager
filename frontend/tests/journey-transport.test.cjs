const test = require('node:test');
const assert = require('node:assert/strict');
const { suggestJourneyTransport } = require('./load-typescript.cjs')('src/components/map/journeyTransport.ts');

const stage = (latitude, longitude, value) => ({ latitude, longitude, start_utc: value, end_utc: value, uncertain: false });
const time = (value) => `2024-07-13T${value}:00Z`;

test('transport hint leaves road versus rail open and accepts plausible walking and flight times', () => {
  const stephansplatz = stage(48.2085, 16.3720, time('12:00'));
  const museumsquartier = stage(48.2037, 16.3593, time('12:16'));
  const berlin = stage(52.52, 13.405, time('08:00'));
  const parisFlight = stage(48.8566, 2.3522, time('09:45'));
  const munichSurface = stage(48.135, 11.581, time('12:15'));
  assert.equal(suggestJourneyTransport(stephansplatz, museumsquartier), 'walk');
  assert.equal(suggestJourneyTransport(berlin, parisFlight), 'plane');
  assert.equal(suggestJourneyTransport(berlin, munichSurface), 'road-or-rail');
});

test('time gaps, incompatible clocks and impossible speeds produce no suggestion', () => {
  const berlin = stage(52.52, 13.405, time('08:00'));
  const paris = stage(48.8566, 2.3522, time('09:45'));
  for (const to of [{ ...paris, uncertain: true }, { ...paris, start_utc: null },
    { ...paris, start_utc: time('07:00') }, { ...paris, start_utc: time('08:01') },
    { ...paris, start_utc: time('23:00') }, { ...paris, start_utc: '2024-07-13T09:45:00' },
    { ...paris, start_utc: time('11:00') }]) {
    assert.equal(suggestJourneyTransport(berlin, to), undefined);
  }
});

test('uses departure from the previous stop and a short path across the dateline', () => {
  const before = stage(-16, 179.998, time('08:00'));
  const after = stage(-16, -179.998, time('13:09'));
  assert.equal(suggestJourneyTransport({ ...before, end_utc: time('13:00') }, after), 'walk');
  assert.equal(suggestJourneyTransport(before, after), undefined);
});
