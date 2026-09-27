const test = require('node:test');
const assert = require('node:assert/strict');
const {reduceSeriesIndices} = require('../delivery_tool/web/chart_math.js');

test('min/max reduction keeps one-frame Raw and Stable pulses in a large sequence', () => {
  const count = 100_000;
  const rawPulse = 43_210;
  const stablePulse = 87_654;
  const rows = Array.from({length: count}, (_, index) => ({
    raw: index === rawPulse ? 1 : 0,
    stable: index === stablePulse ? 1 : 0,
    rolling: index === rawPulse ? 1 : 0
  }));

  const raw = reduceSeriesIndices(rows, 0, count - 1, 'raw', 800);
  const stable = reduceSeriesIndices(rows, 0, count - 1, 'stable', 800);
  const rolling = reduceSeriesIndices(rows, 0, count - 1, 'rolling', 800);

  assert.ok(raw.includes(rawPulse));
  assert.ok(stable.includes(stablePulse));
  assert.ok(rolling.includes(rawPulse));
  assert.ok(raw.length <= 800 * 4);
  assert.deepEqual(raw, [...raw].sort((a, b) => a - b));
});

test('reduction preserves both extrema and the end values within a selected range', () => {
  const rows = Array.from({length: 1000}, (_, index) => ({value: index === 501 ? 1 : index === 502 ? 0 : 0.5}));
  const selected = reduceSeriesIndices(rows, 500, 509, 'value', 1);
  assert.ok(selected.includes(500));
  assert.ok(selected.includes(501));
  assert.ok(selected.includes(502));
  assert.ok(selected.includes(509));
  assert.ok(selected.every(index => index >= 500 && index <= 509));
});
