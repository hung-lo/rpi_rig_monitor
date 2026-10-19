const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../static/dashboard.js"), "utf8");
const start = source.indexOf("  function renderNaturalLicks(data) {");
const end = source.indexOf("\n  function rewardLabel(trial)", start);
assert(start >= 0 && end > start, "renderNaturalLicks function was not found");
const elements = new Map();
const getElement = (id) => {
  if (!elements.has(id)) elements.set(id, { textContent: "", className: "" });
  return elements.get(id);
};
const finiteNumber = (value) => typeof value === "number" && Number.isFinite(value);
const renderNaturalLicks = Function(
  "$",
  "finiteNumber",
  `${source.slice(start, end)}; return renderNaturalLicks;`,
)(getElement, finiteNumber);

function render({ sessionState = "RUNNING", freshness = "FRESH", available = true, age = 0.1 } = {}) {
  renderNaturalLicks({
    telemetry_freshness: freshness,
    rig: { session: { state: sessionState } },
    lick_state: { channels: {
      left: { available, status: available ? "available" : "session_ended", last_lick_age_sec: age },
      right: { available: false, status: "not_requested" },
    } },
  });
  return getElement("lick-left-onset");
}

test("lick onset highlight clears when telemetry/session changes", () => {
  const onset = render();
  assert.equal(onset.textContent, "LICK");
  assert.equal(onset.className, "onset-active");

  render({ freshness: "STALE" });
  assert.equal(onset.className, "");

  render({ sessionState: "COMPLETE" });
  assert.equal(onset.className, "");

  render({ sessionState: "READY", available: false, age: null });
  assert.equal(onset.textContent, "—");
  assert.equal(onset.className, "");
});
