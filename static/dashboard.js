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
  function rewardLabel(trial) {
    if (trial.reward_omission === true) return "OMITTED";
    if (trial.reward_delivered === true) return "DELIVERED";
    if (trial.reward_scheduled === false) return "NO REWARD";
    return "UNKNOWN";
  }
  function render(data) {
    $("session-id").textContent = text(data.session_id); $("protocol").textContent = text(data.protocol);
    $("connection").className = "status " + (data.connected ? "live" : "stale"); $("connection").lastElementChild.textContent = data.status || "STALE";
    $("image").textContent = text(data.image); $("stimulus-role").textContent = text(data.stimulus_role);
    $("phase").textContent = text(data.phase); $("trial").textContent = data.trial == null ? "—" : text(data.trial) + (data.total_trials == null ? "" : " / " + data.total_trials);
    $("block").textContent = data.block == null ? "—" : text(data.block) + (data.total_blocks == null ? "" : " / " + data.total_blocks); $("eta").textContent = formatEta(data.eta_sec);
    var last = data.last_trial, lastNode = $("last-trial");
    clear(lastNode);
    if (!last) { lastNode.className = "last-trial empty"; lastNode.textContent = "No completed trial received"; } else {
      lastNode.className = "last-trial"; addPair(lastNode, "Trial", last.trial); addPair(lastNode, "Condition", last.stimulus_role); addPair(lastNode, "Reward", rewardLabel(last)); addPair(lastNode, "Anticipatory lick", last.anticipatory_lick ? "YES" : "NO");
    }
    var rows = (data.recent_trials || []).slice(0, 20), body = $("history"); clear(body);
    if (!rows.length) { var empty = document.createElement("tr"); var emptyCell = document.createElement("td"); emptyCell.colSpan = 5; emptyCell.className = "empty"; emptyCell.textContent = "No trial history"; empty.appendChild(emptyCell); body.appendChild(empty); return; }
    rows.forEach(function (trial) { var row = document.createElement("tr"); addCell(row, trial.trial); addCell(row, trial.stimulus_role); addCell(row, rewardLabel(trial)); addCell(row, trial.reward_omission ? "Yes" : "No"); addCell(row, trial.anticipatory_lick ? "YES" : "NO", trial.anticipatory_lick ? "yes" : "no"); body.appendChild(row); });
  }
  function poll() { fetch("/api/state", { cache: "no-store" }).then(function (response) { return response.json(); }).then(render).catch(function () { $("connection").className = "status stale"; $("connection").lastElementChild.textContent = "STALE"; }); }
  poll(); window.setInterval(poll, 500);
}());
