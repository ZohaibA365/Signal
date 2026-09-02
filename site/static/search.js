/* Client-side job search.
 *
 * The whole searchable dataset ships with the page (~440 kB gzipped), so
 * filtering runs in memory with no request to a server. That is why results
 * update as you type, cost nothing per visitor, and need no backend to scale.
 *
 * No framework: this is one array, a few predicates and a render loop. A
 * framework would weigh more than the data it manages.
 */
(function () {
  "use strict";

  var DATA = null, PREFIX = [], LIMIT = 100, shownLimit = LIMIT;
  var results = document.getElementById("results");
  var summary = document.getElementById("summary");
  var el = {};
  ["q", "skills", "country", "seniority", "sponsor", "salary", "state"].forEach(function (id) {
    el[id] = document.getElementById(id);
  });
  var picked = document.getElementById("picked");

  // "Remote" is not a state but it is where a large share of these roles
  // actually are, so it sits in the same control rather than in a separate
  // one people would have to know to look for.
  var REMOTE = "\u0000remote";

  var PRESETS = {
    intern: function () { el.seniority.value = "intern"; },
    sponsor: function () { el.sponsor.value = "verified"; },
    de: function () { el.q.value = "data engineer"; },
    fresh: function () { state.maxDays = 7; },
    paid: function () { state.paidOnly = true; },
    canada: function () { el.country.value = "ca"; }
  };
  var UNSET = {
    intern: function () { el.seniority.value = ""; },
    sponsor: function () { el.sponsor.value = ""; },
    de: function () { el.q.value = ""; },
    fresh: function () { state.maxDays = 0; },
    paid: function () { state.paidOnly = false; },
    canada: function () { el.country.value = ""; }
  };
  var state = { maxDays: 0, paidOnly: false, states: [] };

  /* The payload dictionary-encodes the repeated categorical fields. Decoding
     once on load keeps every filter comparing plain strings, which is what
     they did before - doing it per comparison would move the cost into the
     keystroke path, where it is felt. */
  function decode(p) {
    var d = p.dicts || {}, rows = p.rows || [];
    var single = ["c", "s", "l", "e", "p"];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      for (var j = 0; j < single.length; j++) {
        var f = single[j], t = d[f];
        r[f] = (t && r[f] != null) ? t[r[f]] : null;
      }
      if (d.k && r.k) {
        for (var m = 0; m < r.k.length; m++) r.k[m] = d.k[r.k[m]];
      }
    }
    return rows;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function debounce(fn, ms) { var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); }; }
  function href(r) { return r.u == null ? null : (r.h == null ? r.u : PREFIX[r.h] + r.u); }

  function filters() {
    return {
      q: el.q.value.trim().toLowerCase().split(/\s+/).filter(Boolean),
      skills: el.skills.value.trim().toLowerCase().split(/[,\s]+/).filter(Boolean),
      country: el.country.value,
      level: el.seniority.value,
      sponsor: el.sponsor.value,
      salary: parseInt(el.salary.value, 10) || 0,
      maxDays: state.maxDays,
      paidOnly: state.paidOnly,
      states: state.states
    };
  }

  function keep(r, f) {
    if (f.q.length) {
      var hay = (r.t + " " + r.c).toLowerCase();
      for (var i = 0; i < f.q.length; i++) if (hay.indexOf(f.q[i]) === -1) return false;
    }
    if (f.skills.length) {
      // Any-of, not all-of: requiring every skill returns nothing for most
      // people, which reads as a broken search rather than a strict one.
      var hit = false;
      for (var j = 0; j < f.skills.length; j++) if (r.k.indexOf(f.skills[j]) !== -1) { hit = true; break; }
      if (!hit) return false;
    }
    if (f.country && r.n !== f.country) return false;
    if (f.states.length) {
      // Several locations combine as OR - "California or New York" is the
      // question people actually have, not "both at once".
      var loc = false;
      for (var s2 = 0; s2 < f.states.length; s2++) {
        if (f.states[s2] === REMOTE ? r.r === 1 : r.s === f.states[s2]) { loc = true; break; }
      }
      if (!loc) return false;
    }
    if (f.level && r.l !== f.level) return false;
    if (f.maxDays && r.d > f.maxDays) return false;
    if (f.paidOnly && !r.w) return false;
    if (f.salary && !(r.w && r.w >= f.salary)) return false;
    if (f.sponsor === "verified" && r.p !== "frequent_sponsor" && r.p !== "has_sponsored") return false;
    if (f.sponsor === "open" && r.e === "blocked") return false;
    return true;
  }

  function tag(r) {
    if (r.p === "frequent_sponsor") return '<span class="tag ok">Sponsors</span>';
    if (r.p === "has_sponsored") return '<span class="tag ok">Has sponsored</span>';
    if (r.e === "blocked") return '<span class="tag no">US citizens</span>';
    if (r.l === "intern" || r.l === "entry") return '<span class="tag wa">' + esc(r.l) + "</span>";
    return '<span class="tag na">' + esc(r.l || "—") + "</span>";
  }

  // The host an "Apply" click actually lands on. Shown on every row because
  // every link here goes to an employer's own board, and saying which one is
  // what makes that checkable rather than something to take on trust.
  function host(link) {
    if (!link) return "";
    var m = /^https?:\/\/([^/]+)/.exec(link);
    return m ? m[1].replace(/^www\./, "") : "";
  }

  function row(r) {
    var link = href(r);
    var title = link
      ? '<a href="' + esc(link) + '" target="_blank" rel="noopener">' + esc(r.t) + "</a>"
      : esc(r.t);
    var meta = ["<b>" + esc(r.c) + "</b>"];
    if (r.s) meta.push(esc(r.s));
    else meta.push((r.n || "us").toUpperCase());
    meta.push(r.d === 0 ? "today" : r.d + "d");
    if (r.w) meta.push("$" + r.w.toLocaleString());
    var h = host(link);
    if (h) meta.push('<span class="host">' + esc(h) + "</span>");
    var techs = r.k.length
      ? '<div class="k">' + r.k.slice(0, 7).map(function (t) { return "<span>" + esc(t) + "</span>"; }).join("") + "</div>"
      : "";
    return '<div class="jrow"><div><div class="t">' + title + "</div>" +
           '<div class="m">' + meta.join("<span>·</span>") + "</div>" + techs +
           '</div><div class="right">' + tag(r) + "</div></div>";
  }

  /* The state list is built from the data rather than hard-coded, so it can
     never offer a location with nothing behind it. It is scoped to the chosen
     country because Ontario under "United States" is noise. */
  function fillStates() {
    if (!DATA) return;
    var country = el.country.value, counts = {}, anyRemote = false;
    for (var i = 0; i < DATA.length; i++) {
      var r = DATA[i];
      if (country && r.n !== country) continue;
      if (r.r === 1) anyRemote = true;
      if (r.s) counts[r.s] = (counts[r.s] || 0) + 1;
    }
    var names = Object.keys(counts).sort();
    var html = '<option value="">Any location</option>';
    if (anyRemote) html += '<option value="' + REMOTE + '">Remote</option>';
    for (var j = 0; j < names.length; j++) {
      if (state.states.indexOf(names[j]) !== -1) continue;
      html += '<option value="' + esc(names[j]) + '">' + esc(names[j]) +
              " (" + counts[names[j]] + ")</option>";
    }
    el.state.innerHTML = html;
  }

  function label(v) { return v === REMOTE ? "Remote" : v; }

  function drawPicked() {
    picked.hidden = state.states.length === 0;
    picked.innerHTML = state.states.map(function (v) {
      return '<button type="button" data-v="' + esc(v) + '">' + esc(label(v)) +
             "<span>\u00d7</span></button>";
    }).join("");
  }

  /* Filters are mirrored into the URL so a filtered view can be sent to
     someone. That matters here: these links go into messages, and "the data
     internships in Ontario" has to survive being pasted. */
  function writeUrl(f) {
    var p = new URLSearchParams();
    if (f.q.length) p.set("q", el.q.value.trim());
    if (f.skills.length) p.set("skills", el.skills.value.trim());
    if (f.country) p.set("country", f.country);
    if (f.level) p.set("level", f.level);
    if (f.sponsor) p.set("visa", f.sponsor);
    if (f.salary) p.set("salary", String(f.salary));
    if (f.maxDays) p.set("days", String(f.maxDays));
    if (f.paidOnly) p.set("paid", "1");
    if (state.states.length) {
      p.set("where", state.states.map(function (v) {
        return v === REMOTE ? "remote" : v;
      }).join("|"));
    }
    var qs = p.toString();
    history.replaceState(null, "", qs ? "?" + qs : location.pathname);
  }

  function readUrl() {
    var p = new URLSearchParams(location.search);
    if (p.get("q")) el.q.value = p.get("q");
    if (p.get("skills")) el.skills.value = p.get("skills");
    if (p.get("country")) el.country.value = p.get("country");
    if (p.get("level")) el.seniority.value = p.get("level");
    if (p.get("visa")) el.sponsor.value = p.get("visa");
    if (p.get("salary")) el.salary.value = p.get("salary");
    if (p.get("days")) state.maxDays = parseInt(p.get("days"), 10) || 0;
    if (p.get("paid")) state.paidOnly = true;
    if (p.get("where")) {
      state.states = p.get("where").split("|").map(function (v) {
        return v === "remote" ? REMOTE : v;
      });
    }
  }

  function render() {
    if (!DATA) return;
    var f = filters(), out = [], total = 0, sponsored = 0, interns = 0;

    for (var i = 0; i < DATA.length; i++) {
      var r = DATA[i];
      if (!keep(r, f)) continue;
      total++;
      if (r.p === "frequent_sponsor" || r.p === "has_sponsored") sponsored++;
      if (r.l === "intern") interns++;
      if (out.length < shownLimit) out.push(row(r));
    }

    writeUrl(f);
    summary.innerHTML =
      "<span><b>" + total.toLocaleString() + "</b> roles</span>" +
      "<span><b>" + interns.toLocaleString() + "</b> internships</span>" +
      "<span><b>" + sponsored.toLocaleString() + "</b> at employers that sponsor</span>";

    results.innerHTML = out.join("") ||
      '<div class="empty">Nothing matches those filters. Try removing one.</div>';

    if (total > out.length) {
      var b = document.createElement("div");
      b.className = "more";
      b.innerHTML = 'Showing ' + out.length.toLocaleString() + ' of ' + total.toLocaleString() +
                    ' · <a href="#" id="showmore">show more</a>';
      results.appendChild(b);
      document.getElementById("showmore").addEventListener("click", function (e) {
        e.preventDefault(); shownLimit += 200; render();
      });
    }
  }

  function reset() { shownLimit = LIMIT; render(); }
  var onType = debounce(reset, 110);

  ["q", "skills", "salary"].forEach(function (k) { el[k].addEventListener("input", onType); });
  ["seniority", "sponsor"].forEach(function (k) { el[k].addEventListener("change", reset); });

  el.country.addEventListener("change", function () {
    // Switching country invalidates any chosen state, so drop the ones that
    // no longer exist rather than silently filtering everything to nothing.
    var country = el.country.value;
    if (country) {
      var valid = {};
      for (var i = 0; i < DATA.length; i++) if (DATA[i].n === country && DATA[i].s) valid[DATA[i].s] = 1;
      state.states = state.states.filter(function (v) { return v === REMOTE || valid[v]; });
    }
    fillStates(); drawPicked(); reset();
  });

  el.state.addEventListener("change", function () {
    var v = el.state.value;
    if (v && state.states.indexOf(v) === -1) state.states.push(v);
    el.state.value = "";
    fillStates(); drawPicked(); reset();
  });

  picked.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-v]");
    if (!b) return;
    state.states = state.states.filter(function (v) { return v !== b.dataset.v; });
    fillStates(); drawPicked(); reset();
  });

  document.getElementById("chips").addEventListener("click", function (e) {
    var b = e.target.closest(".chip");
    if (!b) return;
    var on = b.getAttribute("aria-pressed") === "true";
    b.setAttribute("aria-pressed", on ? "false" : "true");
    (on ? UNSET : PRESETS)[b.dataset.preset]();
    reset();
  });

  fetch("data/jobs.json")
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (p) {
      PREFIX = p.prefixes || [];
      DATA = decode(p);
      readUrl();
      fillStates();
      drawPicked();
      render();
    })
    .catch(function () {
      summary.innerHTML = "<span>Could not load job data — try reloading.</span>";
    });
})();
