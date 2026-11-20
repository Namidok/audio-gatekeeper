// Unit tests for the pure display helpers. No test framework needed: `npm test` (Node 22+).
import assert from "node:assert/strict";
import { test } from "node:test";

import { db, formatDuration, formatTime, highFrequencies, matchedGains, percent, spokenTime } from "../lib/format.ts";

test("formatTime counts whole seconds and survives bad input", () => {
  assert.equal(formatTime(0), "0:00");
  assert.equal(formatTime(59.9), "0:59");
  assert.equal(formatTime(61), "1:01");
  assert.equal(formatTime(Number.NaN), "0:00");
  assert.equal(formatTime(Number.POSITIVE_INFINITY), "0:00");
});

test("formatDuration rounds the total first, never 0:60", () => {
  assert.equal(formatDuration(59.6), "1:00");
  assert.equal(formatDuration(187.4), "3:07");
});

test("spokenTime is screen-reader friendly", () => {
  assert.equal(spokenTime(62), "1 minute 2 seconds");
  assert.equal(spokenTime(1), "1 second");
});

test("matchedGains turns the louder version down by the LUFS difference", () => {
  assert.deepEqual(matchedGains(false, -8, -14), { original: 1, enhanced: 1 });
  assert.deepEqual(matchedGains(true, null, -14), { original: 1, enhanced: 1 });
  const louderOriginal = matchedGains(true, -8, -14);
  assert.equal(louderOriginal.enhanced, 1);
  assert.ok(Math.abs(20 * Math.log10(louderOriginal.original) + 6) < 1e-9); // exactly -6 dB
  const quieterOriginal = matchedGains(true, -20, -14);
  assert.equal(quieterOriginal.original, 1);
  assert.ok(Math.abs(20 * Math.log10(quieterOriginal.enhanced) + 6) < 1e-9);
});

test("number formatting is honest about missing values", () => {
  assert.equal(db(null, "LUFS"), "Not measured");
  assert.equal(db(-14.04, "LUFS"), "-14.0 LUFS");
  assert.equal(percent(0), "0%");
  assert.equal(percent(0.00001), "< 0.01%");
  assert.equal(highFrequencies(22_050), "Full range");
  assert.equal(highFrequencies(11_000), "Up to 11.0 kHz");
});
