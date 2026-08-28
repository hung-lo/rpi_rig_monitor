(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  function text(value) { return value === null || value === undefined || value === "" ? "—" : String(value); }
  function formatEta(seconds) {
    if (seconds === null || seconds === undefined || isNaN(Number(seconds))) return "—";
    var total = Math.max(0, Math.round(Number(seconds))), h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    return (h ? String(h).padStart(2, "0") + ":" : "") + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }
  function pair(label, value) { return '<div><strong>' + label + '</strong><div class="value">' + text(value) + '</div></div>'; }
  function render(data) {
    $("session-id").textContent = text(data.session_id); $("protocol").textContent = text(data.protocol);
    $("connection").className = "status " + (data.connected ? "live" : "stale"); $("connection").lastElementChild.textContent = data.status || "STALE";
    $("image").textContent = text(data.image); $("stimulus-role").textContent = text(data.stimulus_role);
    $("phase").textContent = text(data.phase); $("trial").textContent = data.trial == null ? "—" : text(data.trial) + (data.total_trials == null ? "" : " / " + data.total_trials);
    $("block").textContent = data.block == null ? "—" : text(data.block) + (data.total_blocks == null ? "" : " / " + data.total_blocks); $("eta").textContent = formatEta(data.eta_sec);
    var last = data.last_trial, lastNode = $("last-trial");
    if (!last) { lastNode.className = "last-trial empty"; lastNode.textContent = "No completed trial received"; } else {
      lastNode.className = "last-trial"; lastNode.innerHTML = pair("Trial", last.trial) + pair("Condition", last.stimulus_role) + pair("Reward", last.reward_scheduled ? (last.reward_omission ? "Reward omitted" : "Reward delivered") : "No reward") + pair("Anticipatory lick", last.anticipatory_lick ? "YES" : "NO");
    }
    var rows = (data.recent_trials || []).slice(0, 20), body = $("history"); body.innerHTML = "";
    if (!rows.length) { body.innerHTML = '<tr><td colspan="5" class="empty">No trial history</td></tr>'; return; }
    rows.forEach(function (trial) { var reward = trial.reward_scheduled ? "Yes" : "No"; if (trial.reward_omission) reward = "Scheduled"; body.insertAdjacentHTML("beforeend", '<tr><td>' + text(trial.trial) + '</td><td>' + text(trial.stimulus_role) + '</td><td>' + reward + '</td><td>' + (trial.reward_omission ? "Yes" : "No") + '</td><td class="' + (trial.anticipatory_lick ? "yes" : "no") + '">' + (trial.anticipatory_lick ? "YES" : "NO") + '</td></tr>'); });
  }
  function poll() { fetch("/api/state", { cache: "no-store" }).then(function (response) { return response.json(); }).then(render).catch(function () { $("connection").className = "status stale"; $("connection").lastElementChild.textContent = "STALE"; }); }
  poll(); window.setInterval(poll, 500);
}());
