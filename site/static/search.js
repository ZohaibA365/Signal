/* Client-side job search.
 *
 * The entire searchable dataset ships with the page (~400 kB gzipped), so
 * filtering happens in memory with no request to a server. That is why search
 * is instant, costs nothing per user, and scales without any backend to scale.
 *
 * No framework on purpose: this is one array, a few predicates and a render
 * loop. A framework would add more bytes than the data it manages.
 */
(function () {
  "use strict";

  var DATA = null, PREFIXES = [], results = document.getElementById("results");
  var countEl = document.getElementById("count");
  var els = {};
  ["q", "skills", "country", "seniority", "sponsor", "salary"].forEach(function (id) {
    els[id] = document.getElementById(id);
  });

  function debounce(fn, ms) {
    var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function url(row) {
    if (row.u == null) return null;
    return row.h == null ? row.u : PREFIXES[row.h] + row.u;
  }

  function matches(row, f) {
    if (f.q) {
      var hay = (row.t + " " + row.c).toLowerCase();
      for (var i = 0; i < f.q.length; i++) if (hay.indexOf(f.q[i]) === -1) return false;
    }
    if (f.skills.length) {
      // Any-of rather than all-of: requiring every skill returns nothing for
      // most people, which reads as a broken search rather than a strict one.
      var hit = false;
      for (var j = 0; j < f.skills.length; j++) {
        if (row.k.indexOf(f.skills[j]) !== -1) { hit = true; break; }
      }
      if (!hit) return false;
    }
    if (f.country && row.n !== f.country) return false;
    if (f.seniority && row.l !== f.seniority) return false;
    if (f.salary && !(row.w && row.w >= f.salary)) return false;
    if (f.sponsor === "verified" && row.p !== "frequent_sponsor" && row.p !== "has_sponsored") return false;
    if (f.sponsor === "open" && row.e === "blocked") return false;
    return true;
  }

  function render() {
    if (!DATA) return;
    var f = {
      q: els.q.value.trim().toLowerCase().split(/\s+/).filter(Boolean),
      skills: els.skills.value.trim().toLowerCase().split(/[,\s]+/).filter(Boolean),
      country: els.country.value,
      seniority: els.seniority.value,
      sponsor: els.sponsor.value,
      salary: parseInt(els.salary.value, 10) || 0
    };

    var out = [], shown = 0, total = 0;
    for (var i = 0; i < DATA.length; i++) {
      var r = DATA[i];
      if (!matches(r, f)) continue;
      total++;
      if (shown >= 200) continue;   // cap the DOM, not the count
      shown++;

      var link = url(r);
      var title = link
        ? '<a href="' + esc(link) + '" target="_blank" rel="noopener">' + esc(r.t) + "</a>"
        : esc(r.t);

      var meta = [esc(r.c)];
      if (r.s) meta.push(esc(r.s));
      meta.push((r.n || "us").toUpperCase());
      meta.push(r.d === 0 ? "today" : r.d + "d ago");
      if (r.w) meta.push("$" + r.w.toLocaleString() + " stated");

      var badge = "";
      if (r.p === "frequent_sponsor") badge = '<span class="pill ok">Sponsors often</span>';
      else if (r.p === "has_sponsored") badge = '<span class="pill ok">Has sponsored</span>';
      else if (r.e === "blocked") badge = '<span class="pill no">Citizenship req.</span>';
      else badge = '<span class="pill na">' + esc(r.l || "—") + "</span>";

      var techs = r.k.length
        ? '<div class="techs">' + r.k.slice(0, 6).map(function (t) {
            return "<span>" + esc(t) + "</span>";
          }).join("") + "</div>"
        : "";

      out.push(
        '<div class="result"><div><div class="title">' + title + "</div>" +
        '<div class="meta">' + meta.join(" · ") + "</div>" + techs +
        "</div><div>" + badge + "</div></div>"
      );
    }

    countEl.textContent = total.toLocaleString() + " role" + (total === 1 ? "" : "s") +
      " match" + (total === 1 ? "es" : "") +
      (total > shown ? " · showing first " + shown : "");
    results.innerHTML = out.join("") ||
      '<div class="result"><div>No roles match those filters. Try removing one.</div></div>';
  }

  var onInput = debounce(render, 120);
  Object.keys(els).forEach(function (k) {
    els[k].addEventListener(k === "q" || k === "skills" || k === "salary" ? "input" : "change",
      k === "q" || k === "skills" || k === "salary" ? onInput : render);
  });

  countEl.textContent = "Loading roles…";
  fetch("../data/jobs.json")
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (payload) {
      PREFIXES = payload.prefixes || [];
      DATA = payload.rows || [];
      render();
    })
    .catch(function () {
      countEl.textContent = "Could not load the job data. Try reloading the page.";
    });
})();
