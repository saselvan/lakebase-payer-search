/* The motion diagram of one search: browser -> this app -> Data API -> api.search_insurers, and the answer back up
   the same line into box 1.
   Input is one data object (see index.html, #pfd-run). Every value is set as text, never as markup.
   The motion is the preview Samuel approved on 2026-10-24 (storyboard-r6/anim.html, draw(t)). frame(t) below is that
   function of time; animate() samples it at every video frame (1/30 s) into Web Animations keyframes, so every frame
   of the video is the approved one, and HyperFrames, the Play button and check.py seek it as one set of animations.
   Each box has two layers in one place: the short text the motion shows (hidden from screen readers) and the full
   sentences of the final frame. At the end the boxes grow from the short layout to the full one in one move.
   mount() opens on the final frame and never starts the motion by itself. With { controls: true } it adds a "Play"
   button (the drawer). Under prefers-reduced-motion: reduce, and below 720 px (a phone: the drawer is a page, not a
   frame), there is no button and no animation, only the still. */
(function () {
  "use strict";

  // Labels the server sets: shown by name with what they are, never with their value (OBJECTIONS H14, H15).
  var SERVER_SET = { customer_id: "a label the server sets" };
  var AUTH_NOTE = "OAuth token of the app's service principal. The browser never gets it.";
  // The reason phrase of an HTTP status (RFC 9110, section 15). A code not in this list shows as the bare number.
  var REASON = { 200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
                 403: "Forbidden", 404: "Not Found", 409: "Conflict", 422: "Unprocessable Content",
                 429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway",
                 503: "Service Unavailable", 504: "Gateway Timeout" };

  // The approved clock (anim.html), in seconds. Every move takes MOVE on the symmetric ease E. Box 1 holds BOX1
  // before the first move (owner, 2026-10-24). A box appears APPEAR_LEAD before the packet leaves for it, and fills
  // over FILL from contact. A tag holds its reading time, then fades over TAG_FADE. The grow starts GROW_GAP after the
  // answer is in box 1, and the final frame holds END_HOLD. The motion shows the order, never a time (H9).
  var FPS = 30;
  var MOVE = 0.75;
  var BOX1 = 1.4;
  var APPEAR_LEAD = 0.2;
  var FILL = 0.5;
  var TAG_FADE = 0.3;
  var GROW_GAP = 0.2;
  var END_HOLD = 3;
  // A box stays lit long enough to read its words aloud: 183 words a minute (Brysbaert 2019, J. Mem. Lang. 109,
  // 104047, "The average oral reading rate ... is 183 wpm"), and 1.4 s or more. Code tokens count as words.
  var WORDS_PER_S = 183 / 60;
  var MIN_READ = 1.4;

  function node(tag, cls, parts) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    (parts || []).forEach(function (p) {
      if (typeof p === "string") el.appendChild(document.createTextNode(p));
      else el.appendChild(p);
    });
    return el;
  }

  function code(text) {
    return node("code", null, [text]);
  }

  // One unit that never breaks across two lines unless it is wider than its box (then only at its own spaces).
  function tok(text) {
    return node("span", "pfd-tok", [text]);
  }

  // Tokens with a break opportunity (a plain space) between them.
  function tokens(list) {
    var out = [];
    list.forEach(function (t, i) {
      if (i) out.push(" ");
      out.push(tok(t));
    });
    return out;
  }

  // A sentence with its claims row in docs/CATECHISM.md (check.py holds the exact wording for each row).
  function note(parts, claim) {
    var p = node("p", "pfd-note", parts);
    if (claim) p.dataset.claim = claim;
    return p;
  }

  // A short line of the motion, with its claims rows (check.py T5-1 holds the approved list).
  function shortLine(parts, claim) {
    var el = node("p", "pfd-short-line", parts);
    if (claim) el.dataset.claim = claim;
    return el;
  }

  // The JSON body as one token per key and value pair, so a pair never splits across two lines.
  function jsonPairs(obj) {
    var keys = Object.keys(obj);
    return keys.map(function (k, i) {
      return (i === 0 ? "{" : "") + JSON.stringify(k) + ": " + JSON.stringify(obj[k]) + (i === keys.length - 1 ? "}" : ",");
    });
  }

  function square(n) {
    var sq = node("span", "pfd-sq", [String(n)]);
    sq.setAttribute("aria-hidden", "true");
    return sq;
  }

  function status(code) {
    return REASON[code] ? code + " " + REASON[code] : String(code);
  }

  // What the packet carries: the browser's body keys to the app, the app's body keys on to the function. The same
  // values as the code boxes: box 1's JSON, box 2's "body keys". The answer is box 1's answer line.
  function labels(run) {
    return {
      browser: Object.keys(run.browser_body).join(", "),
      app: run.app_body_keys.join(", "),
      answer: status(run.status) + " \u00b7 " + (run.row_count === 1 ? "1 row" : run.row_count + " rows")
    };
  }

  function hops(run) {
    var l = labels(run);
    var keys = run.app_body_keys;
    // A closed row only. Ticket 05 fills it from the function's real definition; nothing here types it.
    var def = node("div", "pfd-def", [node("span", "pfd-chev", []), " Function definition"]);
    def.setAttribute("data-placeholder", "ticket 05");
    var keyToks = keys.map(function (k, i) { return k + (i < keys.length - 1 ? "," : ""); });
    var req1 = function () {
      return node("div", "pfd-code", [node("pre", null, tokens(["POST", "/api/search"]).concat(["\n"], tokens(jsonPairs(run.browser_body))))]);
    };
    var req2 = function () {
      return node("div", "pfd-code", [node("pre", null, tokens(["POST", "/api/rpc/search_insurers"]).concat(["\n"], tokens(["body keys:"].concat(keyToks))))]);
    };
    return [
      { id: "browser", name: ["Browser"],
        short: [req1()],
        full: [note(["Sent only the query and the page to this app."]), req1(),
               // The answer is its own block, apart from the request: the reply this app sent back to the browser.
               node("div", "pfd-answer", [node("span", "pfd-answer-k", ["Answer from this app"]), " ",
                                          node("code", "pfd-ans", [l.answer])])] },
      { id: "app", name: ["This app"],
        short: [req2(), shortLine(["Server sets ", code("customer_id"), ". The browser never gets the token."], "C17 C7")],
        full: [note(["Sent the search to the Data API."]), req2()].concat(
               keys.filter(function (k) { return SERVER_SET[k]; }).map(function (k) {
                 return note([code(k), " is " + SERVER_SET[k] + "."], "C17");
               }), [note(["Authorization: " + AUTH_NOTE], "C7")]) },
      { id: "data-api", name: ["Lakebase Data API"],
        short: [shortLine(["App role: 2 functions, their 2 schemas, standard defaults. No data table rights."], "C18 C6")],
        full: [note(["Runs the function as an RPC over HTTP POST. The path names the function."], "C14"),
               note(["Switches into the app's own Postgres role."], "C18"),
               note(["It can run the two search functions and use their two schemas, plus standard defaults. No rights on any data table."], "C6")] },
      { id: "function", name: ["Postgres function ", code("api.search_insurers")],
        short: [shortLine(["Typo matching and ranking run here. ", code("search_core"), " runs as ",
                           code("search_owner"), ", which cannot log in."], "C2 C19")],
        full: [note(["Typo matching and ranking run inside it."], "C2"),
               note(["Runs with the caller's rights and calls ", code("search_idx.search_core"), "."], "C19"),
               note([code("search_core"), " runs with the rights of its owner, ", code("search_owner"),
                     ", a role that cannot log in. It is the only function that reads the search index."], "C19"),
               note(["Both functions pin ", code("search_path"), " to ", code("pg_catalog, pg_temp"), "."], "C8"),
               def] }
    ];
  }

  function packet(run) {
    var l = labels(run);
    var label = function (cls, text) { return node("span", "pfd-tag-label " + cls, [text]); };
    var stack = node("span", "pfd-tag-stack", [label("pfd-tag-browser", l.browser), label("pfd-tag-app", l.app)]);
    stack.setAttribute("data-layout-allow-overflow", "");
    var tag = node("div", "pfd-tag", [stack]);
    var sq = node("span", "pfd-pk-sq", []);
    var ansSq = node("span", "pfd-ans-sq", []);
    [tag, sq, ansSq].forEach(function (el) {
      el.setAttribute("aria-hidden", "true");
      el.setAttribute("data-layout-allow-overlap", "");
      el.setAttribute("data-layout-allow-occlusion", "");
    });
    return { before: [tag, sq], after: [ansSq] };
  }

  function render(root, run) {
    root.textContent = "";
    root.classList.add("pfd");
    var top = node("div", "pfd-top", [node("span", "pfd-red", []), node("h2", "pfd-title", ["How this search ran"]),
                                      node("span", "pfd-yellow", [])]);
    var list = node("ol", "pfd-hops", []);
    hops(run).forEach(function (h, i, all) {
      var shortText = node("div", "pfd-text pfd-short", h.short);
      shortText.setAttribute("aria-hidden", "true");
      var shortBox = node("div", "pfd-box pfd-short-box", [node("h3", "pfd-name", h.name), shortText]);
      var twin = node("h3", "pfd-name", h.name.map(function (p) { return typeof p === "string" ? p : p.cloneNode(true); }));
      twin.setAttribute("aria-hidden", "true");
      var fullBox = node("div", "pfd-box pfd-full-box", [node("span", "pfd-fill", []), twin, node("div", "pfd-text pfd-full", h.full)]);
      var li = node("li", "pfd-hop", [square(i + 1), node("div", "pfd-body", [shortBox, fullBox])]);
      li.dataset.hop = h.id;
      if (i < all.length - 1) {
        var link = node("span", "pfd-link", []);
        link.setAttribute("aria-hidden", "true");
        li.appendChild(link);
      }
      list.appendChild(li);
    });
    // The answer's square comes after the list, so it paints over the line and under the numbers (anim.html).
    var p = packet(run);
    root.appendChild(node("div", "pfd-card", [top, node("div", "pfd-path", p.before.concat([list], p.after))]));
  }

  var REDUCE = "(prefers-reduced-motion: reduce)";
  var NARROW = "(max-width: 719px)";
  function matches(q) {
    return !!(window.matchMedia && window.matchMedia(q).matches);
  }
  // The still only: under reduced motion, and on a phone, where the drawer is a page (DECISIONS 2026-10-24).
  function stillOnly() {
    return matches(REDUCE) || matches(NARROW);
  }

  function count(s) {
    return s.split(/\s+/).filter(Boolean).length;
  }

  // The words a reader sees in a box in the motion (the same rule as check.py T4-50): the heading and the short text.
  function words(li) {
    return count(Array.prototype.map.call(li.querySelectorAll(".pfd-short-box > .pfd-name, .pfd-short > *"),
                                          function (e) { return e.textContent; }).join(" "));
  }

  function readS(n) {
    return Math.max(MIN_READ, n / WORDS_PER_S);
  }

  // The approved timeline (anim.html), from the words on screen. Box k+1 stays lit for the reading time of its
  // words, and at least for the tag's reading time and fade. Boxes 2 and 3 drain at the next contact, a move after
  // the packet leaves; box 4 drains as the answer leaves, so it holds one fill longer to stay fully lit for its
  // reading time (T4-50; the one change to the approved clock, round 6).
  function timeline(root) {
    var lis = root.querySelectorAll(".pfd-hop");
    var l = root.querySelectorAll(".pfd-tag-label");
    var tagRead = readS(Math.max.apply(null, Array.prototype.map.call(l, function (e) { return count(e.textContent); })));
    var T = { b1: -MOVE, hops: [] }, c = BOX1;
    for (var k = 0; k < 3; k++) {
      var h = { go: c, at: c + MOVE };
      h.off = h.at + tagRead;
      T.hops.push(h);
      c = h.at + Math.max(tagRead + TAG_FADE, readS(words(lis[k + 1])) + (k === 2 ? FILL : 0));
    }
    T.cl = c;
    T.g = c + MOVE + GROW_GAP;
    T.end = T.g + MOVE;
    return T;
  }

  // The same timeline in milliseconds, for check.py and stills.py. end: the motion's end (the final frame holds
  // END_HOLD after it).
  function schedule(root) {
    var T = timeline(root), ms = function (s) { return Math.round(s * 1000); };
    var m = { hops: T.hops.map(function (h) { return { go: ms(h.go), at: ms(h.at), off: ms(h.off) }; }),
              climb: { s: ms(T.cl), e: ms(T.cl + MOVE) }, grow: { s: ms(T.g), e: ms(T.end) },
              shortOut: { s: ms(T.g), e: ms(T.g + 0.35 * MOVE) }, fullIn: { s: ms(T.g + 0.65 * MOVE), e: ms(T.end) } };
    return { m: m, end: ms(T.end), hold: END_HOLD * 1000 };
  }

  var E = function (x) { return x <= 0 ? 0 : x >= 1 ? 1 : (1 - Math.cos(Math.PI * x)) / 2; };
  var R = function (t, a, d) { return E((t - a) / d); };
  var clamp = function (x) { return Math.max(0, Math.min(1, x)); };
  var lerp = function (a, b, p) { return p <= 0 ? a : p >= 1 ? b : a + (b - a) * p; };

  // The layout the motion runs on, from the page: each box's height in the short layout and in the full one, and the
  // unit. Measured again when the fonts arrive or the width changes.
  function measure(root) {
    var lis = Array.prototype.slice.call(root.querySelectorAll(".pfd-hop"));
    var u = lis[0].querySelector(".pfd-sq").getBoundingClientRect().height / 28;
    var h = function (li, s) { return Math.max(28 * u, li.querySelector(s).getBoundingClientRect().height); };
    return { u: u, sh: lis.map(function (li) { return h(li, ".pfd-short-box"); }),
             fh: lis.map(function (li) { return h(li, ".pfd-full-box"); }),
             mbS: 36 * u, mbF: 4 * u, padS: 14 * u, padF: 6 * u };
  }

  // draw(t) of anim.html, as values. Positions are in the path's box (the list's top left corner).
  function frame(t, T, M) {
    var U = M.u, H = T.hops, n = M.sh.length;
    var g = R(t, T.g, MOVE);
    var topS = [], topF = [], sS = M.padS, sF = M.padF;
    for (var i = 0; i < n; i++) { topS.push(sS); topF.push(sF); sS += M.sh[i] + M.mbS; sF += M.fh[i] + M.mbF; }
    var top = function (i) { return lerp(topS[i], topF[i], g); };
    var sq = function (i) { return top(i) + 14 * U; };
    var bot = function (i) { return topS[i] + M.sh[i]; };
    var on = function (k) { return R(t, H[k].at, FILL); };
    var v = {
      g: g,
      app: [R(t, T.b1, MOVE)].concat(H.map(function (h) { return R(t, h.go - APPEAR_LEAD, MOVE); })),
      lit: [1 - on(0) + R(t, T.cl + MOVE * 0.6, FILL), on(0) - on(1), on(1) - on(2), on(2) - R(t, T.cl, FILL)].map(clamp),
      h: M.sh.map(function (s, i) { return lerp(s, M.fh[i], g); }),
      mb: lerp(M.mbS, M.mbF, g), pad: lerp(M.padS, M.padF, g)
    };
    var gl = clamp((t - T.g) / MOVE);
    v.short = 1 - E(gl / 0.35);
    v.full = E((gl - 0.65) / 0.35);
    // The packet runs from box k's number to box k+1's number; its tag joins it once it is out of box k and waits at
    // box k+1's top edge; the line is drawn as far as the packet has gone. While the tag fades in, its top never
    // rises above box k's bottom edge, so it covers no word and no number (T4-48, T4-26).
    var end = sq(0);
    v.pk = { y: sq(0), o: 0 };
    v.tag = { y: topS[1] - 24 * U, o: 0, k: 0 };
    H.forEach(function (h, k) {
      if (t < h.go) return;
      var y = lerp(sq(k), sq(k + 1), R(t, h.go, MOVE));
      if (t < h.at) v.pk = { y: y, o: 1 };
      else v.pk = { y: sq(k + 1), o: 0 };
      if (t < h.off + TAG_FADE) {
        var dock = topS[k + 1] - 22 * U - 2 * U;
        v.tag = { y: Math.min(Math.max(y - 11 * U, bot(k)), dock), k: k,
                  o: clamp((y - bot(k)) / (22 * U)) * (1 - clamp((t - h.off) / TAG_FADE)) };
      } else {
        v.tag = { y: v.tag.y, o: 0, k: k };
      }
      end = Math.max(end, y);
    });
    if (t >= T.cl) end = sq(n - 1);
    v.link = [];
    for (var j = 0; j < n - 1; j++) {
      var len = sq(j + 1) - sq(j);
      v.link.push({ len: len, cut: len - Math.max(0, Math.min(len, end - sq(j))) });
    }
    // The answer climbs as a square on the line, under the numbers, into box 1, and fades as the boxes grow.
    v.ans = { y: lerp(sq(n - 1), sq(0), R(t, T.cl, MOVE)),
              o: t >= T.cl && t < T.g + MOVE ? 1 - R(t, T.g, MOVE * 0.5) : 0 };
    return v;
  }

  var px = function (x) { return (Math.round(x * 1000) / 1000) + "px"; };

  // What each animated element takes from a frame. "last": the final keyframe's value, the CSS default's own value
  // (transform: none included), so the last frame and the reduced frame paint the same layers.
  function tracks(root) {
    var q = function (s) { return root.querySelector(s); };
    var out = [];
    var add = function (el, prop, fn, last) { out.push({ el: el, prop: prop, fn: fn, last: last }); };
    var lis = root.querySelectorAll(".pfd-hop");
    lis.forEach(function (li, i) {
      add(li.querySelector(".pfd-sq"), "opacity", function (v) { return v.app[i]; });
      var body = li.querySelector(".pfd-body");
      add(body, "opacity", function (v) { return v.app[i]; });
      add(body, "height", function (v) { return px(v.h[i]); }, "auto");
      add(li.querySelector(".pfd-fill"), "opacity", function (v) { return v.lit[i]; });
      add(li.querySelector(".pfd-short"), "opacity", function (v) { return v.short; });
      add(li.querySelector(".pfd-full"), "opacity", function (v) { return v.full; });
      if (i === lis.length - 1) return;
      add(li, "marginBottom", function (v) { return px(v.mb); }, "var(--mb-full)");
      var link = li.querySelector(".pfd-link");
      add(link, "height", function (v) { return px(v.h[i] + v.mb); }, "calc(100% + var(--mb-full))");
      add(link, "clipPath", function (v) { return "inset(0px 0px " + px(v.link[i].cut) + " 0px)"; });
    });
    var ol = q(".pfd-hops");
    // The list is laid out again at the last keyframe (flow-root to block draws the same box): after the boxes have
    // grown, Chromium keeps a sub-pixel paint of the old layout, and the final frame would not match the still.
    add(ol, "display", function () { return "flow-root"; }, "block");
    add(ol, "paddingTop", function (v) { return px(v.pad); }, "var(--pad-full)");
    add(ol, "paddingBottom", function (v) { return px(v.pad); }, "var(--pad-full)");
    var pk = q(".pfd-pk-sq"), tag = q(".pfd-tag"), ans = q(".pfd-ans-sq");
    add(pk, "transform", function (v, M) { return "translateY(" + px(v.pk.y - 5 * M.u) + ")"; }, "none");
    add(pk, "opacity", function (v) { return v.pk.o; });
    add(tag, "transform", function (v) { return "translateY(" + px(v.tag.y) + ")"; }, "none");
    add(tag, "opacity", function (v) { return v.tag.o; });
    add(q(".pfd-tag-browser"), "opacity", function (v) { return v.tag.k === 0 ? 1 : 0; });
    add(q(".pfd-tag-app"), "opacity", function (v) { return v.tag.k === 0 ? 0 : 1; });
    add(ans, "transform", function (v, M) { return "translateY(" + px(v.ans.y - 5 * M.u) + ")"; }, "none");
    add(ans, "opacity", function (v) { return v.ans.o; });
    return out;
  }

  // One keyframe per video frame, from 0 to the motion's end; a run of equal values keeps only its two ends.
  function keyframes(track, T, M) {
    var n = Math.ceil(T.end * FPS - 1e-9), times = [];
    for (var i = 0; i < n; i++) times.push(i / FPS);
    times.push(T.end);
    var vals = times.map(function (t) {
      var x = track.fn(frame(t, T, M), M);
      return typeof x === "number" ? Math.round(x * 1e6) / 1e6 : x;
    });
    var out = [];
    vals.forEach(function (x, i) {
      if (i > 0 && i < vals.length - 1 && x === vals[i - 1] && x === vals[i + 1]) return;
      var k = { offset: times[i] / T.end };
      k[track.prop] = x;
      out.push(k);
    });
    if (track.last) {
      var pre = { offset: 1 - 0.0005 / T.end };
      pre[track.prop] = vals[vals.length - 1];
      out[out.length - 1][track.prop] = track.last;
      out.splice(out.length - 1, 0, pre);
    }
    return out;
  }

  // One long take. With the still only, nothing is created, and the CSS default (the final frame) stays.
  function animate(root) {
    if (stillOnly() || !root.animate) return [];
    var T = timeline(root), list = tracks(root), M = measure(root);
    var made = list.map(function (tr) {
      return tr.el.animate(keyframes(tr, T, M), { duration: T.end * 1000, delay: 0, fill: "both", easing: "linear", iterations: 1 });
    });
    // The keyframes hold lengths of this layout: when the fonts arrive or the width changes, they are made again.
    var width = root.getBoundingClientRect().width, live = true, observer = null;
    var redo = function () {
      if (!live) return;
      M = measure(root);
      made.forEach(function (a, i) { a.effect.setKeyframes(keyframes(list[i], T, M)); });
    };
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(redo);
    if (window.ResizeObserver) {
      observer = new ResizeObserver(function () {
        var w = root.getBoundingClientRect().width;
        if (w !== width) { width = w; redo(); }
      });
      observer.observe(root);
    }
    // detach() stops the re-measuring: the observer goes, and a late fonts.ready does nothing.
    made.detach = function () {
      live = false;
      if (observer) observer.disconnect();
      observer = null;
    };
    return made;
  }

  // Draw the search. Nothing runs by itself. The drawer ({ controls: true }) opens on the final frame, and Play
  // restarts the one set of animations from the start, however often it is pressed. If the reader turns on reduced
  // motion, or the page narrows to a phone's width, every animation is removed and the button goes: the still stays.
  // The composition (no controls) waits at time 0 for HyperFrames, whose waapi adapter takes an animation's time when
  // it first finds it as an offset.
  function mount(root, run, opts) {
    render(root, run);
    var anims = [], button = null;
    var atEnd = !!(opts && opts.controls);
    var start = function () {
      anims = animate(root);
      anims.forEach(function (a) { a.pause(); a.currentTime = atEnd ? a.effect.getComputedTiming().endTime : 0; });
      if (!anims.length || !(opts && opts.controls)) return;
      button = node("button", "pfd-play", ["Play"]);
      button.type = "button";
      root.querySelector(".pfd-top").insertBefore(button, root.querySelector(".pfd-yellow"));
      root.classList.add("pfd-has-play");
      button.addEventListener("click", function () {
        anims.forEach(function (a) { a.currentTime = 0; a.play(); });
      });
    };
    var stop = function () {
      if (anims.detach) anims.detach();
      anims.forEach(function (a) { a.cancel(); });
      anims = [];
      if (button) button.remove();
      button = null;
      root.classList.remove("pfd-has-play");
    };
    var sync = function () {
      if (stillOnly()) stop();
      else if (!anims.length) start();
    };
    start();
    var reduce = null, narrow = null;
    if (window.matchMedia) {
      reduce = window.matchMedia(REDUCE);
      narrow = window.matchMedia(NARROW);
      if (reduce.addEventListener) reduce.addEventListener("change", sync);
      if (narrow.addEventListener) narrow.addEventListener("change", sync);
    }
    // destroy() lets go of the page: the two media listeners, the observer and the fonts callback. It leaves the
    // animations to the caller, which cancels them with the nodes it removes.
    var destroy = function () {
      if (reduce && reduce.removeEventListener) reduce.removeEventListener("change", sync);
      if (narrow && narrow.removeEventListener) narrow.removeEventListener("change", sync);
      reduce = narrow = null;
      if (anims.detach) anims.detach();
    };
    return { animations: anims, button: button, destroy: destroy };
  }

  window.PayerFinderDiagram = { render: render, animate: animate, mount: mount, labels: labels, schedule: schedule };

  var data = document.getElementById("pfd-run");
  var root = document.getElementById("pfd");
  if (data && root) mount(root, JSON.parse(data.textContent), { controls: root.hasAttribute("data-controls") });
})();
