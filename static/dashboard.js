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
  function finiteNumber(value) { return typeof value === "number" && isFinite(value); }
  function formatFraction(numerator, denominator) {
    if (!finiteNumber(numerator) || !finiteNumber(denominator)) return "—";
    if (denominator === 0) return "0 / 0 (—)";
    return String(numerator) + " / " + String(denominator) + " (" + Math.round(100 * numerator / denominator) + "%)";
  }
  function formatWater(value, approximate) {
    if (!finiteNumber(value)) return "—";
    var formatted = value >= 1000 ? (value / 1000).toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1") + " mL" : String(Number(value.toFixed(2))) + " µL";
    return approximate ? "~" + formatted : formatted;
  }
  function setHidden(id, hidden) { $(id).hidden = hidden; }
  function formatInteger(value) { return finiteNumber(value) ? String(value) : "—"; }
  function formatSeconds(value) { return finiteNumber(value) ? String(Number(value.toFixed(1))) + " s" : "—"; }
  function formatAge(value) { return finiteNumber(value) ? String(Number(value.toFixed(2))) + " s ago" : "—"; }
  function formatSpoutFraction(numerator, denominator) {
    if (!finiteNumber(numerator) || !finiteNumber(denominator) || denominator === 0) return "—";
    return formatFraction(numerator, denominator);
  }
  function spoutRewardProgress(data) {
    if (finiteNumber(data.maximum_training_rewards)) return (finiteNumber(data.training_reward_index) ? data.training_reward_index : "—") + " / " + data.maximum_training_rewards;
    return formatInteger(data.training_reward_index);
  }
  var requestedImage = null;
  var requestedNaturalImage = null;
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
    var image = $("preview-image"), message = $("preview-message"), empty = $("stimulus-preview-empty");
    if (!filename) { requestedImage = null; image.hidden = true; image.removeAttribute("src"); message.hidden = true; empty.hidden = false; return; }
    if (filename === requestedImage) return;
    requestedImage = filename; image.hidden = true; message.hidden = true; empty.hidden = true;
    image.onload = function () { if (requestedImage === filename) { image.hidden = false; message.hidden = true; empty.hidden = true; } };
    image.onerror = function () { if (requestedImage === filename) { image.hidden = true; message.hidden = false; empty.hidden = true; } };
    image.src = "/stimulus-image/" + encodeURIComponent(filename);
  }
  function clearNaturalPreview(message, className) {
    var image = $("preview-image"), empty = $("stimulus-preview-empty"), preview = image.parentNode;
    requestedNaturalImage = null;
    image.hidden = true; image.removeAttribute("src"); $("preview-message").hidden = true;
    empty.textContent = message; empty.hidden = false;
    preview.classList.remove("gray", "stale");
    if (className) preview.classList.add(className);
  }
  function setNaturalPreview(sessionId, filename) {
    var identity = sessionId + "/" + filename;
    if (identity === requestedNaturalImage) return;
    requestedImage = null; requestedNaturalImage = identity;
    var image = $("preview-image"), empty = $("stimulus-preview-empty"), message = $("preview-message");
    image.hidden = true; message.hidden = true; empty.hidden = true;
    image.onload = function () { if (requestedNaturalImage === identity) { image.hidden = false; message.hidden = true; } };
    image.onerror = function () { if (requestedNaturalImage === identity) { image.hidden = true; message.hidden = false; } };
    image.src = "/api/stimulus-image/" + encodeURIComponent(sessionId) + "/" + encodeURIComponent(filename);
  }
  function renderNaturalStimulus(data, isNatural) {
    setHidden("natural-stimulus-details", !isNatural);
    setHidden("natural-lick-feedback", !isNatural);
    if (!isNatural) { requestedNaturalImage = null; return; }
    var current = data.stimulus_state, rigSession = data.rig && data.rig.session || {};
    var sessionState = String(rigSession.state || "").toUpperCase();
    var terminal = ["COMPLETE", "ERROR", "ABORTED"].indexOf(sessionState) >= 0;
    var fresh = data.telemetry_freshness === "FRESH";
    var age = finiteNumber(data.last_packet_age_sec) ? " · " + formatSeconds(data.last_packet_age_sec) + " ago" : "";
    $("stimulus-freshness").textContent = fresh ? "FRESH" + age : (data.telemetry_freshness === "NONE" ? "UNKNOWN · no telemetry" : "STALE / UNKNOWN");
    if (!fresh) {
      $("image").textContent = "—"; $("stimulus-role").textContent = "UNKNOWN";
      $("stimulus-filename").textContent = "—"; $("stimulus-sequence").textContent = "—";
      clearNaturalPreview("STALE / UNKNOWN", "stale");
      return;
    }
    if (terminal) {
      $("image").textContent = "—"; $("stimulus-role").textContent = "Session ended";
      $("stimulus-filename").textContent = "—"; $("stimulus-sequence").textContent = "—";
      clearNaturalPreview("Session ended", "gray");
      return;
    }
    if (!current || typeof current !== "object") {
      $("image").textContent = "—"; $("stimulus-role").textContent = "Unavailable";
      $("stimulus-filename").textContent = "—"; $("stimulus-sequence").textContent = "—";
      clearNaturalPreview("Image assets unavailable", "gray");
      return;
    }
    var phase = text(current.phase), sequence = current.sequence_index;
    $("stimulus-role").textContent = phase;
    $("stimulus-sequence").textContent = sequence == null ? "—" : "Trial " + sequence;
    if (current.stimulus_kind === "gray") {
      $("image").textContent = "Gray";
      $("stimulus-filename").textContent = "—";
      clearNaturalPreview("Gray interval · " + phase, "gray");
      return;
    }
    var filename = current.image_filename;
    if (current.stimulus_kind !== "natural_image" || typeof filename !== "string" ||
        !/^natimg_center_[0-9]{4}\.png$/.test(filename) || Number(filename.slice(14, 18)) >= 2800 ||
        !data.session_id) {
      $("image").textContent = "—"; $("stimulus-filename").textContent = "—";
      clearNaturalPreview("Image preview unavailable", "gray");
      return;
    }
    $("image").textContent = text(current.image_id);
    $("stimulus-filename").textContent = filename;
    $("preview-image").parentNode.classList.remove("gray", "stale");
    setNaturalPreview(data.session_id, filename);
  }
  function renderNaturalLicks(data) {
    var licks = data.lick_state, fresh = data.telemetry_freshness === "FRESH";
    var sessionState = String(data.rig && data.rig.session && data.rig.session.state || "").toUpperCase();
    var ended = ["COMPLETE", "ERROR", "ABORTED"].indexOf(sessionState) >= 0;
    ["left", "right"].forEach(function (name) {
      var channel = licks && licks.channels && licks.channels[name];
      var status = channel && channel.status;
      var available = channel && channel.available === true;
      var onset = $("lick-" + name + "-onset");
      var recent = available && status === "available" && fresh && !ended &&
        finiteNumber(channel.last_lick_age_sec) && channel.last_lick_age_sec >= 0 &&
        channel.last_lick_age_sec <= 1.5;
      var label = status === "not_requested" ? "GPIO not requested" :
        status === "not_configured" ? "Not configured" :
        status === "session_ended" || ended ? "Session ended" :
        available ? (fresh ? "Available" : "STALE") : "Not available";
      $("lick-" + name + "-status").textContent = label;
      if (!available || !fresh || ended) {
        $("lick-" + name + "-contact").textContent = !fresh ? "Unknown (stale)" : "—";
        onset.textContent = "—";
      } else {
        $("lick-" + name + "-contact").textContent = channel.contact_active === true ? "Active" :
          channel.contact_active === false ? "Inactive" : "Unknown";
        onset.textContent = recent ? "LICK" : "—";
      }
      onset.className = recent ? "onset-active" : "";
      ["recent", "total"].forEach(function (metric) {
        var field = metric === "recent" ? "recent_lick_count_2s" : "total_lick_onset_count_session";
        var hasCount = channel && finiteNumber(channel[field]);
        var value = hasCount ? formatInteger(channel[field]) : "—";
        if (hasCount && !fresh) value += " (stale)";
        $("lick-" + name + "-" + metric).textContent = value;
      });
    });
  }
  function rewardLabel(trial) {
    if (trial.reward_omission === true) return "OMITTED";
    if (trial.reward_delivered === true) return "DELIVERED";
    if (trial.reward_eligible === false) return "NO REWARD";
    if (trial.reward_scheduled === false) return "NO REWARD";
    return "UNKNOWN";
  }
  function rewardContactLabel(trial) {
    if (trial.reward_contacted === true) return "YES";
    if (trial.reward_contacted === false && trial.reward_delivered === true) return "NO";
    if (trial.reward_delivered === false || trial.reward_omission === true || trial.reward_scheduled === false) return "N/A";
    return "UNKNOWN";
  }
  function renderRewardBehavior(data) {
    var hasSession = data.session_id !== null && data.session_id !== undefined && data.session_id !== "";
    if (!hasSession) {
      $("reward-contact-rate").textContent = "—";
      $("rewarded-cue-anticipatory-rate").textContent = "—";
      $("unrewarded-cue-anticipatory-rate").textContent = "—";
      $("low-prob-cue-anticipatory-rate").textContent = "—";
    } else {
      $("reward-contact-rate").textContent = formatFraction(data.task_reward_trains_contacted_session, data.task_reward_trains_verified_session);
      $("rewarded-cue-anticipatory-rate").textContent = formatFraction(data.task_rewarded_high_cue_anticipatory_lick_trials_session, data.task_rewarded_high_cue_trials_completed_session);
      $("unrewarded-cue-anticipatory-rate").textContent = formatFraction(data.task_unrewarded_high_cue_anticipatory_lick_trials_session, data.task_unrewarded_high_cue_trials_completed_session);
      $("low-prob-cue-anticipatory-rate").textContent = formatFraction(data.task_low_probability_cue_anticipatory_lick_trials_session, data.task_low_probability_cue_trials_completed_session);
    }
    $("water-delivered").textContent = formatWater(data.task_water_delivered_ul_session, false);
    $("water-likely-consumed").textContent = formatWater(data.task_water_likely_consumed_ul_session, true);
    $("reward-volume").textContent = formatWater(data.reward_volume_ul_per_train, false);
  }
  function protocolFraction(numerator, denominator) {
    return finiteNumber(numerator) && finiteNumber(denominator) && denominator > 0 ? formatFraction(numerator, denominator) : "—";
  }
  function protocolRate(numerator, denominator) {
    return finiteNumber(numerator) && finiteNumber(denominator) && denominator > 0 ? numerator / denominator : null;
  }
  function renderPartialReversalBehavior(data) {
    var plusRate = protocolRate(data.task_r_plus_cue_anticipatory_lick_trials_session, data.task_r_plus_cue_trials_completed_session);
    var minusRate = protocolRate(data.task_r_minus_cue_anticipatory_lick_trials_session, data.task_r_minus_cue_trials_completed_session);
    $("new-reward-contact-rate").textContent = formatFraction(data.task_reward_trains_contacted_session, data.task_reward_trains_verified_session);
    $("r-plus-anticipatory-rate").textContent = protocolFraction(data.task_r_plus_cue_anticipatory_lick_trials_session, data.task_r_plus_cue_trials_completed_session);
    $("r-minus-anticipatory-rate").textContent = protocolFraction(data.task_r_minus_cue_anticipatory_lick_trials_session, data.task_r_minus_cue_trials_completed_session);
    $("r-discrimination").textContent = plusRate === null || minusRate === null ? "—" : (plusRate >= minusRate ? "+" : "") + Math.round((plusRate - minusRate) * 100) + " pp";
    $("r-plus-omission-rate").textContent = protocolFraction(data.task_r_plus_omission_anticipatory_lick_trials_session, data.task_r_plus_omission_trials_completed_session);
    var readiness = "—";
    if (plusRate !== null) {
      readiness = plusRate < 0.8 ? "NOT YET" : (minusRate !== null && (minusRate >= 0.5 || plusRate - minusRate < 0.2) ? "CHECK DISCRIMINATION" : "READY CANDIDATE");
    }
    $("reversal-readiness").textContent = readiness;
    $("high-r-plus-rate").textContent = protocolFraction(data.task_high_r_plus_cue_anticipatory_lick_trials_session, data.task_high_r_plus_cue_trials_completed_session);
    $("high-r-minus-rate").textContent = protocolFraction(data.task_high_r_minus_cue_anticipatory_lick_trials_session, data.task_high_r_minus_cue_trials_completed_session);
    $("medium-r-plus-rate").textContent = protocolFraction(data.task_medium_r_plus_cue_anticipatory_lick_trials_session, data.task_medium_r_plus_cue_trials_completed_session);
    $("medium-r-minus-rate").textContent = protocolFraction(data.task_medium_r_minus_cue_anticipatory_lick_trials_session, data.task_medium_r_minus_cue_trials_completed_session);
    $("low-r-minus-rate").textContent = protocolFraction(data.task_low_r_minus_cue_anticipatory_lick_trials_session, data.task_low_r_minus_cue_trials_completed_session);
    $("new-water-delivered").textContent = formatWater(data.task_water_delivered_ul_session, false);
    $("new-water-likely-consumed").textContent = formatWater(data.task_water_likely_consumed_ul_session, true);
    $("new-reward-volume").textContent = formatWater(data.reward_volume_ul_per_train, false);
  }
  function renderSpoutBehavior(data, isBaitView) {
    $("spout-phase").textContent = text(data.phase);
    $("spout-reward-progress").textContent = spoutRewardProgress(data);
    $("spout-next-reward").textContent = isBaitView || data.phase === "COMPLETE" ? "—" : formatSeconds(data.next_reward_in_sec);
    var retrievalDenominator = data.completed_training_reward_count;
    $("spout-retrieval-rate").textContent = formatSpoutFraction(data.retrieval_success_count_session, retrievalDenominator);
    var recentDenominator = finiteNumber(data.criterion_window_rewards) ? data.criterion_window_rewards : 20;
    $("spout-recent20").textContent = data.criterion_evaluable ? formatSpoutFraction(data.recent_20_success_count, recentDenominator) : "Not yet evaluable";
    var criterion = data.training_passed === true ? "PASS" : (!data.criterion_evaluable ? "NOT YET" : data.phase === "COMPLETE" ? "NOT REACHED" : "NOT YET");
    $("spout-criterion").textContent = criterion;
    setHidden("spout-pass-reward-row", !(data.training_passed === true && finiteNumber(data.training_pass_reward_index)));
    $("spout-pass-reward").textContent = formatInteger(data.training_pass_reward_index);
    $("spout-training-water").textContent = formatWater(data.task_water_delivered_ul_session, false);
    $("spout-bait-water").textContent = formatWater(data.bait_water_ul_session, false);
    $("spout-total-water").textContent = formatWater(data.total_water_ul_session, false);
    $("spout-reward-volume").textContent = formatWater(data.reward_volume_ul, false);
    $("spout-total-licks").textContent = formatInteger(data.total_lick_onset_count_session);
    var baitDenominator = data.completed_bait_reward_count;
    $("spout-bait-drops").textContent = finiteNumber(data.bait_total) ? formatInteger(data.completed_bait_reward_count) + " / " + data.bait_total : formatInteger(data.completed_bait_reward_count);
    $("spout-bait-contact").textContent = formatSpoutFraction(data.bait_contacted_count_session, baitDenominator);
    $("spout-manual-bait-water").textContent = formatWater(data.bait_water_ul_session, false);
    $("spout-recent-licks").textContent = formatInteger(data.recent_lick_count_2s);
    $("spout-last-lick").textContent = formatAge(data.last_lick_age_sec);
    $("spout-lick-status").textContent = data.licking_active === true ? "LICKING" : "quiet";
    $("spout-lick-status").className = data.licking_active === true ? "licking" : "quiet";
    $("spout-training-status").textContent = data.manual_start_requested === true ? "Starting after bait cycle" : data.phase === "MANUAL_START_DELAY" ? "Starting" : "Waiting for operator";
  }
  function spoutContact(value) { return value === true ? "YES" : value === false ? "NO" : "—"; }
  function renderSpoutLast(data, isBaitView) {
    var last = data.last_trial;
    var hasBait = finiteNumber(data.completed_bait_reward_count) && data.completed_bait_reward_count > 0;
    setHidden("spout-last-bait", !isBaitView || !hasBait);
    setHidden("spout-last-reward", isBaitView || !last);
    var hasLast = isBaitView ? hasBait : !!last;
    setHidden("spout-last-empty", hasLast);
    $("spout-last-empty").textContent = isBaitView ? "No completed bait reward" : "No completed reward";
    if (isBaitView) {
      $("spout-last-bait-index").textContent = formatInteger(data.bait_index !== null && data.bait_index !== undefined ? data.bait_index : data.completed_bait_reward_count);
      $("spout-last-bait-contact").textContent = spoutContact(data.last_bait_contacted);
      $("spout-last-bait-latency").textContent = formatSeconds(data.last_bait_first_lick_latency_sec);
    } else if (last) {
      $("spout-last-reward-index").textContent = formatInteger(last.training_reward_index !== undefined ? last.training_reward_index : last.trial);
      $("spout-last-reward-contact").textContent = spoutContact(last.reward_contacted);
      $("spout-last-reward-latency").textContent = formatSeconds(last.first_lick_latency_sec);
      $("spout-last-licks-05").textContent = formatInteger(last.lick_count_reward_to_0p5_sec);
      $("spout-last-licks-10").textContent = formatInteger(last.lick_count_reward_to_1p0_sec);
      $("spout-last-licks-25").textContent = formatInteger(last.lick_count_reward_to_2p5_sec);
    }
  }
  function currentRewardLabel(trial) { return trial.reward_eligible === true ? "R+" : trial.reward_eligible === false ? "R-" : "—"; }
  function renderHistory(data, isSpout, isNewProtocol) {
    var body = $("history");
    clear(body);
    setHidden("reward-conditioning-history-head", isSpout);
    setHidden("partial-reversal-history-head", isSpout || !isNewProtocol);
    setHidden("spout-history-head", !isSpout);
    $("history-title").textContent = isSpout ? "RECENT REWARDS" : "RECENT TRIALS";
    var rows = (data.recent_trials || []).slice(0, 20);
    if (!rows.length) { var empty = document.createElement("tr"), emptyCell = document.createElement("td"); emptyCell.colSpan = 5; emptyCell.className = "empty"; emptyCell.textContent = isSpout ? "No scheduled rewards" : "No trial history"; empty.appendChild(emptyCell); body.appendChild(empty); return; }
    rows.forEach(function (trial) {
      var row = document.createElement("tr");
      if (isSpout) { addCell(row, trial.training_reward_index !== undefined ? trial.training_reward_index : trial.trial); addCell(row, spoutContact(trial.reward_contacted)); addCell(row, formatSeconds(trial.first_lick_latency_sec)); addCell(row, formatInteger(trial.lick_count_reward_to_0p5_sec)); addCell(row, formatInteger(trial.lick_count_reward_to_2p5_sec)); }
      else if (isNewProtocol) { addCell(row, trial.trial); addCell(row, trial.stimulus_role); addCell(row, currentRewardLabel(trial)); addCell(row, rewardLabel(trial)); addCell(row, trial.anticipatory_lick === true ? "YES" : trial.anticipatory_lick === false ? "NO" : "—", trial.anticipatory_lick ? "yes" : "no"); }
      else { addCell(row, trial.trial); addCell(row, trial.stimulus_role); addCell(row, rewardLabel(trial)); addCell(row, trial.reward_omission ? "Yes" : "No"); addCell(row, trial.anticipatory_lick ? "YES" : "NO", trial.anticipatory_lick ? "yes" : "no"); }
      body.appendChild(row);
    });
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
    var isSpout = data.protocol === "spout_training";
    var isNewProtocol = data.protocol === "exposure_reward_partial_reversal_v1" || data.protocol_version === "exposure_reward_partial_reversal_v1" || data.new_reward_protocol_behavior_available === true;
    var isNatural = data.protocol === "natural-images" || data.protocol === "natural_images";
    var isBaitPhase = data.phase === "MANUAL_BAIT" || data.phase === "MANUAL_START_DELAY";
    var isBaitView = isSpout && (isBaitPhase || (!data.phase && data.manual_bait_active === true));
    $("session-id").textContent = text(data.session_id); $("protocol").textContent = text(data.protocol);
    $("contingency-phase").textContent = text(data.contingency_phase);
    renderNaturalStimulus(data, isNatural);
    if (isNatural) renderNaturalLicks(data);
    $("connection").className = "status " + (data.connected ? "live" : "stale"); $("connection").lastElementChild.textContent = data.status || "STALE";
    setHidden("reward-conditioning-session-view", isSpout); setHidden("spout-session-view", !isSpout);
    setHidden("reward-conditioning-last-view", isSpout); setHidden("spout-last-view", !isSpout);
    setHidden("legacy-behavior-view", isSpout || isNewProtocol || isNatural); setHidden("partial-reversal-behavior-view", isSpout || isNatural || !isNewProtocol);
    if (isSpout) {
      setHidden("spout-behavior-scheduled", isBaitView);
      setHidden("spout-behavior-manual", !isBaitView);
    }
    $("session-title").textContent = isSpout ? "SPOUT TRAINING" : "SESSION";
    $("last-panel-title").textContent = isSpout ? (isBaitView ? "LAST BAIT" : "LAST REWARD") : "LAST TRIAL";
    if (isSpout) {
      $("image").textContent = "—"; $("stimulus-role").textContent = "—"; setPreview(null);
      renderSpoutBehavior(data, isBaitView); renderSpoutLast(data, isBaitView);
    } else if (isNatural) {
      var stimulus = data.stimulus_state || {};
      $("phase").textContent = text(stimulus.phase);
      $("trial").textContent = stimulus.sequence_index == null ? "—" : text(stimulus.sequence_index);
      $("block").textContent = "—"; $("eta").textContent = formatEta(data.eta_sec);
      $("phase-contingency").textContent = "—";
      renderLickTimeline(null);
    } else {
      $("image").textContent = text(data.image); $("stimulus-role").textContent = text(data.stimulus_role); setPreview(data.image);
      $("phase").textContent = text(data.phase); $("trial").textContent = data.trial == null ? "—" : text(data.trial) + (data.total_trials == null ? "" : " / " + data.total_trials);
      $("block").textContent = data.block == null ? "—" : text(data.block) + (data.total_blocks == null ? "" : " / " + data.total_blocks); $("eta").textContent = formatEta(data.eta_sec);
      $("phase-contingency").textContent = text(data.contingency_phase);
      if (isNewProtocol) renderPartialReversalBehavior(data); else renderRewardBehavior(data);
      renderLickTimeline(data.last_trial);
    }
    var last = data.last_trial, lastNode = $("last-trial"), summaryNode = $("last-summary");
    clear(lastNode);
    if (!last) { lastNode.className = "last-trial empty"; lastNode.textContent = "No completed trial received"; summaryNode.textContent = "No completed trial received"; } else {
      lastNode.className = "last-trial"; summaryNode.textContent = trialSummary(last); addPair(lastNode, "Trial", last.trial); addPair(lastNode, "Condition", last.stimulus_role); addPair(lastNode, "Reward", rewardLabel(last)); addPair(lastNode, "Reward contacted", rewardContactLabel(last)); addPair(lastNode, "Anticipatory lick", last.anticipatory_lick ? "YES" : "NO");
      if (isNewProtocol) {
        if (last.exposure_level !== undefined && last.exposure_level !== null) addPair(lastNode, "Exposure", last.exposure_level);
        addPair(lastNode, "Current reward", currentRewardLabel(last));
        if (last.reward_trajectory !== undefined && last.reward_trajectory !== null) addPair(lastNode, "Trajectory", last.reward_trajectory);
      }
    }
    renderHistory(data, isSpout, isNewProtocol);
  }
  var operatorToken = "";
  var protocolChoices = [];
  var runtimeFieldInputs = {};
  var pendingControlIds = {};
  var hardwareLabels = { display: "Display", camera_1: "Camera 1", camera_2: "Camera 2", storage: "Storage", gpio: "GPIO", irig: "IRIG", chrony: "Chrony", audio: "Audio" };
  var integrityLabels = { event_drops: "Event drops", writer_errors: "Writer errors", kms_completed: "KMS completed", kms_long_intervals: "KMS long intervals", camera_errors: "Camera errors" };
  function addStatus(parent, label, value) {
    var item = document.createElement("div"), name = document.createElement("span"), content = document.createElement("strong");
    name.textContent = label; content.textContent = text(value); item.appendChild(name); item.appendChild(content); parent.appendChild(item);
  }
  function statusValue(value) {
    if (value === null || value === undefined) return "—";
    if (typeof value === "object") { try { value = JSON.stringify(value); } catch (_error) { value = "[value]"; } }
    return String(value).length > 100 ? String(value).slice(0, 97) + "…" : String(value);
  }
  function selectedProtocol() {
    return protocolChoices.find(function (item) { return item.protocol_id === $("protocol-select").value; });
  }
  function selectedPreset() {
    var protocol = selectedProtocol();
    return protocol && protocol.presets.find(function (item) { return item.preset_id === $("preset-select").value; });
  }
  function runtimeFieldsValid() {
    var protocol = selectedProtocol();
    if (!protocol) return false;
    return protocol.runtime_fields.every(function (field) {
      var input = runtimeFieldInputs[field.name];
      return !!input && (!field.required || input.checkValidity()) &&
        (input.value === "" || input.checkValidity());
    });
  }
  function renderPresets() {
    var select = $("preset-select"), protocol = selectedProtocol(); clear(select);
    if (!protocol) { select.appendChild(new Option("No available presets", "")); select.disabled = true; return; }
    protocol.presets.forEach(function (preset) {
      var option = new Option(preset.display_name + (preset.available ? "" : " · unavailable"), preset.preset_id);
      option.disabled = preset.available !== true;
      if (preset.unavailable_reason) option.title = preset.unavailable_reason;
      select.appendChild(option);
    });
    var firstAvailable = protocol.presets.find(function (preset) { return preset.available === true; });
    if (firstAvailable) select.value = firstAvailable.preset_id;
    renderRuntimeFields();
  }
  function renderRuntimeFields() {
    var container = $("runtime-fields"), protocol = selectedProtocol(); clear(container);
    runtimeFieldInputs = {};
    (protocol ? protocol.runtime_fields : []).forEach(function (field) {
      if (field.type !== "subject_id") return;
      var label = document.createElement("label"), input = document.createElement("input");
      input.type = "text"; input.name = field.name; input.maxLength = 64;
      input.pattern = "[A-Za-z0-9][A-Za-z0-9_-]{0,63}"; input.required = field.required === true;
      input.autocomplete = "off"; label.textContent = field.display_name; label.htmlFor = "runtime-" + field.name;
      input.id = "runtime-" + field.name;
      container.appendChild(label); container.appendChild(input); runtimeFieldInputs[field.name] = input;
      input.addEventListener("input", renderOperatorFromLastState);
    });
  }
  var lastOperatorState = null;
  function renderOperatorFromLastState() { if (lastOperatorState) renderOperator(lastOperatorState); }
  function renderOperator(data) {
    lastOperatorState = data;
    var rig = data.rig || {}, session = rig.session || {}, supervisor = data.supervisor || {};
    var observedActive = ["PREPARING", "READY", "RUNNING", "FINISHING"].indexOf(session.state) >= 0 && session.id;
    var managed = supervisor.available && supervisor.session_id && (!observedActive || session.id === supervisor.session_id);
    var state = managed ? supervisor.state : (observedActive ? session.state + " · UNMANAGED" :
      (!supervisor.available ? "UNKNOWN · SUPERVISOR OFFLINE" : (session.state || "IDLE")));
    var sessionId = managed ? supervisor.session_id : session.id;
    var protocol = managed ? (supervisor.protocol_display_name || supervisor.protocol_id || supervisor.protocol) : session.protocol;
    if (managed && supervisor.preset_display_name) protocol += " · " + supervisor.preset_display_name;
    var eta = managed ? supervisor.eta_s : session.eta_s;
    var elapsed = managed ? supervisor.elapsed_s : session.elapsed_s;
    var capabilities = supervisor.capabilities || {};
    var hasToken = operatorToken.length > 0;
    var canControl = data.control_enabled === true && supervisor.available === true && hasToken;
    var taskControlAvailable = supervisor.control_connected === true;
    var terminal = ["IDLE", "COMPLETE", "ERROR", "ABORTED"].indexOf(supervisor.state) >= 0;
    var unmanagedActive = observedActive && (!supervisor.available || session.id !== supervisor.session_id);
    var protocolChoice = selectedProtocol(), presetChoice = selectedPreset();
    var prepareAllowed = canControl && terminal && !unmanagedActive && supervisor.process_running !== true &&
      !!protocolChoice && protocolChoice.available === true && !!presetChoice && presetChoice.available === true &&
      runtimeFieldsValid();
    $("operator-state").textContent = state || "IDLE";
    $("operator-session-id").textContent = text(sessionId || "No session");
    $("operator-protocol").textContent = text(protocol);
    $("operator-elapsed").textContent = finiteNumber(elapsed) ? formatEta(elapsed) : "—";
    $("operator-eta").textContent = finiteNumber(eta) ? formatEta(eta) : "—";
    $("supervisor-connection").className = "status " + (supervisor.available ? "live" : "stale");
    $("supervisor-connection").lastElementChild.textContent = !supervisor.available ? "SUPERVISOR OFFLINE" :
      (supervisor.process_running && !taskControlAvailable ? "SUPERVISOR ONLINE · TASK CONTROL DEGRADED" : "SUPERVISOR ONLINE");
    $("control-token").disabled = data.control_enabled !== true;
    $("protocol-select").disabled = !prepareAllowed || protocolChoices.length === 0;
    $("preset-select").disabled = !prepareAllowed || !protocolChoice || !protocolChoice.presets.length;
    $("prepare-button").disabled = !prepareAllowed;
    ["start", "stop", "abort", "bait_left", "bait_right"].forEach(function (name) {
      var button = $(name.replace("_", "-") + "-button");
      button.disabled = !canControl || !taskControlAvailable || capabilities[name] !== true;
      if (name.indexOf("bait_") === 0) button.hidden = capabilities[name] !== true;
    });
    if (data.control_enabled !== true) $("control-help").textContent = "Browser controls are disabled until the server is configured with RIG_MONITOR_CONTROL_TOKEN.";
    else if (!hasToken) $("control-help").textContent = "Enter the operator token configured on the monitor server to enable valid controls.";
    else if (!supervisor.available) $("control-help").textContent = "Supervisor unavailable. Task state is unknown unless telemetry is still arriving; no launch is attempted.";
    else if (supervisor.process_running && !taskControlAvailable) $("control-help").textContent = "Experiment process is alive but its control endpoint is unavailable. The task is left running.";
    else if (supervisor.recovery_state === "STALE") $("control-help").textContent = "Saved process identity is stale; no controls were sent to that PID.";
    else if (supervisor.recovery_diagnostic) $("control-help").textContent = supervisor.recovery_diagnostic;
    else if (supervisor.engine_error_summary) $("control-help").textContent = supervisor.engine_error_summary;
    else if (unmanagedActive) $("control-help").textContent = "An active telemetry session is not registered with the supervisor. New launches are blocked.";
    else if (supervisor.process_running === true && terminal) $("control-help").textContent = "The previous experiment process has not exited. New launches are blocked.";
    else $("control-help").textContent = "Commands are acknowledged by the supervisor and validated by the experiment process.";

    var hardware = $("hardware-status"), hardwareValues = rig.hardware || {}; clear(hardware);
    Object.keys(hardwareLabels).forEach(function (key) {
      if ((key === "camera_1" || key === "camera_2") && Array.isArray(hardwareValues.cameras) && hardwareValues.cameras.length) return;
      addStatus(hardware, hardwareLabels[key], hardwareValues[key]);
    });
    (hardwareValues.cameras || []).forEach(function (camera, index) {
      if (!camera || typeof camera !== "object") return;
      var cameraId = camera.camera_id || camera.id || camera.role || "Camera " + (index + 1);
      var label = camera.role && camera.role !== cameraId ? cameraId + " (" + camera.role + ")" : cameraId;
      addStatus(hardware, label, camera.state || "unknown");
    });
    var integrity = $("integrity-status"); clear(integrity);
    Object.keys(integrityLabels).forEach(function (key) { addStatus(integrity, integrityLabels[key], (rig.integrity || {})[key]); });
    var protocolNode = $("protocol-status"); clear(protocolNode);
    var protocolState = rig.protocol || {}, protocolKeys = Object.keys(protocolState);
    if (!protocolKeys.length) { var emptyProtocol = document.createElement("span"); emptyProtocol.className = "empty"; emptyProtocol.textContent = "No protocol state received"; protocolNode.appendChild(emptyProtocol); }
    protocolKeys.slice(0, 12).forEach(function (key) { addStatus(protocolNode, key.replace(/_/g, " "), statusValue(protocolState[key])); });
    var eventsNode = $("recent-events"); clear(eventsNode);
    var events = (supervisor.recent_events || []).concat(data.recent_events || []).slice(0, 12);
    if (!events.length) { var emptyEvents = document.createElement("span"); emptyEvents.className = "empty"; emptyEvents.textContent = "No events received"; eventsNode.appendChild(emptyEvents); }
    events.forEach(function (item) {
      var row = document.createElement("div"), label = document.createElement("strong"), detail = document.createElement("span");
      label.textContent = item.event || item.type || "event";
      var description = item.channel || item.session_id || (item.trial !== undefined ? "trial " + item.trial : "");
      detail.textContent = description; row.appendChild(label); row.appendChild(detail); eventsNode.appendChild(row);
    });
  }
  function loadProtocols() {
    fetch("/api/protocols", { cache: "no-store" }).then(function (response) {
      if (!response.ok) throw new Error("protocol registry unavailable");
      return response.json();
    }).then(function (items) {
      protocolChoices = items;
      var select = $("protocol-select"); clear(select);
      protocolChoices.forEach(function (item) {
        var option = new Option(item.display_name + (item.available ? "" : " · unavailable"), item.protocol_id);
        option.disabled = item.available !== true;
        if (item.unavailable_reason) option.title = item.unavailable_reason;
        select.appendChild(option);
      });
      var firstAvailable = protocolChoices.find(function (item) { return item.available === true; });
      if (firstAvailable) select.value = firstAvailable.protocol_id;
      select.disabled = !firstAvailable;
      renderPresets(); renderOperatorFromLastState();
    }).catch(function () { $("protocol-select").innerHTML = "<option value=''>Supervisor unavailable</option>"; });
  }
  function sendControl(command) {
    var requestId = pendingControlIds[command];
    if (!requestId) {
      requestId = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
      pendingControlIds[command] = requestId;
    }
    var body = { command: command, request_id: requestId }, receivedResponse = false;
    if (command === "prepare") {
      body.protocol_id = $("protocol-select").value;
      body.preset_id = $("preset-select").value;
      body.fields = {};
      Object.keys(runtimeFieldInputs).forEach(function (name) {
        if (runtimeFieldInputs[name].value !== "") body.fields[name] = runtimeFieldInputs[name].value;
      });
    }
    $("control-message").textContent = "Sending " + command.replace(/_/g, " ") + "…";
    fetch("/api/control", { method: "POST", headers: { "Content-Type": "application/json", "Authorization": "Bearer " + operatorToken }, body: JSON.stringify(body) })
      .then(function (response) { return response.json().then(function (value) { receivedResponse = true; if (!response.ok) throw new Error(value.error || "command rejected"); return value; }); })
      .then(function () { delete pendingControlIds[command]; $("control-message").textContent = command.replace(/_/g, " ") + " acknowledged"; poll(); })
      .catch(function (error) { if (receivedResponse) delete pendingControlIds[command]; $("control-message").textContent = error.message; });
  }
  $("control-token").addEventListener("input", function (event) { operatorToken = event.target.value; poll(); });
  $("protocol-select").addEventListener("change", function () { renderPresets(); renderOperatorFromLastState(); });
  $("preset-select").addEventListener("change", renderOperatorFromLastState);
  [["prepare-button", "prepare"], ["start-button", "start"], ["stop-button", "stop"], ["abort-button", "abort"], ["bait-left-button", "bait_left"], ["bait-right-button", "bait_right"]].forEach(function (item) {
    $(item[0]).addEventListener("click", function () { sendControl(item[1]); });
  });
  loadProtocols();
  var pollInFlight = false;
  function poll() {
    if (pollInFlight) return;
    pollInFlight = true;
    fetch("/api/state", { cache: "no-store" })
      .then(function (response) { return response.json(); })
      .then(function (data) { render(data); renderOperator(data); })
      .catch(function () {
        $("connection").className = "status stale";
        $("connection").lastElementChild.textContent = "STALE";
      })
      .then(function () { pollInFlight = false; });
  }
  poll(); window.setInterval(poll, 300);
}());
