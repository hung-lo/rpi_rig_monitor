(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  function text(value) { return value === null || value === undefined || value === "" ? "—" : String(value); }
  function formatEta(seconds) {
    if (seconds === null || seconds === undefined || isNaN(Number(seconds))) return "—";
    var total = Math.max(0, Math.round(Number(seconds))), h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    return (h ? String(h).padStart(2, "0") + ":" : "") + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function addPair(parent, label, value) {
    var wrapper = document.createElement("div"), heading = document.createElement("strong"), content = document.createElement("div");
    heading.textContent = label; content.className = "value"; content.textContent = text(value); wrapper.appendChild(heading); wrapper.appendChild(content); parent.appendChild(wrapper);
  }
  function addCell(row, value, className) { var cell = document.createElement("td"); cell.textContent = text(value); if (className) cell.className = className; row.appendChild(cell); }
  var requestedImage = null;
  var SVG_NS = "http://www.w3.org/2000/svg";
  function svgElement(name, attributes, content) {
    var element = document.createElementNS(SVG_NS, name);
    Object.keys(attributes || {}).forEach(function (key) { element.setAttribute(key, attributes[key]); });
    if (content !== undefined) element.textContent = content;
    return element;
  }
  function renderLickTimeline(trial) {
    var svg = $("lick-timeline"), minTime = -0.5, maxTime = 4.0, stimulusOffset = 1.5, left = 30, right = 570, baseline = 138;
    clear(svg);
    function xFor(time) { return left + ((time - minTime) / (maxTime - minTime)) * (right - left); }
    if (!trial) {
      svg.appendChild(svgElement("text", { x: 300, y: 125, "text-anchor": "middle", "class": "timeline-message" }, "No completed trial received"));
      return;
    }
    // This is protocol-intended epoch timing for operator feedback, not photodiode measurement.
    svg.appendChild(svgElement("path", { d: "M " + left + " 49 H " + xFor(0) + " V 30 H " + xFor(stimulusOffset) + " V 49 H " + right, "class": "stimulus-epoch" }));
    svg.appendChild(svgElement("text", { x: (xFor(0) + xFor(stimulusOffset)) / 2, y: 20, "text-anchor": "middle", "class": "timeline-message" }, "VISUAL STIMULUS"));
    svg.appendChild(svgElement("rect", { x: xFor(0), y: 74, width: xFor(1) - xFor(0), height: 48, "class": "anticipatory-band" }));
    svg.appendChild(svgElement("text", { x: (xFor(0) + xFor(1)) / 2, y: 67, "text-anchor": "middle", "class": "timeline-message" }, "ANTICIPATORY 0–1 s"));
    svg.appendChild(svgElement("line", { x1: left, y1: baseline, x2: right, y2: baseline, "class": "axis" }));
    [[-0.5, "-0.5"], [0, "0"], [1, "1.0"], [4, "4.0 s"]].forEach(function (label) {
      var x = xFor(label[0]);
      svg.appendChild(svgElement("line", { x1: x, y1: baseline - 5, x2: x, y2: baseline + 5, "class": "axis-tick" }));
      svg.appendChild(svgElement("text", { x: x, y: 181, "text-anchor": "middle" }, label[1]));
    });
    var lickTimes = Array.isArray(trial.lick_times_sec) ? trial.lick_times_sec : null;
    if (lickTimes) {
      var numericLicks = lickTimes.filter(function (value) { return typeof value === "number" && isFinite(value); });
      var validLicks = numericLicks.filter(function (value) { return value >= minTime && value <= maxTime; });
      validLicks.forEach(function (value) { var x = xFor(value); svg.appendChild(svgElement("line", { x1: x, y1: baseline - 18, x2: x, y2: baseline + 18, "class": "lick-tick" })); });
      if (!validLicks.length) {
        var message = lickTimes.length === 0 ? "No licks" : (numericLicks.length ? "No licks in displayed window" : "Lick timing unavailable");
      svg.appendChild(svgElement("text", { x: 300, y: 130, "text-anchor": "middle", "class": "timeline-message" }, message));
      }
    } else {
      svg.appendChild(svgElement("text", { x: 300, y: 130, "text-anchor": "middle", "class": "timeline-message" }, "Lick timing unavailable"));
    }
    var stimX = xFor(0);
    svg.appendChild(svgElement("line", { x1: stimX, y1: 78, x2: stimX, y2: 161, "class": "stim-marker" }));
    svg.appendChild(svgElement("text", { x: stimX + 5, y: 96, "class": "marker-label stim-label" }, "STIM"));
    if (trial.reward_omission === true || trial.reward_delivered === true) {
      var rewardX = xFor(1), rewardClass = trial.reward_omission === true ? "omission-marker" : "reward-marker", rewardLabel = trial.reward_omission === true ? "OMISSION" : "REWARD";
      svg.appendChild(svgElement("line", { x1: rewardX, y1: 78, x2: rewardX, y2: 161, "class": rewardClass }));
      svg.appendChild(svgElement("text", { x: rewardX + 5, y: 96, "class": "marker-label " + (trial.reward_omission === true ? "omission-label" : "reward-label") }, rewardLabel));
    }
  }
  function setPreview(filename) {
    var image = $("preview-image"), message = $("preview-message");
    if (!filename) { requestedImage = null; image.hidden = true; image.removeAttribute("src"); message.hidden = false; return; }
    if (filename === requestedImage) return;
    requestedImage = filename; image.hidden = true; message.hidden = false;
    image.onload = function () { image.hidden = false; message.hidden = true; };
    image.onerror = function () { image.hidden = true; message.hidden = false; };
    image.src = "/stimulus-image/" + encodeURIComponent(filename);
  }
  function rewardLabel(trial) {
    if (trial.reward_omission === true) return "OMITTED";
    if (trial.reward_delivered === true) return "DELIVERED";
    if (trial.reward_scheduled === false) return "NO REWARD";
    return "UNKNOWN";
  }
  function trialSummary(trial) {
    var condition;
    if (trial.reward_omission === true) condition = "Reward omission";
    else if (trial.reward_scheduled === true) condition = "Rewarded cue";
    else if (trial.reward_scheduled === false) condition = "Unrewarded cue";
    else if (trial.stimulus_role) condition = String(trial.stimulus_role);
    else return "Trial summary unavailable";
    if (typeof trial.anticipatory_lick !== "boolean") return condition;
    return condition + " + " + (trial.reward_scheduled === false ? (trial.anticipatory_lick ? "lick" : "no lick") : (trial.anticipatory_lick ? "anticipatory lick" : "no anticipatory lick"));
  }
  function render(data) {
    $("session-id").textContent = text(data.session_id); $("protocol").textContent = text(data.protocol);
    $("connection").className = "status " + (data.connected ? "live" : "stale"); $("connection").lastElementChild.textContent = data.status || "STALE";
    $("image").textContent = text(data.image); $("stimulus-role").textContent = text(data.stimulus_role);
    setPreview(data.image);
    $("phase").textContent = text(data.phase); $("trial").textContent = data.trial == null ? "—" : text(data.trial) + (data.total_trials == null ? "" : " / " + data.total_trials);
    $("block").textContent = data.block == null ? "—" : text(data.block) + (data.total_blocks == null ? "" : " / " + data.total_blocks); $("eta").textContent = formatEta(data.eta_sec);
    var last = data.last_trial, lastNode = $("last-trial"), summaryNode = $("last-summary");
    renderLickTimeline(last);
    clear(lastNode);
    if (!last) { lastNode.className = "last-trial empty"; lastNode.textContent = "No completed trial received"; summaryNode.textContent = "No completed trial received"; } else {
      lastNode.className = "last-trial"; summaryNode.textContent = trialSummary(last); addPair(lastNode, "Trial", last.trial); addPair(lastNode, "Condition", last.stimulus_role); addPair(lastNode, "Reward", rewardLabel(last)); addPair(lastNode, "Anticipatory lick", last.anticipatory_lick ? "YES" : "NO");
    }
    var rows = (data.recent_trials || []).slice(0, 20), body = $("history"); clear(body);
    if (!rows.length) { var empty = document.createElement("tr"); var emptyCell = document.createElement("td"); emptyCell.colSpan = 5; emptyCell.className = "empty"; emptyCell.textContent = "No trial history"; empty.appendChild(emptyCell); body.appendChild(empty); return; }
    rows.forEach(function (trial) { var row = document.createElement("tr"); addCell(row, trial.trial); addCell(row, trial.stimulus_role); addCell(row, rewardLabel(trial)); addCell(row, trial.reward_omission ? "Yes" : "No"); addCell(row, trial.anticipatory_lick ? "YES" : "NO", trial.anticipatory_lick ? "yes" : "no"); body.appendChild(row); });
  }
  function poll() { fetch("/api/state", { cache: "no-store" }).then(function (response) { return response.json(); }).then(render).catch(function () { $("connection").className = "status stale"; $("connection").lastElementChild.textContent = "STALE"; }); }
  poll(); window.setInterval(poll, 500);
}());
