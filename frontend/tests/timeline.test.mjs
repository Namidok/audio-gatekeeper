// What the pipeline timeline shows in each situation, including re-queued jobs.
import assert from "node:assert/strict";
import { test } from "node:test";

import { timelineView } from "../lib/timeline.ts";

const repairNeeded = { flags: { declip: false, upsample: true }, has_signal: true };
const healthy = { flags: { declip: false, upsample: false }, has_signal: true };

function job(overrides) {
  return { status: "queued", queue: "default", attempts: 0, max_attempts: 3, message: null, diagnostics: null, processing: null, ...overrides };
}
const states = (view) => view.nodes.map((n) => n.state).join(" ");

test("first wait: nothing started", () => {
  const view = timelineView(job({ message: "Waiting for a worker" }), null);
  assert.equal(states(view), "pending pending pending pending pending pending");
  assert.equal(view.note, "Waiting for a worker");
});

test("handed to the GPU worker: analysis done, waiting at repair", () => {
  const view = timelineView(job({ queue: "gpu", attempts: 0, diagnostics: repairNeeded }), "diagnosing");
  assert.equal(states(view), "done done current pending pending pending");
  assert.equal(view.nodes[2].caption, "Waiting for GPU");
  assert.match(view.note, /Waiting for a GPU worker/);
});

test("GPU worker restarts from Fetch: the note explains the restart", () => {
  const view = timelineView(job({ status: "downloading", queue: "gpu", attempts: 1, diagnostics: repairNeeded }), "downloading");
  assert.equal(states(view), "current pending pending pending pending pending");
  assert.match(view.note, /fetches and checks the file again/);
});

test("retry of a job that needed no repair: nothing claimed as done, never 'waiting at repair'", () => {
  const view = timelineView(job({ attempts: 1, diagnostics: healthy }), "mastering");
  assert.equal(states(view), "pending pending pending pending pending pending");
  assert.match(view.note, /attempt 2 of 3/);
  assert.ok(!view.nodes.some((n) => n.caption.includes("Waiting")));
});

test("second attempt running shows its number", () => {
  const view = timelineView(job({ status: "mastering", attempts: 2, diagnostics: healthy }), "mastering");
  assert.equal(view.note, "Retrying: attempt 2 of 3.");
  assert.equal(view.nodes[2].state, "skipped");
});

test("healthy track: repair skipped, then done", () => {
  const view = timelineView(job({ status: "completed", attempts: 1, diagnostics: healthy, processing: { outcome: "bypassed" } }), "uploading");
  assert.equal(states(view), "done done skipped done done done");
  assert.equal(view.note, null);
});

test("failure is shown where it happened, even after a re-queue", () => {
  const view = timelineView(job({ status: "failed", attempts: 3, diagnostics: healthy }), "mastering");
  assert.equal(states(view), "done done skipped failed pending pending");
});
