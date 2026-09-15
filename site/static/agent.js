/* The agent console.
 *
 * Two sources, one renderer. If the live service answers within LIVE_TIMEOUT it
 * streams real events; otherwise a recorded run plays with identical rendering.
 * A visitor therefore never sees a broken panel - deploys, outages, the daily
 * budget ceiling and "the backend does not exist yet" all look the same, and none
 * of them looks like a failure.
 *
 * The recorded runs are genuine: real agent code, real validators, replayed from
 * site/data/agent_runs.json. Where the model's misbehaviour was scripted rather
 * than observed, the scenario carries staged:true and the page says so.
 */
(function () {
  "use strict";

  var API = window.SIGNAL_AGENT_API || "";      // set when the service exists
  /* How long to wait for the service to START answering before giving up on it.
     Two seconds was chosen when this endpoint did nothing but exist. It now opens
     a Postgres connection and resolves the pasted text before the first event, and
     the first byte measures 0.7-1.1s on a warm service - so two seconds was inside
     the noise, and any cold connection fell back to a recording while the service
     was working perfectly. This bounds "the service is dead", which it still does;
     it is not a budget for the whole run, which has its own on the server. */
  var LIVE_TIMEOUT = 8000;
  var STEP_PAUSE = 620;                          // ms between steps when replaying
  var TYPE_SPEED = 9;                            // ms per character for the draft

  var el = {
    console: document.getElementById("console"),
    draft: document.getElementById("draft"),
    paste: document.getElementById("paste"),
    run: document.getElementById("run"),
    mode: document.getElementById("mode"),
    presets: document.getElementById("presets")
  };
  if (!el.console) return;

  var DATA = window.SIGNAL_AGENT_RUNS || { scenarios: [] };
  var running = false;
  var timers = [];

  function clearTimers() { timers.forEach(clearTimeout); timers = []; }
  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }

  function line(cls, mark, text, detail) {
    var row = document.createElement("div");
    row.className = "ln " + cls;
    var m = document.createElement("span");
    m.className = "mk";
    m.textContent = mark;
    var t = document.createElement("span");
    t.className = "tx";
    t.textContent = text;
    row.appendChild(m);
    row.appendChild(t);
    el.console.appendChild(row);
    if (detail) {
      var d = document.createElement("div");
      d.className = "det";
      d.textContent = detail;
      el.console.appendChild(d);
    }
    el.console.scrollTop = el.console.scrollHeight;
    return row;
  }

  function reset(note) {
    clearTimers();
    el.console.innerHTML = "";
    el.draft.textContent = "";
    el.mode.textContent = note || "";
  }

  /* One event, rendered. Shared by the live path and the replay so the two cannot
     drift apart visually.
     Returns "fallback" when the event means the live run is not going to happen,
     so the caller can play a recording instead of leaving a half-filled console. */
  function render(ev) {
    if (ev.kind === "done") {
      var row = line("done", "", ev.note || "");
      row.className = "ln done";
      return;
    }
    if (ev.kind === "resolved") { line("say", "·", ev.note || ""); return; }
    /* A plain remark from the service - not a step, not a failure. Without this it
       fell through to the step branch, where an absent status renders as a cross,
       so an explanatory note read as something going wrong. */
    if (ev.kind === "note") { line("say", "·", ev.note || ""); return; }
    if (ev.kind === "unavailable") {
      // Not a failure: the agent declining to invent something about a company it
      // has no data on is the system working, so it reads as an answer.
      line("rej", "▲", ev.note || "");
      if (ev.suggestions && ev.suggestions.length) {
        line("say", "·", "Try one of these instead: " + ev.suggestions.join(", "));
      }
      return;
    }
    if (ev.kind === "limited" || ev.kind === "error") return "fallback";
    if (ev.before) line("say", "·", ev.before);
    var cls = ev.status === "ok" ? "ok" : (ev.status === "rejected" ? "rej" : "fail");
    var mark = ev.status === "ok" ? "✓" : (ev.status === "rejected" ? "▲" : "✕");
    line(cls, mark, ev.note || "", ev.detail);
  }

  function typeOut(text) {
    if (!text) return;
    var i = 0;
    el.draft.textContent = "";
    (function tick() {
      if (i >= text.length) return;
      // Several characters per frame: one at a time is slower than reading and
      // stops feeling like output, it starts feeling like waiting.
      el.draft.textContent += text.slice(i, i + 3);
      i += 3;
      later(tick, TYPE_SPEED);
    })();
  }

  /* `why` is the line printed under the console, and it has to be true.
     Playing a recording on arrival is not the same event as a live run failing,
     and saying "live drafting is unavailable right now" before anything has been
     attempted told every arriving visitor the page was broken. */
  function replay(scenario, why) {
    running = true;
    reset(why || (scenario.staged
      ? "Recorded run · the model's misbehaviour here was scripted; the refusal is real"
      : "Recorded run · real agent, real validators"));
    if (scenario.note) line("say", "·", scenario.note);
    line("say", "·", scenario.company + " — " + scenario.title);

    var i = 0;
    (function step() {
      if (i >= scenario.events.length) {
        running = false;
        /* No email is ever typed out from a recording, whoever it was written
           for. Every case that can still write the visitor a real email does -
           the model budget being spent included - so a recording means there was
           nothing true to show them. Showing somebody else's message here,
           however it was labelled, is what made this page feel broken. */
        return;
      }
      render(scenario.events[i++]);
      later(step, STEP_PAUSE);
    })();
  }

  function scenarioById(id) {
    for (var i = 0; i < DATA.scenarios.length; i++) {
      if (DATA.scenarios[i].id === id) return DATA.scenarios[i];
    }
    return DATA.scenarios[0];
  }

  /* Live, when there is something to be live against. Falls back on any failure at
     any point, including mid-stream, because a half-finished console is the one
     outcome worth avoiding. */
  function live(payload, fallbackId) {
    var fellBack = false;
    function fallback() {
      if (fellBack) return;
      fellBack = true;
      replay(scenarioById(fallbackId),
             "Recorded run · the live service did not answer, so no email was "
             + "written for you — try again shortly");
    }

    /* Something on screen before the first event arrives. A second of silence
       after pressing a button reads as nothing having happened. */
    running = true;
    reset("Live run");
    line("say", "·", "Looking that company up...");

    var ctrl = new AbortController();
    var guard = setTimeout(function () { ctrl.abort(); fallback(); }, LIVE_TIMEOUT);

    fetch(API + "/api/agent/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: ctrl.signal
    }).then(function (res) {
      if (!res.ok || !res.body) throw new Error("no stream");
      clearTimeout(guard);
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";
      (function pump() {
        reader.read().then(function (chunk) {
          if (chunk.done) { running = false; return; }
          buffer += decoder.decode(chunk.value, { stream: true });
          var parts = buffer.split("\n\n");
          buffer = parts.pop();
          parts.forEach(function (part) {
            /* ':' opens an SSE comment. The service sends one every few seconds
               while the model is writing, so the connection is never idle long
               enough for a proxy to cut it. There is nothing in it to render. */
            if (part.trim().charAt(0) === ":") return;
            var body = part.replace(/^data: ?/gm, "").trim();
            if (!body) return;
            try {
              var ev = JSON.parse(body);
              if (ev.kind === "draft") { typeOut(ev.text); return; }
              // A refusal to spend, or a server-side error, is indistinguishable
              // from the service being down as far as the visitor is concerned:
              // both play a recording.
              if (render(ev) === "fallback") { fellBack = false; fallback(); }
            } catch (e) { /* a malformed frame is not worth breaking the run for */ }
          });
          pump();
        }).catch(fallback);
      })();
    }).catch(function () { clearTimeout(guard); fallback(); });
  }

  function start(payload, fallbackId) {
    if (running) return;
    if (API) live(payload, fallbackId);
    else replay(scenarioById(fallbackId),
                "Recorded run · no live service is configured for this page");
  }

  /* Whatever the visitor told us about themselves. All optional: an empty object
     means the service uses its default, so the button works with nothing filled in. */
  function sender() {
    var out = {}, map = { name: "s-name", program: "s-program",
                          school: "s-school", term: "s-term" };
    Object.keys(map).forEach(function (key) {
      var field = document.getElementById(map[key]);
      if (field && field.value.trim()) out[key] = field.value.trim();
    });
    return out;
  }

  el.run.addEventListener("click", function () {
    var text = (el.paste.value || "").trim();
    start({ posting: text, sender: sender() }, "real");
  });

  /* Played, not requested. These are recordings of a rail firing, and the server
     has no "run scenario X" mode - it was being sent {scenario: id} with no
     posting, finding nothing to resolve, and failing. Every preset click therefore
     ended on the fallback message, which read as the page being broken when it was
     the button being wired to the wrong thing. */
  el.presets.addEventListener("click", function (e) {
    var id = e.target.getAttribute("data-scenario");
    if (!id || running) return;
    replay(scenarioById(id));
  });

  /* Play the real run once on arrival, so the panel is never an empty box. This
     is a sample and says so: nothing has been attempted yet, and the visitor has
     not typed anything for it to have been attempted with. */
  if (DATA.scenarios.length) {
    replay(scenarioById("real"),
           "A recorded run, so the console is not empty · paste a posting above "
           + "and press Run to do it live with your own details");
  }
})();
