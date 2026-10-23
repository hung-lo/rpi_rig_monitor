const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "static/dashboard.js"), "utf8");
const template = fs.readFileSync(path.join(root, "templates/dashboard.html"), "utf8");
const css = fs.readFileSync(path.join(root, "static/dashboard.css"), "utf8");

class Element {
  constructor(tagName, id) {
    this.tagName = tagName || "div";
    this.id = id || "";
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.className = "";
    this.text = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.title = "";
    this.open = false;
    this.parentNode = null;
    this.src = "";
  }
  get firstChild() { return this.children[0] || null; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  get textContent() { return this.text; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get innerHTML() { return this.text; }
  set innerHTML(value) { this.textContent = value; }
  get classList() {
    const element = this;
    return {
      add(...names) { const classes = new Set(element.className.split(/\s+/).filter(Boolean)); names.forEach((name) => classes.add(name)); element.className = [...classes].join(" "); },
      remove(...names) { const classes = new Set(element.className.split(/\s+/).filter(Boolean)); names.forEach((name) => classes.delete(name)); element.className = [...classes].join(" "); },
      contains(name) { return element.className.split(/\s+/).includes(name); },
      toggle(name, force) { const classes = new Set(element.className.split(/\s+/).filter(Boolean)); const add = force === undefined ? !classes.has(name) : !!force; if (add) classes.add(name); else classes.delete(name); element.className = [...classes].join(" "); return add; },
    };
  }
  checkValidity() {
    if (this.required && !this.value) return false;
    if (this.value === "") return !this.required;
    const value = Number(this.value);
    if (this.type === "number" && (!Number.isInteger(value) || value < Number(this.min) || value > Number(this.max))) return false;
    if (this.pattern && !new RegExp("^(?:" + this.pattern + ")$").test(this.value)) return false;
    return true;
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    if (this.tagName === "select" && this.children.length === 1) this.value = child.value;
    return child;
  }
  removeChild(child) {
    this.children.splice(this.children.indexOf(child), 1);
    child.parentNode = null;
    if (this.tagName === "select" && !this.children.length) this.value = "";
    return child;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === "src") this.src = String(value); }
  removeAttribute(name) { delete this.attributes[name]; if (name === "src") this.src = ""; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name) { (this.listeners[name] || []).forEach((callback) => callback({ target: this })); }
}

class OptionElement extends Element {
  constructor(label, value) { super("option"); this.textContent = label; this.value = value; }
}

const protocols = ["natural_images", "reward_conditioning", "spout_training", "fake"].map((protocol_id) => ({
  protocol_id,
  display_name: protocol_id,
  available: true,
  runtime_fields: [],
  presets: [{ preset_id: protocol_id + "_preset", display_name: "Standard", available: true,
    parameters: protocol_id === "natural_images" ? [{ name: "image_count", display_name: "Image count",
      type: "integer", default: 5, minimum: 1, maximum: 2800, units: "images",
      description: "Number of images." }] : [] }],
}));

function baseState(overrides = {}) {
  return Object.assign({
    protocol: null,
    session_id: null,
    phase: null,
    trial: null,
    total_trials: null,
    block: null,
    total_blocks: null,
    image: null,
    stimulus_role: null,
    stimulus_state: null,
    lick_state: null,
    lick_aggregate_available: false,
    recent_trials: [],
    recent_events: [],
    last_trial: null,
    connected: false,
    status: "NO TELEMETRY",
    telemetry_freshness: "NONE",
    last_packet_age_sec: null,
    control_enabled: true,
    rig: { session: { id: null, protocol: null, state: "IDLE", elapsed_s: null, eta_s: null },
      hardware: {}, integrity: {}, protocol: {} },
    supervisor: { available: true, session_id: null, protocol_id: null, protocol_display_name: null,
      preset_display_name: null, subject_id: null, state: "IDLE", elapsed_s: null, eta_s: null,
      capabilities: {}, process_running: false, control_connected: true },
  }, overrides);
}

async function createDashboard(initialState, choices = protocols, profileResponse = []) {
  const elements = new Map();
  const getElement = (id, tagName = "div") => {
    if (!elements.has(id)) elements.set(id, new Element(tagName, id));
    return elements.get(id);
  };
  for (const match of template.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const tag = match[0];
    const tagName = /^<([a-z0-9-]+)/i.exec(tag)[1].toLowerCase();
    const element = getElement(match[1], tagName);
    element.hidden = /\shidden(?:\s|>)/.test(tag);
    element.open = /\sopen(?:\s|>)/.test(tag);
    const className = /\bclass="([^"]*)"/.exec(tag);
    if (className) element.className = className[1];
  }
  const preview = getElement("preview");
  getElement("preview-image").parentNode = preview;
  ["connection", "supervisor-connection"].forEach((id) => {
    const element = getElement(id);
    element.appendChild(new Element("span"));
    element.appendChild(new Element("span"));
  });

  let state = initialState;
  let intervalCallback = null;
  const document = {
    getElementById(id) { return getElement(id); },
    createElement(tag) { return new Element(tag); },
    createElementNS(_namespace, tag) { return new Element(tag); },
  };
  const requests = [];
  const fetch = (url, options = {}) => {
    requests.push({ url, options });
    const payload = url === "/api/protocols" ? choices :
      (url.indexOf("/api/profiles?") === 0 ? profileResponse : state);
    return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
  };
  const storage = new Map();
  const localStorage = { getItem(key) { return storage.has(key) ? storage.get(key) : null; },
    setItem(key, value) { storage.set(key, String(value)); }, removeItem(key) { storage.delete(key); } };
  const window = { crypto: { randomUUID: () => "test-request" }, confirm: () => true,
    setInterval(callback) { intervalCallback = callback; return 1; } };
  vm.runInNewContext(source, { document, fetch, Option: OptionElement, window, localStorage,
    console, encodeURIComponent, Date });
  const flush = async () => { for (let index = 0; index < 4; index += 1) await new Promise(setImmediate); };
  await flush();
  return {
    element: getElement,
    hasElement(id) { return elements.has(id); },
    setState(value) { state = value; },
    async poll() { intervalCallback(); await flush(); },
    async flush() { await flush(); },
    requests,
  };
}

function parameterInput(app, name) {
  const wrapper = app.element("parameter-fields").children.find((item) =>
    item.children.some((child) => child.id === "parameter-" + name));
  return wrapper && wrapper.children.find((child) => child.id === "parameter-" + name);
}

test("Natural Images shows the selected preview, sequence and independent lick channels", async () => {
  const sessionId = "session-natural-7";
  const app = await createDashboard(baseState({
    protocol: "natural-images", session_id: sessionId, connected: true, status: "LIVE",
    telemetry_freshness: "FRESH", last_packet_age_sec: 0.1,
    stimulus_state: { phase: "TASK", stimulus_kind: "natural_image", image_id: "natimg_center_0007",
      image_filename: "natimg_center_0007.png", sequence_index: 3, sequence_total: 5 },
    lick_state: { channels: {
      left: { available: true, status: "available", contact_active: true, last_lick_age_sec: 0.2,
        recent_lick_count_2s: 2, total_lick_onset_count_session: 8 },
      right: { available: true, status: "available", contact_active: false, last_lick_age_sec: 3,
        recent_lick_count_2s: 0, total_lick_onset_count_session: 1 },
    } },
    rig: { session: { id: sessionId, protocol: "natural-images", state: "RUNNING" }, hardware: {}, integrity: {},
      protocol: { phase: "TASK", sequence_index: 3, image: "natimg_center_0007" } },
    supervisor: { ...baseState().supervisor, session_id: sessionId, protocol_id: "natural_images",
      protocol_display_name: "Natural Images", preset_display_name: "Pi5 simulation", subject_id: "mouse_7",
      state: "RUNNING", process_running: true, control_connected: true },
  }));
  assert.equal(app.element("preview-image").src, "/api/stimulus-image/session-natural-7/natimg_center_0007.png");
  assert.equal(app.element("stimulus-filename").textContent, "natimg_center_0007.png");
  assert.equal(app.element("trial").textContent, "3 / 5");
  assert.equal(app.element("lick-left-contact").textContent, "Active");
  assert.equal(app.element("lick-left-recent").textContent, "2");
  assert.equal(app.element("lick-right-contact").textContent, "Inactive");
  assert.equal(app.element("lick-right-total").textContent, "1");
  assert.equal(app.element("outcome-summary").hidden, true);
  assert.equal(app.element("operator-subject").textContent, "mouse_7");
});

test("Natural gray and stale/terminal states clear the prior image and live contact", async () => {
  const sessionId = "natural-gray";
  const fresh = baseState({ protocol: "natural_images", session_id: sessionId, telemetry_freshness: "FRESH",
    connected: true, stimulus_state: { phase: "TASK", stimulus_kind: "natural_image",
      image_id: "natimg_center_0002", image_filename: "natimg_center_0002.png", sequence_index: 1 },
    lick_state: { channels: { left: { available: true, status: "available", contact_active: true,
      last_lick_age_sec: 0.1, recent_lick_count_2s: 1, total_lick_onset_count_session: 1 } } },
    rig: { session: { id: sessionId, protocol: "natural-images", state: "RUNNING" }, hardware: {}, integrity: {}, protocol: {} },
    supervisor: { ...baseState().supervisor, session_id: sessionId, state: "RUNNING", process_running: true,
      control_connected: true } });
  const app = await createDashboard(fresh);
  assert.match(app.element("preview-image").src, /natimg_center_0002\.png$/);

  app.setState({ ...fresh, stimulus_state: { phase: "ITI", stimulus_kind: "gray" } });
  await app.poll();
  assert.equal(app.element("preview-image").src, "");
  assert.equal(app.element("image").textContent, "Gray");

  app.setState({ ...fresh, telemetry_freshness: "STALE", connected: false });
  await app.poll();
  assert.equal(app.element("stimulus-role").textContent, "UNKNOWN");
  assert.equal(app.element("lick-left-contact").textContent, "Unknown (stale)");
  assert.equal(app.element("lick-left-onset").textContent, "—");
  assert.equal(app.element("lick-left-recent").textContent, "1 (stale)");

  app.setState({ ...fresh, telemetry_freshness: "STALE", supervisor: { ...fresh.supervisor, state: "COMPLETE" } });
  await app.poll();
  assert.equal(app.element("stimulus-role").textContent, "Session ended");
  assert.equal(app.element("stimulus-preview-empty").textContent, "Session ended");
  assert.equal(app.element("lick-left-status").textContent, "Session ended");
  for (const terminal of ["ERROR", "ABORTED"]) {
    app.setState({ ...fresh, telemetry_freshness: "FRESH", supervisor: { ...fresh.supervisor, state: terminal } });
    await app.poll();
    assert.equal(app.element("stimulus-role").textContent, "Session ended");
  }
});

test("Reward conditioning keeps the current trial and last outcome in the main task card", async () => {
  const app = await createDashboard(baseState({ protocol: "reward_conditioning", session_id: "reward-1",
    phase: "TASK", trial: 4, total_trials: 20, contingency_phase: "reversal",
    last_trial: { trial: 3, stimulus_role: "rewarded", reward_scheduled: true, reward_delivered: true,
      reward_contacted: true, anticipatory_lick: true },
    telemetry_freshness: "FRESH", connected: true }));
  assert.equal(app.element("phase").textContent, "TASK");
  assert.equal(app.element("trial").textContent, "4 / 20");
  assert.equal(app.element("legacy-behavior-view").hidden, false);
  assert.equal(app.element("last-summary").textContent, "Rewarded cue + anticipatory lick");
  assert.equal(app.element("outcome-summary").hidden, false);
});

test("Spout training prioritizes reward progress and only reports present aggregate lick data", async () => {
  const state = baseState({ protocol: "spout_training", session_id: "spout-1", phase: "TRAINING",
    training_reward_index: 3, maximum_training_rewards: 10, next_reward_in_sec: 2.5,
    total_lick_onset_count_session: 6, recent_lick_count_2s: 2, licking_active: true,
    lick_aggregate_available: true, telemetry_freshness: "FRESH", connected: true });
  const app = await createDashboard(state);
  assert.equal(app.element("spout-session-view").hidden, false);
  assert.equal(app.element("spout-reward-progress").textContent, "3 / 10");
  assert.equal(app.element("spout-next-reward").textContent, "2.5 s");
  assert.equal(app.element("spout-total-licks").textContent, "6");
  assert.equal(app.element("spout-lick-status").textContent, "LICKING");

  app.setState(baseState({ protocol: "spout_training", phase: "TRAINING", telemetry_freshness: "FRESH" }));
  await app.poll();
  assert.equal(app.element("spout-total-licks").textContent, "—");
  assert.equal(app.element("lick-left-recent").textContent, "—");
});

test("Unknown and missing telemetry never appear live or fabricate lick counts", async () => {
  const app = await createDashboard(baseState());
  assert.equal(app.element("connection").lastElementChild.textContent, "NO TELEMETRY");
  assert.equal(app.element("lick-left-status").textContent, "No telemetry");
  assert.equal(app.element("lick-left-contact").textContent, "Unknown");
  assert.equal(app.element("lick-left-recent").textContent, "—");
  assert.equal(app.element("lick-right-total").textContent, "—");
  assert.equal(app.element("lick-aggregate-summary").hidden, true);
});

test("Fake task retains its generic trial view without inventing reward metrics", async () => {
  const app = await createDashboard(baseState({ protocol: "fake", session_id: "fake-1", phase: "RUNNING",
    trial: 2, total_trials: 8, telemetry_freshness: "FRESH", connected: true }));
  assert.equal(app.element("session-title").textContent, "TASK / TRIAL");
  assert.equal(app.element("phase").textContent, "RUNNING");
  assert.equal(app.element("trial").textContent, "2 / 8");
  assert.equal(app.element("legacy-behavior-view").hidden, true);
  assert.equal(app.element("lick-left-status").textContent, "Not available");
});

test("Controls remain visible and follow runtime capabilities and terminal state", async () => {
  const sessionId = "control-1";
  const running = baseState({ protocol: "natural_images", session_id: sessionId,
    rig: { session: { id: sessionId, protocol: "natural-images", state: "RUNNING" }, hardware: {}, integrity: {}, protocol: {} },
    supervisor: { ...baseState().supervisor, session_id: sessionId, state: "RUNNING", process_running: true,
      control_connected: true, capabilities: { start: false, stop: true, abort: true } } });
  const app = await createDashboard(running);
  assert.equal(app.element("stop-button").hidden, false);
  assert.equal(app.element("abort-button").hidden, false);
  assert.equal(app.element("stop-button").disabled, true);
  app.element("control-token").value = "operator-token";
  app.element("control-token").dispatch("input");
  await app.flush();
  assert.equal(app.element("start-button").disabled, true);
  assert.equal(app.element("stop-button").disabled, false);
  assert.equal(app.element("abort-button").disabled, false);

  app.setState({ ...running, supervisor: { ...running.supervisor, state: "COMPLETE", process_running: false,
    capabilities: { start: false, stop: false, abort: false } },
    rig: { ...running.rig, session: { ...running.rig.session, state: "COMPLETE" } } });
  await app.poll();
  assert.equal(app.element("stop-button").disabled, true);
  assert.equal(app.element("abort-button").disabled, true);
  assert.equal(app.element("prepare-button").disabled, false);
});

test("Protocol switching changes the preset list without moving controls", async () => {
  const app = await createDashboard(baseState());
  app.element("protocol-select").value = "spout_training";
  app.element("protocol-select").dispatch("change");
  assert.equal(app.element("preset-select").value, "spout_training_preset");
  assert.equal(app.element("prepare-button").hidden, false);
  assert.equal(app.element("stop-button").hidden, false);
});

test("Natural Images settings expose only validated image count and mark edits for future sessions", async () => {
  const app = await createDashboard(baseState());
  const input = parameterInput(app, "image_count");
  assert.equal(input.value, "5");
  assert.match(app.element("resolved-settings-summary").textContent, /Image count: 5 images/);
  input.value = "12";
  input.dispatch("input");
  assert.equal(app.element("settings-dirty").textContent, "Unsaved changes");
  assert.match(app.element("resolved-settings-summary").textContent, /Image count: 12 images/);
  assert.equal(app.element("parameter-fields").children.some((wrapper) =>
    wrapper.children.some((child) => child.id === "parameter-rule_switch_block")), false);
  assert.match(app.element("settings-future-note").textContent, /future session only/);
  assert.equal(app.element("profile-save-button").disabled, true);
  app.element("control-token").value = "operator-token";
  app.element("control-token").dispatch("input");
  await app.flush();
  app.element("profile-name").value = "short run";
  app.element("profile-name").dispatch("input");
  assert.equal(app.element("profile-save-button").disabled, false);
});

test("Saved profile loads validated values and returns explicitly to preset defaults", async () => {
  const profile = { profile_id: "profile-0123456789abcdef0123456789abcdef", name: "Six images",
    version: 2, parameters: { image_count: 6 }, valid: true };
  const app = await createDashboard(baseState(), protocols, [profile]);
  app.element("session-profile-select").value = profile.profile_id;
  app.element("session-profile-select").dispatch("change");
  assert.equal(parameterInput(app, "image_count").value, "6");
  assert.equal(app.element("settings-dirty").textContent, "Profile v2");
  app.element("settings-defaults-button").dispatch("click");
  assert.equal(parameterInput(app, "image_count").value, "5");
  assert.equal(app.element("settings-dirty").textContent, "Defaults");
});

test("Invalid saved profile can be corrected for a future prepare without reusing its identity", async () => {
  const profile = { profile_id: "profile-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", name: "Old profile",
    version: 3, parameters: { image_count: "invalid" }, valid: false, error: "deprecated value" };
  const app = await createDashboard(baseState(), protocols, [profile]);
  app.element("control-token").value = "operator-token";
  app.element("control-token").dispatch("input");
  await app.flush();
  app.element("session-profile-select").value = profile.profile_id;
  app.element("session-profile-select").dispatch("change");
  assert.equal(app.element("profile-update-button").disabled, true);
  assert.match(app.element("profile-message").textContent, /save them as a new profile/);
  const input = parameterInput(app, "image_count");
  input.value = "9";
  input.dispatch("input");
  assert.equal(app.element("profile-update-button").disabled, false);
  assert.equal(app.element("prepare-button").disabled, false);
  app.element("prepare-button").dispatch("click");
  await app.flush();
  const request = app.requests.find((item) => item.url === "/api/control");
  assert.deepEqual(JSON.parse(request.options.body).parameters, { image_count: 9 });
  assert.equal(Object.prototype.hasOwnProperty.call(JSON.parse(request.options.body), "profile_ref"), false);
});

test("Manual bait stays hidden unless the engine advertises it and never reports delivery from HTTP ack", async () => {
  const sessionId = "bait-capability";
  const unsupported = await createDashboard(baseState({ protocol: "natural_images", session_id: sessionId,
    supervisor: { ...baseState().supervisor, session_id: sessionId, protocol_id: "natural_images",
      state: "READY", process_running: true, control_connected: true,
      capabilities: { start: true, stop: false, abort: true, bait_left: false } } }));
  assert.equal(unsupported.element("bait-left-button").hidden, true);
  assert.match(unsupported.element("manual-bait-explanation").textContent, /not supported/);

  const supported = await createDashboard(baseState({ protocol: "external_reward", session_id: sessionId,
    supervisor: { ...baseState().supervisor, session_id: sessionId, protocol_id: "external_reward",
      state: "READY", process_running: true, control_connected: true,
      capabilities: { start: true, abort: true, bait_left: true } } }));
  supported.element("control-token").value = "operator-token";
  supported.element("control-token").dispatch("input");
  await supported.flush();
  assert.equal(supported.element("bait-left-button").hidden, false);
  assert.equal(supported.element("bait-left-button").disabled, false);
  supported.element("bait-left-button").dispatch("click");
  await supported.flush();
  const request = supported.requests.find((item) => item.url === "/api/control");
  assert.deepEqual(JSON.parse(request.options.body), { command: "bait_left", request_id: "test-request" });
  assert.match(supported.element("control-message").textContent, /completion is not reported/);
});

test("Long session and protocol strings remain text, and diagnostic details stay open while polling", async () => {
  const sessionId = "session-" + "x".repeat(120);
  const longProtocol = "protocol " + "y".repeat(300);
  const app = await createDashboard(baseState({ session_id: sessionId, protocol: "fake",
    telemetry_freshness: "FRESH", rig: { session: { id: sessionId, protocol: longProtocol,
      state: "READY" }, hardware: {}, integrity: {}, protocol: { opaque: { value: "keep" } } },
    supervisor: { ...baseState().supervisor, session_id: sessionId, protocol_display_name: longProtocol,
      subject_id: "mouse <script> text", state: "READY", process_running: true } }));
  app.element("protocol-diagnostics").open = true;
  assert.equal(app.element("operator-session-id").textContent, sessionId);
  assert.equal(app.element("operator-session-id").title, sessionId);
  assert.equal(app.element("operator-protocol").textContent, longProtocol);
  assert.equal(app.element("operator-subject").textContent, "mouse <script> text");
  assert.match(app.element("protocol-raw").textContent, /"opaque"/);
  await app.poll();
  assert.equal(app.element("protocol-diagnostics").open, true);
  assert.match(css, /max-width:\s*1880px/);
  assert.match(css, /max-width:\s*1399px\) and \(min-width:\s*1000px/);
  assert.match(css, /max-width:\s*999px/);
  assert.match(css, /max-width:\s*600px/);
});
