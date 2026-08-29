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
  ["q", "skills", "country", "seniority", "sponsor", "salary"].forEach(function (id) {
    el[id] = document.getElementById(id);
  });

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
  var state = { maxDays: 0, paidOnly: false };

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
      paidOnly: state.paidOnly
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
    var techs = r.k.length
      ? '<div class="k">' + r.k.slice(0, 7).map(function (t) { return "<span>" + esc(t) + "</span>"; }).join("") + "</div>"
      : "";
    return '<div class="jrow"><div><div class="t">' + title + "</div>" +
           '<div class="m">' + meta.join("<span>·</span>") + "</div>" + techs +
           '</div><div class="right">' + tag(r) + "</div></div>";
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
  ["country", "seniority", "sponsor"].forEach(function (k) { el[k].addEventListener("change", reset); });

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
    .then(function (p) { PREFIX = p.prefixes || []; DATA = p.rows || []; render(); })
    .catch(function () {
      summary.innerHTML = "<span>Could not load job data — try reloading.</span>";
    });
})();
