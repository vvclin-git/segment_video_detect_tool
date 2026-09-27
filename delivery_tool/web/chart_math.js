(() => {
  'use strict';

  function reduceSeriesIndices(rows, firstIndex, lastIndex, field, pixelWidth) {
    if (!Array.isArray(rows) || !rows.length || lastIndex < firstIndex) return [];
    const first = Math.max(0, Math.floor(firstIndex));
    const last = Math.min(rows.length - 1, Math.floor(lastIndex));
    const width = Math.max(1, Math.floor(pixelWidth));
    const bucketSize = Math.max(1, Math.ceil((last - first + 1) / width));
    const output = [];
    for (let low = first; low <= last; low += bucketSize) {
      const high = Math.min(last, low + bucketSize - 1);
      let minIndex = low, maxIndex = low;
      for (let index = low + 1; index <= high; index++) {
        if (rows[index][field] < rows[minIndex][field]) minIndex = index;
        if (rows[index][field] > rows[maxIndex][field]) maxIndex = index;
      }
      [...new Set([low, minIndex, maxIndex, high])].sort((a, b) => a - b).forEach(index => output.push(index));
    }
    return output;
  }

  const api = {reduceSeriesIndices};
  globalThis.SeaTrialChartMath = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})();
