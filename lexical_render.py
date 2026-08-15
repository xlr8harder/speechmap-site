"""Lexical Fingerprints page rendering.

Renders the /experiments/ section into dist from the artifacts that
`lexical_build` produces — no analysis-file or cache access. Pages use the
site chrome from preprocess (`_page_head`/`_page_foot`); vocab-specific
styles and behavior ship as page-scoped `vocab.css` / `vocab.js`, so the
global style.css and script.js are untouched.

Standalone:  uv run python lexical_render.py
"""
from __future__ import annotations

import gzip
import html
import json
import math
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import SITE_BASE_URL, WHY_WE_TEST_HTML, _page_head, _page_foot  # noqa: E402

SITE = Path(__file__).resolve().parent
ART = SITE / ".cache" / "lexical-artifacts"
ASSET_V = 17

VOCAB_BASE = "/experiments/vocab"


def esc(s):
    return html.escape(str(s), quote=True)


def _read(name):
    with gzip.open(ART / name, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def wlink(word):
    return f'<a class="w" href="{VOCAB_BASE}/?w={esc(word)}">{esc(word)}</a>'


def highlight(word, text):
    import re as _re
    rx = _re.compile(r"\b(" + _re.escape(word) + r")\b", _re.IGNORECASE)
    return rx.sub(r"<mark>\1</mark>", esc(text))


def model_url(m):
    return f"{VOCAB_BASE}/m/{m['slug']}/"


def spark(points, field_points=None, w=150, h=30, band=None):
    def poly(pts):
        return " ".join(f"{2 + x * (w - 8):.1f},{h - 4 - y * (h - 9):.1f}" for x, y in pts)
    parts = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    if band:
        bx = 2 + band[0] * (w - 8)
        bw = (band[1] - band[0]) * (w - 8)
        parts.append(f'<rect x="{bx:.1f}" y="1" width="{bw:.1f}" height="{h - 2}" '
                     f'fill="#dbeafe" opacity="0.55"/>')
    if field_points:
        parts.append(f'<polyline points="{poly(field_points)}" fill="none" stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="4 3"/>')
    parts.append(f'<polyline points="{poly(points)}" fill="none" stroke="#2d5a87" stroke-width="1.6"/>')
    if points:
        x, y = points[-1]
        parts.append(f'<circle cx="{2 + x * (w - 8):.1f}" cy="{h - 4 - y * (h - 9):.1f}" r="2.4" fill="#2d5a87"/>')
    parts.append("</svg>")
    return "".join(parts)


def norm_series(series):
    lo, hi = min(series), max(series)
    n = len(series)
    return [(k / (n - 1), (v - lo) / (hi - lo) if hi > lo else 0.5)
            for k, v in enumerate(series)]


def trend_row(item, val_text, band=None):
    return (f'<div class="trend-row">{wlink(item["w"])}'
            f'{spark(norm_series(item["s"]), w=90, h=22, band=band)}'
            f'<span class="val">{val_text}</span></div>')


def ratio_bar(ratio, max_ratio=70.0):
    width = max(6, int(150 * math.log2(max(ratio, 1.01)) / math.log2(max(max_ratio, 2))))
    return (f'<div class="rbar-wrap"><div class="rbar" style="width:{width}px"></div>'
            f'<span class="rbar-val">{ratio:.1f}&times;</span></div>')


def page(title, canonical, body, description=None):
    head = _page_head(title, canonical, depth=0, active_tab="experiments", description=description)
    return (head
            + f'<link rel="stylesheet" href="{VOCAB_BASE}/vocab.css?v={ASSET_V}">\n'
            + '<div class="vocab-page">\n' + body + '\n</div>'
            + f'\n<script src="{VOCAB_BASE}/vocab.js?v={ASSET_V}" defer></script>'
            + _page_foot(depth=0))


BANNER = ('<div class="vocab-banner">Experiments preview — a SpeechMap side project. '
          'Data and layout may change.</div>')


def load_ctx_snip(dist_data, word, model_i, _cache={}):
    """Baked example quote for (word, model) from the ctx shards, if stored."""
    b = _fnv1a(word) % 32
    if b not in _cache:
        p = dist_data / "ctx" / f"{b}.json"
        _cache[b] = json.load(p.open()) if p.exists() else {}
    for s in _cache[b].get(word, ()):
        if s["m"] == model_i:
            return s
    return None


def _fnv1a(s):
    h = 0x811C9DC5
    for byte in s.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def render_all(dist_root=None):
    dist = Path(dist_root) if dist_root else SITE / "dist"
    out = dist / "experiments"
    vocab_out = out / "vocab"
    for sub in ("m", "lab"):
        (vocab_out / sub).mkdir(parents=True, exist_ok=True)

    models = _read("models.json.gz")
    signatures = _read("signatures.json.gz")
    classes = _read("classes.json.gz")
    similarity = _read("similarity.json.gz")
    panels = _read("panels.json.gz")
    lineages = _read("lineages.json.gz")
    proper = set(classes["proper"])
    person = set(classes["person"])
    dist_data = ART / "dist-data"

    lab_names = {}
    lab_colors = {}
    for m in models:
        lab_names.setdefault(m["lab"], m["lab"])
        if m.get("color"):
            lab_colors[m["lab"]] = m["color"]
    try:
        from preprocess import load_lab_metadata
        lab_meta = load_lab_metadata(str(SITE / "lab_metadata.jsonl"))
        for lab, rec in lab_meta.items():
            lab_names[lab] = rec.get("full_name", lab)
    except Exception:
        pass

    total_complete = sum(m["complete"] for m in models)

    # ---- experiments index
    body = f"""{BANNER}
<h1>Experiments</h1>
<div class="page-intro">
  <p class="lab-lede">Side studies built on the SpeechMap corpus — less polished than the main site, more curious.
  Each experiment reuses the same {total_complete:,} judged responses that power the rest of SpeechMap,
  so everything here is comparable with the main results. For methodology and broader context, start with the
  <a href="/">project overview</a>.</p>
  {WHY_WE_TEST_HTML}
</div>
<div class="vx-card-grid">
  <a class="vx-card" href="{VOCAB_BASE}/">
    <span class="vx-status">New</span>
    <h4>Lexical Fingerprints</h4>
    <p>The words each model overuses, which models sound alike, how lineages drift over time,
    and which stock character names give a model away.</p>
  </a>
</div>
"""
    (out / "index.html").write_text(page(
        "Experiments | SpeechMap.AI", f"{SITE_BASE_URL}/experiments/", body,
        "Side studies built on the SpeechMap corpus."))

    # ---- overview
    # shade the trailing-12-month window on panel sparklines: the panel figures
    # compare that window against the year before, while the sparkline shows
    # the full rolling history — without the band the two visibly disagree
    from datetime import date as _date, timedelta as _td
    sdates = [_date.fromisoformat(s) for s in panels["spark_dates"]]
    sd0 = sdates[0]
    newest = _date.fromisoformat(panels["windows"]["newest"])
    right = max(sdates[-1], newest)
    span_days = max((right - sd0).days, 1)
    # x by DATE, not by sample index — the final sample sits at the newest
    # release, not on the quarterly grid
    spark_xs = [(d - sd0).days / span_days for d in sdates]
    recent_band = (max(0.0, ((newest - _td(days=365)) - sd0).days / span_days), 1.0)

    def panel_points(series):
        lo, hi = min(series), max(series)
        xs = spark_xs if len(series) == len(spark_xs) else [
            k / max(len(series) - 1, 1) for k in range(len(series))]
        return [(x, (v - lo) / (hi - lo) if hi > lo else 0.5)
                for x, v in zip(xs, series)]

    # one continuous stripe through the whole aligned sparkline column —
    # per-row band rects read as clutter now that rows are column-aligned
    stripe_left = 2 + recent_band[0] * 82        # svg-local px (w=90, pad 2/6)
    stripe_w = 84 - stripe_left

    def banded(rows_html):
        return (f'<div class="rows-band">'
                f'<div class="band-stripe" style="width:{stripe_w:.1f}px"></div>'
                f'{rows_html}</div>')

    def panelrows(items, fmt, drop_possessives=False):
        if drop_possessives:
            items = [it for it in items if not it["w"].endswith("'s")]
        rows = "".join(
            f'<div class="trend-row">{wlink(it["w"])}'
            f'{spark(panel_points(it["s"]), w=90, h=22)}'
            f'<span class="val">{fmt(it)}</span></div>'
            for it in items)
        return banded(rows)

    # similarity is surfaced through the model pages (closest voices, closest
    # outside the lab, head-to-head); a 2D map projection was tried and dropped —
    # measured structure was weak either way (timeline-dominated unadjusted,
    # idiosyncratic scatter era-adjusted). map_coords stay in the artifact.
    drift_cards = []
    for lab in sorted(lineages):
        tables = lineages[lab]
        best = None
        for t in tables:
            if t["rows"] and (best is None or t["n_models"] > best[0]["n_models"]):
                best = (t, t["rows"][0])
        if not best:
            continue
        t, row = best
        vals, field = row["vals"], row["field"]
        hi = max(vals + field) or 1
        d0 = t["dates"][0]
        span = max((_days(t["dates"][-1]) - _days(d0)), 1)
        pts = [((_days(d) - _days(d0)) / span, v / hi) for d, v in zip(t["dates"], vals)]
        fpts = [((_days(d) - _days(d0)) / span, v / hi) for d, v in zip(t["dates"], field)]
        drift_cards.append(
            f'<a class="drift-card" href="{VOCAB_BASE}/lab/{lab}/">'
            f'<h4><i style="background:{lab_colors.get(lab, "#94a3b8")}"></i>{esc(lab_names.get(lab, lab))}</h4>'
            f'<div class="drift-word">{esc(row["w"])}</div>'
            f'{spark(pts, fpts, w=170, h=40)}'
            f'<div class="drift-val">{vals[0]:.0f} → {vals[-1]:.0f} /M across {len(vals)} versions</div>'
            f'<div class="drift-meta">{esc(t["lineage"])} lineage · {t["n_models"]} models · view drift →</div></a>')

    table_rows = []
    for m in sorted(models, key=lambda m: m["date"], reverse=True):
        sig = [r for r in signatures.get(m["id"], ())
               if r["broad"] and not r["prompty"] and r["w"] not in proper][:3]
        # a signature chip in a model's row goes to THAT model's word check,
        # not the field-wide view
        chips = " ".join(
            f'<a class="w" href="{model_url(m)}?w={esc(r["w"])}">{esc(r["w"])}</a>'
            for r in sig)
        table_rows.append(
            f'<tr><td><a href="{model_url(m)}">{esc(m["name"])}</a></td>'
            f'<td>{esc(lab_names.get(m["lab"], m["lab"]))}</td><td>{m["date"]}</td>'
            f'<td class="chips-cell">{chips}</td></tr>')

    body = f"""{BANNER}
<div id="overview-head">
<p class="back"><a href="/experiments/">← Experiments</a></p>
<h1>Lexical Fingerprints</h1>
<div class="page-intro">
  <p class="lab-lede">What {len(models)} models' word choices reveal — computed from {total_complete:,}
  COMPLETE answers to the same 2,120 questions. Every rate is the mean within-response rate per million words
  in COMPLETE answers, so refusal language never pollutes the comparison. One caveat up front: SpeechMap's
  prompts are deliberately narrow — sensitive and controversial questions — so this is how models write
  <em>here</em>, not a portrait of their language in general. What travels is the comparison: every model
  answers the same questions, so the differences between them are real even where the words themselves are
  shaped by our setting. For methodology and broader context, start with the
  <a href="/">project overview</a>.</p>
  {WHY_WE_TEST_HTML}
</div>
</div>
<div class="wordbox"><input id="wordsearch" list="vocablist" placeholder="Look up any word… (e.g. ordinarily, delve, henderson)">
<datalist id="vocablist"></datalist></div>
<div id="wordview" hidden></div>
<div id="overview-content">
<p class="vx-muted small">Sparklines show the rolling 12-month average from early 2024 through the newest
release; the shaded band marks that release's trailing 12 months — the figures compare that window against
the year before it.</p>
<div class="panel-grid">
<div class="panel"><h4>Rising across models <span class="h4-note">(fastest relative growth, year over year)</span></h4>{panelrows(panels["rising"], lambda i: f"{i['r0']:.0f} → {i['r1']:.0f} /M")}</div>
<div class="panel"><h4>Falling across models <span class="h4-note">(fastest relative decline)</span></h4>{panelrows(panels["falling"], lambda i: f"{i['r0']:.0f} → {i['r1']:.0f} /M")}</div>
</div>
<div class="panel"><h4>Words of the current era <span class="h4-note">(weighted by rate: the big words of today's answers, not the fastest movers — see Rising for those)</span></h4>
<div class="era-grid">{panelrows(panels["era"][0::2], lambda i: f"{i['r1']:.0f}/M · {i['x']}&times;")}{panelrows(panels["era"][1::2], lambda i: f"{i['r1']:.0f}/M · {i['x']}&times;")}</div></div>
<div class="panel-grid">
<div class="panel"><h4>Names on the rise <span class="h4-note">(stock characters)</span></h4>{panelrows(panels["names_up"], lambda i: f"{i['r0']:.1f} → {i['r1']:.1f} /M", drop_possessives=True)}</div>
<div class="panel"><h4>Names fading</h4>{panelrows(panels["names_down"], lambda i: f"{i['r0']:.1f} → {i['r1']:.1f} /M", drop_possessives=True)}</div>
</div>
<h2>Lab drift over time</h2>
<p>Click through to see what each lab is up to. Each card previews the lab's most dramatic word shift —
solid line is the lineage, dashed is the all-model average.</p>
<div class="drift-grid">{"".join(drift_cards)}</div>
<h2>All models</h2>
<p>Each fingerprint page shows a model's signature words, who it sounds like — including its closest
voices <em>outside its own lab</em>, where fine-tune lineages and convergence show up — and a head-to-head
with its nearest neighbor.</p>
<table class="mtable"><thead><tr><th>Model</th><th>Lab</th><th>Released</th><th>Signature words</th></tr></thead>
<tbody>{"".join(table_rows)}</tbody></table>
</div>
"""
    (vocab_out / "index.html").write_text(page(
        "Lexical Fingerprints | SpeechMap.AI", f"{SITE_BASE_URL}{VOCAB_BASE}/", body,
        "The words each model overuses, which models sound alike, and how model language drifts over time."))

    # ---- model pages
    by_index = {i: m for i, m in enumerate(models)}
    for i, m in enumerate(models):
        rows = signatures.get(m["id"], [])
        sig = [r for r in rows if r["broad"] and not r["prompty"] and r["w"] not in proper][:20]
        name_rows = sorted((r for r in rows if r["w"] in person),
                           key=lambda r: -r["ratio"])[:8]
        sig_html = ""
        for r in sig:
            sig_html += (
                f'<tr><td>{wlink(r["w"])}</td><td class="num">{r["rate"]:.1f}</td>'
                f'<td class="num">{r["base"]:.1f}</td><td>{ratio_bar(r["ratio"])}</td>'
                f'<td class="num">{r["docs"]}</td><td class="num">{r["domains"]}</td></tr>')
        names_html = "".join(
            f'<span class="chip">{wlink(r["w"])} {r["ratio"]:.0f}&times;</span>'
            for r in name_rows) or '<span class="vx-muted">No distinctive stock names detected.</span>'

        nb = similarity["neighbors"][str(i)]
        s_lo, s_hi = nb["range"]
        s_span = (s_hi - s_lo) or 1

        def nb_rows(pairs):
            outp = []
            for j, s in pairs:
                other = by_index[j]
                width = max(8, int(130 * (s - s_lo) / s_span))
                outp.append(
                    f'<div class="nb-row"><a class="nb-name" href="{model_url(other)}?w=" '
                    f'title="{esc(lab_names.get(other["lab"], other["lab"]))}">{esc(other["name"])}</a>'
                    f'<div class="nb-bar" style="width:{width}px"></div><span class="nb-val">{s:.2f}</span></div>')
            return "".join(outp)

        h2h = similarity["h2h"].get(str(i))
        h2h_html = ""
        if h2h:
            other = by_index[h2h["vs"]]
            left = "".join(f'<div class="trend-row">{wlink(w)}<span class="val">{x}&times; more</span></div>'
                           for w, x in h2h["left"])
            right = "".join(f'<div class="trend-row">{wlink(w)}<span class="val">{x}&times; more</span></div>'
                            for w, x in h2h["right"])
            h2h_html = f"""
<div class="panel"><h4>Head to head with its closest voice, <a href="{model_url(other)}">{esc(other["name"])}</a></h4>
<div class="panel-grid" style="margin:8px 0 0;">
<div><h5 class="lean-h">This model leans on</h5>{left}</div>
<div><h5 class="lean-h">{esc(other["name"])} leans on</h5>{right}</div>
</div>
<p class="vx-muted small">&times; = rate multiple between the two models. Similarity is measured on deviations
from the average model, so it reflects shared quirks, not shared English.</p></div>"""

        pred = similarity["predecessor"].get(str(i))
        pred_html = ""
        if pred:
            fam, sim, pct = pred
            pred_html = (f'<div><b>{sim:.2f}</b><span>similarity to its predecessor ({esc(fam)}) — '
                         f'a bigger voice change than {pct}% of version steps</span></div>')

        lab_full = lab_names.get(m["lab"], m["lab"])
        body = f"""{BANNER}
<p class="back"><a href="{VOCAB_BASE}/">← Lexical Fingerprints</a></p>
<h1>{esc(m["name"])} <span class="h1-sub">lexical fingerprint</span></h1>
<div class="page-intro">
  <p class="lab-lede">The lexical fingerprint of <b>{esc(m["id"])}</b> ({esc(lab_full)}, released {m["date"]}):
  the words it leans on far more than other models answering the exact same questions, computed from its
  {m["complete"]:,} COMPLETE answers. Part of the <a href="{VOCAB_BASE}/">Lexical Fingerprints</a> experiment;
  see also this model's <a href="/models/{m["slug"]}/">main SpeechMap results</a>.</p>
  {WHY_WE_TEST_HTML}
</div>
<div class="wordbox"><input id="modelwordsearch" data-mid="{esc(m["id"])}" data-site="{esc(m["slug"])}"
placeholder="Check this model's usage of any word…"></div>
<div id="modelwordview" hidden></div>
<div class="vx-stats">
  <div><b>{m["complete"]:,}</b><span>COMPLETE responses analyzed</span></div>
  <div><b>{len(models) - 1}</b><span>comparison models</span></div>
  <div><b>{len(rows)}</b><span>signature candidates screened</span></div>
</div>
<h2>Signature words</h2>
<table class="mtable"><thead><tr><th>Word</th><th class="num">Rate /M</th><th class="num">Others /M</th>
<th>&times; over matched baseline</th><th class="num">Responses</th><th class="num">Domains</th></tr></thead>
<tbody>{sig_html}</tbody></table>
<p class="vx-muted small">A signature word is one this model uses far more than other models answering the
exact same questions — ranked purely by that contrast, so field-wide habits can never qualify. Words must
appear in ≥15 answers across ≥6 topic domains, and words echoed from the questions are excluded; names
appear separately below.
Rates are mean within-response rates over COMPLETE answers. Bars are log-scaled.
Click any word to see this model using it.</p>
<h2>Stock character names</h2>
<div class="chips">{names_html}</div>
<p class="vx-muted small">&times; = rate multiple over other models' COMPLETE answers to the same questions.</p>
<h2>Sounds like</h2>
<div class="vx-stats">
  <div><b>{nb["distinct_pct"]}%</b><span>of models sound less distinctive than this one</span></div>
  {pred_html}
</div>
<div class="panel-grid">
<div class="panel"><h4>Closest voices</h4>{nb_rows(nb["top"])}</div>
<div class="panel"><h4>Closest outside {esc(lab_full)}</h4>{nb_rows(nb["outside"])}</div>
</div>
{h2h_html}
"""
        pdir = vocab_out / "m" / m["slug"]
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "index.html").write_text(page(
            f"{m['name']} — Lexical Fingerprint | SpeechMap.AI",
            f"{SITE_BASE_URL}{VOCAB_BASE}/m/{m['slug']}/", body,
            f"Signature words and lexical similarity for {m['name']}."))

    # ---- lab drift pages
    def version_label(fam, lineage):
        # "grok-4.20-experimental-beta" under lineage "grok" -> "4.20 exp beta"
        label = fam
        if label.startswith(lineage + "-"):
            label = label[len(lineage) + 1:]
        label = label.replace("experimental", "exp").replace("preview", "pre")
        label = label.replace("-", " ")
        return label if len(label) <= 14 else label[:13] + "…"

    for lab, tables in lineages.items():
        sections = []
        for t in sorted(tables, key=lambda t: t["lineage"]):
            fam_headers = "".join(
                f'<th class="num" title="{esc(fam)} · {d}">'
                f'{esc(version_label(fam, t["lineage"]))}</th>'
                for fam, d in zip(t["families"], t["dates"]))

            def lineage_row(row):
                vals, field = row["vals"], row["field"]
                hi = max(vals + field) or 1
                d0 = t["dates"][0]
                span = max((_days(t["dates"][-1]) - _days(d0)), 1)
                pts = [((_days(d) - _days(d0)) / span, v / hi) for d, v in zip(t["dates"], vals)]
                fpts = [((_days(d) - _days(d0)) / span, v / hi) for d, v in zip(t["dates"], field)]
                cells = "".join(f'<td class="num">{v:.0f}</td>' for v in vals)
                return f'<tr><td>{wlink(row["w"])}</td>{cells}<td>{spark(pts, fpts)}</td></tr>'

            def direction(row):
                if "up" in row:
                    return row["up"]
                v = row["vals"]
                return (v[-1] + v[-2]) / 2 > (v[0] + v[1]) / 2

            rising_rows = [lineage_row(r) for r in t["rows"] if direction(r)]
            falling_rows = [lineage_row(r) for r in t["rows"] if not direction(r)]

            def sub_table(title, rows_list):
                if not rows_list:
                    return ""
                return (f'<h3 class="lin-sub">{title}</h3>'
                        f'<table class="mtable"><thead><tr><th>Word</th>{fam_headers}'
                        f'<th>Trend vs field</th></tr></thead>'
                        f'<tbody>{"".join(rows_list)}</tbody></table>')

            sections.append(f"""
<h2>{esc(t["lineage"])} lineage <span class="h1-sub">{len(t["families"])} versions · {t["n_models"]} models</span></h2>
<p class="legend-line-p"><span class="sw solid"></span> this lineage, by version
&nbsp; <span class="sw dash"></span> trailing-12-month all-model mean
&nbsp;·&nbsp; ranked by the shift from its first two versions to its last two, weighted by usage</p>
{sub_table("Top rising", rising_rows)}
{sub_table("Top falling", falling_rows)}""")
        lab_full = lab_names.get(lab, lab)
        body = f"""{BANNER}
<p class="back"><a href="{VOCAB_BASE}/">← Lexical Fingerprints</a></p>
<h1>{esc(lab_full)} <span class="h1-sub">language drift</span></h1>
<div class="page-intro">
  <p class="lab-lede">How {esc(lab_full)}'s vocabulary has shifted, version to version. One point per version
  family (near-simultaneous variants averaged, including reasoning and non-reasoning siblings — equal weight
  per model), plotted at the family's median release date. No smoothing — the dashed line is the
  trailing-12-month mean across all {len(models)} models. Single-version spikes and prompt-echo words are
  excluded; the "trend vs field" column shows whether the lineage moves with the rest of the field or against
  it. Part of the <a href="{VOCAB_BASE}/">Lexical Fingerprints</a> experiment.</p>
  {WHY_WE_TEST_HTML}
</div>
{"".join(sections)}
"""
        pdir = vocab_out / "lab" / lab
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "index.html").write_text(page(
            f"{lab_full} Language Drift | SpeechMap.AI",
            f"{SITE_BASE_URL}{VOCAB_BASE}/lab/{lab}/", body,
            f"How {lab_full}'s vocabulary has shifted version to version."))

    # ---- client data + assets
    data_out = dist / "data" / "vocab"
    if data_out.exists():
        shutil.rmtree(data_out)
    shutil.copytree(dist_data, data_out)
    shutil.copy(SITE / "vocab.css", vocab_out / "vocab.css")
    shutil.copy(SITE / "vocab.js", vocab_out / "vocab.js")
    n_pages = 2 + len(models) + len(lineages)
    print(f"vocab pages rendered: {n_pages}; data copied to {data_out}")
    return n_pages


def _days(iso):
    y, mo, d = (int(x) for x in iso.split("-"))
    return y * 372 + mo * 31 + d


if __name__ == "__main__":
    render_all()
