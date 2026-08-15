/* Lexical Fingerprints PoC — word lookup, word view, model-page word checker. */
(function () {
  var search = document.getElementById('wordsearch');
  var modelSearch = document.getElementById('modelwordsearch');
  if (!search && !modelSearch) return;

  var base = '/data/vocab/';
  var modelsP = null, vocabP = null, shardCache = {};

  function fnv1a(s) {
    var h = 0x811c9dc5;
    var bytes = new TextEncoder().encode(s);
    for (var i = 0; i < bytes.length; i++) {
      h ^= bytes[i];
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h >>> 0;
  }
  function getModels() {
    if (!modelsP) modelsP = fetch(base + 'models.json').then(function (r) { return r.json(); });
    return modelsP;
  }
  function getVocab() {
    if (!vocabP) vocabP = fetch(base + 'vocab.json').then(function (r) { return r.json(); });
    return vocabP;
  }
  function getShard(word) {
    var b = fnv1a(word) % 32;
    if (!shardCache[b]) shardCache[b] = fetch(base + 'w/' + b + '.json').then(function (r) { return r.json(); });
    return shardCache[b];
  }
  var ctxCache = {};
  function getCtx(word) {
    var b = fnv1a(word) % 32;
    if (!ctxCache[b]) {
      ctxCache[b] = fetch(base + 'ctx/' + b + '.json')
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; });
    }
    return ctxCache[b];
  }
  var occCache = {};
  function getOcc(word) {
    var b = fnv1a(word) % 32;
    if (!occCache[b]) {
      occCache[b] = fetch(base + 'occ/' + b + '.json')
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; });
    }
    return occCache[b];
  }
  var qidsP = null;
  function getQids() {
    if (!qidsP) qidsP = fetch(base + 'qids.json').then(function (r) { return r.json(); });
    return qidsP;
  }
  function responseUrl(siteSlug, qid) {
    // qid = theme + trailing variation digit; responses live on theme pages
    var v = /\d$/.test(qid) ? qid.slice(-1) : '';
    var theme = v ? qid.slice(0, -1) : qid;
    return '/themes/' + encodeURIComponent(theme) + '/m/' + siteSlug +
      '/' + (v ? '#v' + v : '');
  }
  function useLinks(siteSlug, qids, label) {
    return qids.map(function (qid, k) {
      return '<a href="' + responseUrl(siteSlug, qid) + '" title="' +
        qid.replace(/"/g, '') + '">' + (label || 'response') + ' ' + (k + 1) + ' ↗</a>';
    }).join(' · ');
  }
  function highlighted(word, text) {
    var safe = text.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    var rx = new RegExp('\\b(' + word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')\\b', 'ig');
    return safe.replace(rx, '<mark>$1</mark>');
  }

  // ---- model-page word checker
  if (modelSearch) {
    var mid = modelSearch.dataset.mid;
    var msite = modelSearch.dataset.site;
    // live extraction: fetch the same theme shard the theme page renders from,
    // pull this model's variation card, and cut snippets around the word —
    // records stay the single source of truth, nothing duplicated
    var themeShardCache = {};
    function fetchThemeShard(theme) {
      var bucket = fnv1a(msite) % 8;
      var url = '/data/theme-shards/' + theme + '.' + bucket + '.json';
      if (!themeShardCache[url]) {
        themeShardCache[url] = fetch(url).then(function (r) { return r.ok ? r.json() : null; });
      }
      return themeShardCache[url];
    }
    function extractUses(theme, variation, word, limit) {
      limit = limit || 3;
      return fetchThemeShard(theme)
        .then(function (shard) {
          var entry = shard && shard[msite];
          if (!entry || !entry.html) return [];
          var doc = new DOMParser().parseFromString(entry.html, 'text/html');
          // search the matched variation first, then the theme's other
          // variations — the shard is already fetched, so more examples are free
          var cards = Array.prototype.slice.call(doc.querySelectorAll('.response-card-nested'));
          cards.sort(function (a, b) {
            var am = (a.querySelector('.response-header') || {textContent: ''}).textContent.indexOf('Variation ' + variation) !== -1;
            var bm = (b.querySelector('.response-header') || {textContent: ''}).textContent.indexOf('Variation ' + variation) !== -1;
            return (bm ? 1 : 0) - (am ? 1 : 0);
          });
          var rx = new RegExp('\\b' + word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'ig');
          var out = [];
          cards.forEach(function (card) {
            if (out.length >= limit) return;
            var body = card.querySelector('.markdown-content');
            if (!body) return;
            var text = ' ' + body.textContent.replace(/\s+/g, ' ') + ' ';
            rx.lastIndex = 0;
            var m;
            while ((m = rx.exec(text)) && out.length < limit) {
              var lo = Math.max(0, m.index - 90), hi = Math.min(text.length, m.index + word.length + 110);
              out.push((lo > 0 ? '…' : '') + text.slice(lo, hi).trim() + (hi < text.length ? '…' : ''));
            }
          });
          return out;
        })
        .catch(function () { return []; });
    }
    function checkWord(w) {
      Promise.all([getModels(), getShard(w), getOcc(w), getQids()]).then(function (rs) {
        var models = rs[0], shard = rs[1], occ = rs[2], qidTable = rs[3];
        var view = document.getElementById('modelwordview');
        view.hidden = false;
        var entry = shard[w];
        if (!entry) { view.innerHTML = '<p class="muted">Not in the watch vocabulary.</p>'; return; }
        var myIdx = models.findIndex(function (m) { return m.id === mid; });
        var mine = null, myQis = [], rank = 0, users = 0;
        entry.m.slice().sort(function (a, b) { return b[1] - a[1]; }).forEach(function (p, i) {
          users++;
          if (p[0] === myIdx) {
            mine = p[1];
            myQis = Array.isArray(p[2]) ? p[2] : (p[2] >= 0 ? [p[2]] : []);
            rank = i + 1;
          }
        });
        var head = '<div class="panel"><h4>' + w.replace(/</g, '&lt;') + '</h4>';
        var fwLink = '<p class="fw-link"><a href="/experiments/vocab/?w=' + encodeURIComponent(w) +
          '">View &ldquo;' + w.replace(/</g, '&lt;') + '&rdquo; on all models &rarr;</a></p>';
        if (mine === null) {
          view.innerHTML = head + '<p class="muted">No uses in this model\'s COMPLETE answers ' +
            '(' + users + ' other models use it).</p>' + fwLink + '</div>';
          return;
        }
        head += '<p>' + mine + '/M — rank ' + rank + ' of ' + users + ' models using this word ' +
          'in COMPLETE answers.</p>';
        // every recorded use carries up to 3 format-diverse pointers;
        // rare words may add more from the occurrence index
        var extra = ((occ[w] || {})[String(myIdx)] || []).filter(function (qi) {
          return myQis.indexOf(qi) === -1;
        });
        var qids = myQis.concat(extra).map(function (qi) { return qidTable[qi]; }).filter(Boolean);
        if (!qids.length) {
          view.innerHTML = head + fwLink + '</div>';
          return;
        }
        view.innerHTML = head + '<p class="muted small">Fetching examples…</p></div>';
        var shown = qids.slice(0, 3);
        Promise.all(shown.map(function (qid) {
          var v = /\d$/.test(qid) ? qid.slice(-1) : '';
          var theme = v ? qid.slice(0, -1) : qid;
          return extractUses(theme, v || '0', w, 2).then(function (uses) {
            return { qid: qid, theme: theme, uses: uses };
          });
        })).then(function (groups) {
          var inner = head;
          var any = false;
          var cards = '';
          groups.forEach(function (g) {
            if (!g.uses.length) return;
            any = true;
            cards += '<div class="ctx-card">';
            g.uses.forEach(function (u) {
              cards += '<blockquote>' + highlighted(w, u) + '</blockquote>';
            });
            cards += '<div class="ctx-meta"><span>' +
              g.theme.replace(/_/g, ' ').replace(/</g, '&lt;') + '</span>' +
              '<a href="' + responseUrl(msite, g.qid) + '">view response ↗</a></div></div>';
          });
          if (cards) inner += '<div class="checker-cards">' + cards + '</div>';
          if (!any) {
            inner += '<p class="muted small">Answers that use it: ' + useLinks(msite, shown) + '</p>';
          }
          if (qids.length > shown.length) {
            inner += '<p class="muted small">More answers that use it: ' +
              useLinks(msite, qids.slice(shown.length)) + '</p>';
          }
          view.innerHTML = inner + fwLink + '</div>';
        });
      });
    }
    modelSearch.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && modelSearch.value.trim()) checkWord(modelSearch.value.trim().toLowerCase());
    });
    // any word link on a model page checks THAT word for THIS model in place —
    // the field-wide view stays one explicit click away inside the result
    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a.w');
      if (!a) return;
      var w = new URL(a.href, location).searchParams.get('w');
      if (!w) return;
      e.preventDefault();
      modelSearch.value = w;
      checkWord(w.toLowerCase());
      var url = new URL(location);
      url.searchParams.set('w', w);
      history.replaceState(null, '', url);
      modelSearch.scrollIntoView({ block: 'center' });
    });
    var initialW = new URL(location).searchParams.get('w');
    if (initialW) {
      modelSearch.value = initialW;
      checkWord(initialW.toLowerCase());
      modelSearch.scrollIntoView({ block: 'center' });
    }
    if (!search) return;   // model pages have no overview word view
  }

  // Shared hover tooltip + click-through for SVG dots.
  var tip = null;
  function ensureTip() {
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'sm-tip';
      tip.hidden = true;
      document.body.appendChild(tip);
    }
    return tip;
  }
  function bindDots(container, selector, getInfo) {
    // rebinding after a re-render only swaps the lookup, listeners attach once
    container._dotInfo = getInfo;
    if (container.dataset.dotsBound) return;
    container.dataset.dotsBound = '1';
    var t = ensureTip();
    container.addEventListener('mouseover', function (e) {
      var el = e.target.closest && e.target.closest(selector);
      if (!el) return;
      var info = container._dotInfo(el);
      el.dataset.r0 = el.getAttribute('r');
      el.setAttribute('r', 5.5);
      t.textContent = info.text;
      t.hidden = false;
    });
    container.addEventListener('mousemove', function (e) {
      if (t.hidden) return;
      t.style.left = (e.pageX + 14) + 'px';
      t.style.top = (e.pageY + 10) + 'px';
    });
    container.addEventListener('mouseout', function (e) {
      var el = e.target.closest && e.target.closest(selector);
      if (!el) return;
      if (el.dataset.r0) el.setAttribute('r', el.dataset.r0);
      t.hidden = true;
    });
    container.addEventListener('click', function (e) {
      var el = e.target.closest && e.target.closest(selector);
      if (!el) return;
      var info = container._dotInfo(el);
      if (info.href) location.assign(info.href);
    });
  }

  // Overview map dots (build-time SVG) get the same treatment.
  var mapwrap = document.querySelector('.mapwrap');
  if (mapwrap) {
    bindDots(mapwrap, '.mdot', function (el) {
      return { text: el.dataset.name + ' (' + el.dataset.lab + ')', href: '/experiments/vocab/m/' + el.dataset.slug + '/' };
    });
  }

  // Build a snippet card safely: text nodes only, word highlighted via <mark>.
  function snippetCard(word, snip, models) {
    var card = document.createElement('div');
    card.className = 'ctx-card';
    var q = document.createElement('blockquote');
    var rx = new RegExp('\\b(' + word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')\\b', 'ig');
    var parts = snip.t.split(rx);
    parts.forEach(function (part, i) {
      if (i % 2 === 1) {
        var mk = document.createElement('mark');
        mk.textContent = part;
        q.appendChild(mk);
      } else if (part) {
        q.appendChild(document.createTextNode(part));
      }
    });
    card.appendChild(q);
    var meta = document.createElement('div');
    meta.className = 'ctx-meta';
    var m = models[snip.m];
    var name = document.createElement('span');
    name.textContent = m.name + ' · ' + m.date.slice(0, 7);
    meta.appendChild(name);
    var q = snip.q || (snip.th ? snip.th + (snip.v || '') : '');
    if (q) {
      var a = document.createElement('a');
      a.href = responseUrl(m.slug, q);
      a.textContent = 'view response ↗';
      meta.appendChild(a);
    }
    card.appendChild(meta);
    return card;
  }

  // dynamic autocomplete: all prefix matches, capped — a static datalist big
  // enough for the whole vocabulary is slow, a subsampled one drops words
  var vocabArr = null;
  search.addEventListener('focus', function () {
    getVocab().then(function (v) { vocabArr = v; });
  });
  function updateOptions(prefix) {
    var dl = document.getElementById('vocablist');
    dl.textContent = '';
    if (!vocabArr || !prefix) return;
    var frag = document.createDocumentFragment(), count = 0;
    for (var i = 0; i < vocabArr.length && count < 30; i++) {
      if (vocabArr[i].lastIndexOf(prefix, 0) === 0) {
        var o = document.createElement('option');
        o.value = vocabArr[i];
        frag.appendChild(o);
        count++;
      }
    }
    dl.appendChild(frag);
  }

  function monthRange(models) {
    var dates = models.map(function (m) { return new Date(m.date); });
    var lo = new Date(Math.min.apply(null, dates)), hi = new Date(Math.max.apply(null, dates));
    return [lo, hi];
  }

  function render(word, entry, models) {
    var view = document.getElementById('wordview');
    var rates = {};
    entry.m.forEach(function (p) { rates[p[0]] = p[1]; });
    var range = monthRange(models), lo = range[0], hi = range[1];
    var span = hi - lo || 1;
    var W = 660, H = 262, L = 46, R = 640, T = 14, B = 186, RAIL = 218;
    var maxRate = 1;
    entry.m.forEach(function (p) { if (p[1] > maxRate) maxRate = p[1]; });
    if (entry.h > maxRate) maxRate = entry.h;
    var logMax = Math.log10(maxRate * 1.4 + 1);
    function x(d) { return L + (R - L) * ((new Date(d)) - lo) / span; }
    function y(r) { return B - (B - T) * Math.log10(r + 1) / logMax; }

    var svg = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '">'];
    // gridlines at 1, 10, 100, 1000
    [1, 10, 100, 1000].forEach(function (g) {
      if (g > maxRate * 1.4) return;
      svg.push('<line x1="' + L + '" y1="' + y(g).toFixed(0) + '" x2="' + R + '" y2="' + y(g).toFixed(0) + '" stroke="#eef2f6"/>');
      svg.push('<text x="' + (L - 6) + '" y="' + (y(g) + 3).toFixed(0) + '" text-anchor="end" class="axis-label">' + g + '</text>');
    });
    svg.push('<line x1="' + L + '" y1="' + B + '" x2="' + R + '" y2="' + B + '" stroke="#e2e8f0"/>');
    // zero rail: models with no recorded uses, fanned beeswarm-style per month
    svg.push('<text x="' + (L - 6) + '" y="' + (RAIL + 3) + '" text-anchor="end" class="axis-label">0</text>');
    models.forEach(function (m, i) {
      if (rates[i] !== undefined) return;
      // hash-based jitter: decorrelated from release order, stable across renders
      // (index-cycled offsets make chevron lattice artifacts)
      var off = (fnv1a(m.slug) % 19) - 9;
      svg.push('<circle class="wdot" data-i="' + i + '" cx="' + x(m.date).toFixed(0) +
        '" cy="' + (RAIL + off) + '" r="2" fill="#fff" stroke="#cbd5e1" stroke-width="1"/>');
    });
    for (var yr = lo.getFullYear(); yr <= hi.getFullYear(); yr++) {
      var d = new Date(yr, 0, 1);
      if (d >= lo && d <= hi) {
        svg.push('<text x="' + x(d).toFixed(0) + '" y="' + (H - 6) + '" class="axis-label">' + yr + '</text>');
      }
    }
    // human baseline (dashed); a zero baseline is drawn on the zero rail and
    // labeled — omitting it reads as "missing", not "zero"
    if (entry.h > 0) {
      svg.push('<line x1="' + L + '" y1="' + y(entry.h).toFixed(0) + '" x2="' + R + '" y2="' + y(entry.h).toFixed(0) +
        '" stroke="#64748b" stroke-width="1.4" stroke-dasharray="5 4"/>');
      svg.push('<text x="' + R + '" y="' + (y(entry.h) - 4).toFixed(0) + '" text-anchor="end" class="axis-label">human (CMV)</text>');
    } else {
      svg.push('<line x1="' + L + '" y1="' + RAIL + '" x2="' + R + '" y2="' + RAIL +
        '" stroke="#64748b" stroke-width="1.2" stroke-dasharray="5 4" opacity="0.6"/>');
      svg.push('<text x="' + R + '" y="' + (RAIL - 5) + '" text-anchor="end" class="axis-label">human (CMV): 0 — not in the human corpus</text>');
    }
    // trailing-12-month field mean, monthly (absent models count as zero)
    var line = [];
    for (var t = new Date(lo.getFullYear(), lo.getMonth() + 6, 1); t <= hi; t = new Date(t.getFullYear(), t.getMonth() + 1, 1)) {
      var cut = new Date(t.getFullYear() - 1, t.getMonth(), t.getDate());
      var sum = 0, n = 0;
      models.forEach(function (m, i) {
        var d = new Date(m.date);
        if (d <= t && d >= cut) { n++; sum += rates[i] || 0; }
      });
      if (n >= 5) line.push(x(t).toFixed(1) + ',' + y(sum / n).toFixed(1));
    }
    if (line.length > 1) {
      svg.push('<polyline points="' + line.join(' ') + '" fill="none" stroke="#2d5a87" stroke-width="2"/>');
    }
    // dots (interactive: tooltip + click-through, bound after injection)
    models.forEach(function (m, i) {
      var r = rates[i];
      if (r === undefined) return;
      var c = m.color || '#94a3b8';
      svg.push('<circle class="wdot" data-i="' + i + '" cx="' + x(m.date).toFixed(0) + '" cy="' + y(r).toFixed(0) +
        '" r="3" fill="' + c + '" opacity="0.75"/>');
    });
    svg.push('</svg>');

    var overall = 0, recent = 0, nRecent = 0;
    var newest = new Date(Math.max.apply(null, models.map(function (m) { return +new Date(m.date); })));
    var cutoff = new Date(newest.getFullYear() - 1, newest.getMonth(), newest.getDate());
    models.forEach(function (m, i) {
      var r = rates[i] || 0;
      overall += r;
      if (new Date(m.date) >= cutoff) { nRecent++; recent += r; }
    });
    overall /= models.length;
    recent /= Math.max(nRecent, 1);

    var users = entry.m.slice().sort(function (a, b) { return b[1] - a[1]; });
    var topUsers = users.slice(0, 20);
    var maxRate = topUsers.length ? topUsers[0][1] : 1;

    view.hidden = false;
    view.dataset.word = word;
    view.innerHTML =
      '<p class="back"><a href="/experiments/vocab/" id="word-back">← Lexical Fingerprints</a></p>' +
      '<h3>Word view: <span class="w"></span></h3>' +
      '<p class="sub">Used by ' + users.length + ' of ' + models.length + ' models. ' +
      'Rate per million words by model release date (log scale). Dot = one model (lab colors where tracked; hover to identify, click for its fingerprint); ' +
      'hollow dots on the 0 rail = models with no recorded uses; ' +
      'solid line = trailing-12-month field mean; dashed = human baseline.</p>' + svg.join('') +
      '<div class="topusers"><h4>Heaviest users</h4><div class="topusers-rows"></div></div>' +
      '<div class="firstuses" hidden><h4>First recorded uses</h4><div class="fu-rows"></div>' +
      '<p class="sub">From the rare-word occurrence index; links open the full response on its theme page.</p></div>' +
      '<div class="ctx" hidden><h4>In the wild</h4><div class="ctx-cards"></div>' +
      '<p class="sub ctx-note"></p></div>';
    view.querySelector('.w').textContent = word;

    // baselines rank into the chart as gray reference bars
    var entries = topUsers.map(function (p) {
      return { name: models[p[0]].name, v: p[1], slug: models[p[0]].slug,
               title: models[p[0]].lab + ' · ' + models[p[0]].date };
    });
    [{ name: 'human writing (CMV)', v: entry.h },
     { name: 'all models — average', v: overall },
     { name: 'last 12 months — average', v: recent }].forEach(function (r) {
      entries.push({ name: r.name, v: Math.round(r.v * 100) / 100, ref: true });
    });
    entries.sort(function (a, b) { return b.v - a.v; });
    maxRate = entries[0].v || 1;

    var rowsBox = view.querySelector('.topusers-rows');
    entries.forEach(function (p) {
      var row = document.createElement('div');
      row.className = 'nb-row';
      var name;
      if (p.ref) {
        name = document.createElement('span');
        name.className = 'nb-name ref';
      } else {
        name = document.createElement('a');
        name.className = 'nb-name';
        name.href = '/experiments/vocab/m/' + p.slug + '/?w=' + encodeURIComponent(word);
        name.title = p.title + ' — see its uses';
      }
      name.textContent = p.name;
      var bar = document.createElement('div');
      bar.className = 'nb-bar' + (p.ref ? ' ref' : '');
      bar.style.width = Math.max(4, Math.round(150 * p.v / maxRate)) + 'px';
      var val = document.createElement('span');
      val.className = 'nb-val';
      val.textContent = p.v + ' /M';
      row.appendChild(name); row.appendChild(bar); row.appendChild(val);
      rowsBox.appendChild(row);
    });

    view.querySelector('#word-back').addEventListener('click', function (e) {
      e.preventDefault();
      closeWord();
    });
    bindDots(view, '.wdot', function (el) {
      var m = models[+el.dataset.i];
      var r = rates[+el.dataset.i];
      return { text: m.name + ' (' + m.lab + ') · ' + m.date.slice(0, 7) + ' · ' +
                     (r === undefined ? 'no recorded uses' : r + '/M') +
                     (r === undefined ? '' : ' — click for its uses of “' + word + '”'),
               href: '/experiments/vocab/m/' + m.slug + '/?w=' + encodeURIComponent(word) };
    });
    getCtx(word).then(function (ctx) {
      if (view.dataset.word !== word) return;   // superseded by another word
      var snips = ctx[word];
      if (!snips || !snips.length) return;
      var box = view.querySelector('.ctx');
      if (!box) return;
      var cards = view.querySelector('.ctx-cards');
      snips.forEach(function (s) { cards.appendChild(snippetCard(word, s, models)); });
      box.querySelector('.ctx-note').textContent =
        'One example per model — the heaviest users first, plus a few others for contrast (' +
        snips.length + ' of the ' + users.length + ' models that use this word). ' +
        'Click any model above for more of its own uses.';
      box.hidden = false;
    });
    Promise.all([getOcc(word), getQids()]).then(function (rs) {
      if (view.dataset.word !== word) return;   // superseded by another word
      var per = rs[0][word], qidTable = rs[1];
      if (!per) return;
      if (!view.querySelector('.firstuses')) return;
      var rows = Object.keys(per).map(function (k) {
        var m = models[+k];
        return m && { m: m, q: per[k].map(function (qi) { return qidTable[qi]; }).filter(Boolean) };
      })
        .filter(Boolean)
        .sort(function (a, b) { return a.m.date < b.m.date ? -1 : 1; })
        .slice(0, 12);
      if (!rows.length) return;
      var box = view.querySelector('.firstuses');
      box.querySelector('.fu-rows').innerHTML = rows.map(function (r) {
        return '<div class="trend-row"><span class="val" style="width:76px">' + r.m.date.slice(0, 7) +
          '</span><a href="/experiments/vocab/m/' + r.m.slug + '/?w=' + encodeURIComponent(word) +
          '" class="nb-name" style="flex:1">' + r.m.name +
          '</a><span class="val">' + useLinks(r.m.slug, r.q) + '</span></div>';
      }).join('');
      box.hidden = false;
    });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function closeWord(viaHistory) {
    var view = document.getElementById('wordview');
    var main = document.getElementById('overview-content');
    view.hidden = true;
    view.innerHTML = '';
    if (main) main.hidden = false;
    var headEl = document.getElementById('overview-head');
    if (headEl) headEl.hidden = false;
    search.value = '';
    var url = new URL(location);
    url.searchParams.delete('w');
    if (!viaHistory && url.href !== location.href) history.pushState(null, '', url);
  }
  window.addEventListener('popstate', function () {
    var w = new URL(location).searchParams.get('w');
    if (w) { search.value = w; show(w.toLowerCase(), true); }
    else closeWord(true);
  });

  function show(word, viaHistory) {
    word = word.trim().toLowerCase();
    if (!word) return;
    Promise.all([getModels(), getShard(word)]).then(function (rs) {
      var models = rs[0], shard = rs[1];
      var entry = shard[word];
      var view = document.getElementById('wordview');
      var main = document.getElementById('overview-content');
      if (!entry) {
        view.hidden = false;
        view.innerHTML = '<h3>Word view: <span class="w"></span></h3><p class="sub">Not in the watch vocabulary ' +
          '(needs to appear in ≥30 models or be a signature word).</p>';
        view.querySelector('.w').textContent = word;
        return;
      }
      if (main) main.hidden = true;
      var headEl = document.getElementById('overview-head');
      if (headEl) headEl.hidden = true;
      var url = new URL(location);
      url.searchParams.set('w', word);
      if (!viaHistory && url.href !== location.href) {
        history.pushState(null, '', url);   // Back returns to the previous view
      }
      render(word, entry, models);
    });
  }

  search.addEventListener('change', function () { show(search.value); });
  search.addEventListener('keydown', function (e) { if (e.key === 'Enter') show(search.value); });
  // browsers disagree on inputType for datalist picks, so don't trust it:
  // any input whose value exactly matches a vocab word runs the search
  // (typing straight through an exact word also shows it — desirable);
  // anything else refreshes the suggestions
  var lastShown = null;
  search.addEventListener('input', function () {
    var v = search.value.trim().toLowerCase();
    if (!v) { updateOptions(''); return; }
    getVocab().then(function (vocab) {
      if (v === search.value.trim().toLowerCase() && vocab.indexOf(v) !== -1) {
        if (v !== lastShown) { lastShown = v; show(v); }
      } else {
        updateOptions(v);
      }
    });
  });
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a.w');
    if (!a) return;
    var u = new URL(a.href, location);
    var w = u.searchParams.get('w');
    if (w && u.pathname.replace(/.*\//, '') === location.pathname.replace(/.*\//, '')) {
      e.preventDefault();
      search.value = w;
      show(w);
    }
  });

  var initial = new URL(location).searchParams.get('w');
  if (initial) { search.value = initial; show(initial, true); }
})();
