"""Lexical Fingerprints analytics: caches in, artifacts out.

Reads the per-model caches written by `lexical.LexicalCollector` — never the
raw analysis files — and computes everything the vocab section renders:

- signature words per model (question-matched leave-one-out estimator,
  COMPLETE-only mean within-response rates — the single estimator everywhere)
- proper-noun / person-name classification from merged capitalization stats
- centered-cosine similarity, 2D map coordinates, neighbors, predecessor steps
- field trends (two 12-month windows), era words, trending names
- lineage drift series per lab
- data shards for the client (model-major, word-major, snippet contexts)

Memory: per-question rate sums are only accumulated for screened candidate
words (union ~tens of thousands), never the full vocabulary, so peak RSS stays
in the low hundreds of MB. Two passes over the caches, zero raw-file reads.

Standalone:  uv run python lexical_build.py
"""
from __future__ import annotations

import gzip
import json
import math
import os
import random
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

import numpy as np

from lexical import load_cache

SMOOTH_PM = 0.25
CANDIDATE_BROAD = 400
CANDIDATE_UNRESTRICTED = 100
BROAD_DOCS = 15
BROAD_DOMAINS = 6
CAP_FRAC = 0.75
CAP_MIN = 20
SIM_VOCAB_SIZE = 2000
SNIPPET_PRIORITY = 6
SNIPPET_OTHER = 4
WATCH_PREVALENCE = 30
SHARD_BUCKETS = 32
# words used by at most this many models get a full occurrence index
# (word -> model -> question ids), so every dot on a rare word's chart can
# link to the actual responses on the theme pages. ~7 MB gz at 90.
OCCURRENCE_PREVALENCE = 90
OCCURRENCE_QIDS = 5
POINTER_QIDS = 3          # per (model, word): up to 3 example locations,
POINTER_CANDIDATES = 12   # picked for variation-format diversity first
                          # (v1 essay / v2 direct / v3 satire / v4 speech),
                          # then theme diversity


def select_diverse_qids(cands, k=POINTER_QIDS):
    """First use first; then prefer unseen variation formats, then unseen themes."""
    sel = []
    digits = set()
    themes = set()
    for qid in cands:
        if len(sel) >= k:
            break
        d = qid[-1] if qid[-1:].isdigit() else ""
        t = qid[:-1] if d else qid
        if not sel or d not in digits or (t not in themes and len(digits) >= min(3, len(cands))):
            sel.append(qid)
            digits.add(d)
            themes.add(t)
    for qid in cands:
        if len(sel) >= k:
            break
        if qid not in sel:
            sel.append(qid)
    return sel

ARTIFACT_DIR = os.path.join(".cache", "lexical-artifacts")
RATES_DIR = os.path.join(".cache", "lexical-rates")

LINEAGE_OVERRIDES = {"gpt-turbo": "gpt", "gpt-chat": "gpt"}
VERSION_TOKEN = re.compile(r"v?\d+(\.\d+)*[a-z]?")
NOISE_TOKENS = {"beta", "experimental", "preview", "exp", "it"}

PERSON_NAMES = set("""
aaron adam alan albert alex alice amanda amy andrew angela anna arthur ashley barbara barry benjamin beth betty bill bob brandon brenda brian brock bruce carl carlos carol carter chen chloe chris christine claire clara dale dan dana daniel dave david dawn dean debra dennis derek diana diane donald donna dorothy doug douglas earl ed eddie edward elena eleanor elias elijah ella ellen emily emma eric erin ethan eugene eva evelyn frank fred gary george gerald gloria grace greg gregory hannah harold harry heather helen henderson henry howard ian irene isaac jack jacob james jane janet jason jean jeff jeffrey jennifer jeremy jerry jessica jill jim joan joe john johnson jonathan jordan jose joseph joshua joyce juan judith julia julian julie justin karen katherine kathleen keith kelly kenneth kevin kim kyle larry laura lauren lawrence lena leo leon linda lisa liz lloyd lois louis lucas lucy luke lynn maggie marcus margaret maria marie marilyn mark marla marsh martha martin marvin mary mateo matthew maya megan melissa michael michelle miguel mike miller miriam molly monica morgan nancy naomi natalie nathan nicholas nicole noah nora norman oliver olivia omar oscar pamela patricia patrick paul paula peter philip phillip phyllis priya rachel ralph randy raymond rebecca reginald richard rita robert roberto roger ronald rosa rose roy russell ruth ryan sam samantha samuel sandra sara sarah scott sean sharon shirley simon sofia sophia stanley stephanie stephen steve steven susan tanya ted teresa terry theodore thomas timothy tina todd tom tony tracy travis trent tyler valerie vanessa victor victoria vincent virginia walter wayne wendy william willie zoe
aris pritchard voss vance pemberton mallory pendelton higgins hayes brooks reyes okafor ramirez nguyen patel park singh garcia lopez martinez rodriguez hernandez gonzalez perez sanchez torres rivera flores diaz cruz ortiz gomez marsh whitfield calloway ashford thorne blackwood sterling elara zephyr
""".split())


def fnv1a(s):
    h = 0x811C9DC5
    for byte in s.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def log2_ratio(left, right):
    return math.log2((left + SMOOTH_PM) / (right + SMOOTH_PM))


def lineage_of(family):
    toks = []
    for tok in family.split("-"):
        if VERSION_TOKEN.fullmatch(tok) or tok in NOISE_TOKENS:
            continue
        toks.append(re.sub(r"\d+(\.\d+)*$", "", tok) or tok)
    key = "-".join(toks) if toks else family
    return LINEAGE_OVERRIDES.get(key, key)


def _write_json_gz(path, obj):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as handle:
        json.dump(obj, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def load_site_metadata(site_root="."):
    model_meta = {}
    with open(os.path.join(site_root, "model_metadata.json"), encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rec = json.loads(line)
                model_meta[rec["model_identifier"]] = rec
    lab_meta = {}
    with open(os.path.join(site_root, "lab_metadata.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rec = json.loads(line)
                lab_meta[rec["lab"]] = rec
    return model_meta, lab_meta


def load_human_rates(site_root="."):
    path = os.path.join(site_root, "assets", "cmv-rates.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {w: v[0] for w, v in raw.items()}, {w: v[1] for w, v in raw.items()}


def load_prompt_rates(questions_path):
    counts = Counter()
    total = 0
    tok = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ']*")
    with open(questions_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rec = json.loads(line)
            words = [t.lower() for t in tok.findall(str(rec.get("question") or ""))]
            counts.update(words)
            total += len(words)
    return {w: c * 1e6 / total for w, c in counts.items()} if total else {}


def build(site_root=None, data_root=None):
    # anchored to this module's directory, never the cwd — the site build
    # chdirs into dist/ before this runs
    site_root = site_root or os.path.dirname(os.path.abspath(__file__))
    data_root = data_root or os.environ.get(
        "SPEECHMAP_DATA_ROOT", os.path.join("..", "speechmap-data"))
    cache_dir = os.path.join(site_root, ".cache", "lexical")
    rates_dir = os.path.join(site_root, RATES_DIR)
    art_dir = os.path.join(site_root, ARTIFACT_DIR)
    os.makedirs(rates_dir, exist_ok=True)
    os.makedirs(art_dir, exist_ok=True)

    model_meta, lab_meta = load_site_metadata(site_root)
    cmv, brown = load_human_rates(site_root)
    prompt = load_prompt_rates(os.path.join(data_root, "questions", "us_hard.jsonl"))

    cache_files = sorted(
        f for f in os.listdir(cache_dir) if f.endswith(".json.gz"))

    # ---- pass A: per-model mean rates, global aggregates, cap stats
    models = []                       # ordered by release date after this pass
    prevalence = Counter()
    mean_rate_sums = Counter()        # sum over models of mean within-response rate
    question_complete = Counter()     # qid -> COMPLETE models
    caps = defaultdict(lambda: [0, 0])
    per_model = {}                    # model -> summary for later passes

    for fname in cache_files:
        cache = load_cache(os.path.join(cache_dir, fname))
        mid = cache["model"]
        meta = model_meta.get(mid)
        rel = meta.get("release_date") if meta else None
        try:
            rel = date.fromisoformat(rel) if rel else None
        except ValueError:
            rel = None
        if rel is None:
            continue
        questions = cache["questions"]
        if not questions:
            continue
        n_q = len(questions)
        rate_sums = defaultdict(float)
        doc_counts = Counter()
        for toks, counts in questions.values():
            scale = 1e6 / toks
            for w, c in counts.items():
                rate_sums[w] += c * scale
                doc_counts[w] += 1
        rates = {w: s / n_q for w, s in rate_sums.items()}
        slug = re.sub(r"-+", "-", re.sub(r"[^\w-]+", "-", mid.lower().strip()))
        _write_json_gz(os.path.join(rates_dir, f"{slug}.json.gz"),
                       {w: round(r, 4) for w, r in rates.items()})
        for w, c in cache["caps"].items():
            caps[w][0] += c[0]
            caps[w][1] += c[1]
        prevalence.update(rates.keys())
        for w, r in rates.items():
            mean_rate_sums[w] += r
        for qid in questions:
            question_complete[qid] += 1
        per_model[mid] = {
            "slug": slug, "cache_file": fname, "date": rel,
            "lab": meta.get("creator", ""), "family": meta.get("model_family", ""),
            "lineage": lineage_of(meta.get("model_family", "")),
            "name": mid.split("/")[-1], "n_complete": n_q,
            "doc_counts": doc_counts,
            "domain_counts": cache["domain_counts"],
            "rates_hot": {w: r for w, r in rates.items() if doc_counts[w] >= 5},
        }
        models.append(mid)
    models.sort(key=lambda mid: (per_model[mid]["date"], mid))
    n_models = len(models)
    print(f"pass A: {n_models} models aggregated")

    proper = {w for w, c in caps.items()
              if sum(c) >= CAP_MIN and c[0] / sum(c) >= CAP_FRAC}
    person = {w for w in proper
              if w in PERSON_NAMES or w.rstrip("'s") in PERSON_NAMES}

    # ---- candidate screening (cheap leave-one-out on mean rates)
    candidates = {}
    for mid in models:
        pm = per_model[mid]
        rows = []
        for w, own in pm["rates_hot"].items():
            other = (mean_rate_sums[w] - own) / max(n_models - 1, 1)
            # signature ranking is models-only: the page's claim is "vs other
            # models on the same questions"; a human floor here suppresses
            # sounds-more-human-than-peers signatures (glad, likewise…)
            score = log2_ratio(own, other)
            broad = (pm["doc_counts"][w] >= BROAD_DOCS
                     and pm["domain_counts"].get(w, 0) >= BROAD_DOMAINS)
            non_prompt = prompt.get(w, 0.0) <= max(10.0, own * 0.25)
            rows.append((score, pm["doc_counts"][w], w, broad, non_prompt))
        rows.sort(reverse=True)
        selected = set()
        taken_broad = taken_any = 0
        for score, _dc, w, broad, non_prompt in rows:
            if broad and non_prompt and taken_broad < CANDIDATE_BROAD:
                selected.add(w)
                taken_broad += 1
        for score, _dc, w, broad, non_prompt in rows:
            if w in selected:
                continue
            selected.add(w)
            taken_any += 1
            if taken_any >= CANDIDATE_UNRESTRICTED:
                break
        candidates[mid] = selected
    candidate_union = set().union(*candidates.values()) if candidates else set()
    print(f"screened candidates: union {len(candidate_union)}")

    watch = {w for w, c in prevalence.items() if c >= WATCH_PREVALENCE} | candidate_union

    # ---- pass B: per-question sums for candidates; snippet selection;
    #      word shards with first-use pointers; trend windows
    question_sums = defaultdict(dict)      # qid -> {word: sum of rates}
    own_adjust = {mid: defaultdict(float) for mid in models}
    heavy = {}
    for w in watch:
        ranked = sorted(
            ((per_model[mid]["rates_hot"].get(w, 0.0), mid) for mid in models
             if w in per_model[mid]["rates_hot"]), reverse=True)
        heavy[w] = {mid for _r, mid in ranked[:SNIPPET_PRIORITY]}
    snip_pri = defaultdict(list)
    snip_oth = defaultdict(list)
    rare = {w for w in watch if prevalence[w] <= OCCURRENCE_PREVALENCE}
    occurrences = defaultdict(dict)     # word -> model -> [qid, ...]
    order = models[:]
    random.Random(42).shuffle(order)
    cache_order = {mid: i for i, mid in enumerate(order)}

    # numeric qid table keeps every pointer small (index into sorted list)
    all_qids = sorted(question_complete)
    qid_index = {q: i for i, q in enumerate(all_qids)}
    model_index = {mid: i for i, mid in enumerate(models)}
    shard_rows = defaultdict(list)      # word -> [model_i, rate, first_use_qid_i]
    newest = max(per_model[mid]["date"] for mid in models)
    recent_lo = newest - timedelta(days=365)
    prev_lo = newest - timedelta(days=730)
    n_recent = sum(1 for mid in models if per_model[mid]["date"] >= recent_lo)
    n_prev = sum(1 for mid in models if prev_lo <= per_model[mid]["date"] < recent_lo)
    win = defaultdict(lambda: [0.0, 0.0])   # word -> [prev_sum, recent_sum]

    for mid in sorted(models, key=lambda m: cache_order[m]):
        pm = per_model[mid]
        cache = load_cache(os.path.join(cache_dir, pm["cache_file"]))
        cand = candidates[mid]
        n_q = len(cache["questions"])
        full_rate_sums = defaultdict(float)
        word_qids = defaultdict(list)
        word_digits = defaultdict(set)   # variation formats already collected
        for qid, (toks, counts) in cache["questions"].items():
            denom = question_complete[qid] - 1
            scale = 1e6 / toks
            qs = question_sums[qid]
            for w in counts.keys() & candidate_union:
                rate = counts[w] * scale
                qs[w] = qs.get(w, 0.0) + rate
                if denom > 0 and w in cand:
                    own_adjust[mid][w] += rate / denom
            q_digit = qid[-1] if qid[-1:].isdigit() else ""
            for w in counts.keys() & watch:
                full_rate_sums[w] += counts[w] * scale
                wq = word_qids[w]
                # keep a few early uses, then only new variation formats —
                # otherwise the first file-order occurrences (often all the
                # same ask format) crowd out satire/speech variants entirely
                if len(wq) < 4 or (q_digit not in word_digits[w]
                                   and len(wq) < POINTER_CANDIDATES):
                    wq.append(qid)
                    word_digits[w].add(q_digit)
            for w in counts.keys() & rare:
                lst = occurrences[w].setdefault(mid, [])
                if len(lst) < OCCURRENCE_QIDS:
                    lst.append(qid)
        mi = model_index[mid]
        in_recent = pm["date"] >= recent_lo
        in_prev = prev_lo <= pm["date"] < recent_lo
        snips = cache["snippets"]
        for w, s in full_rate_sums.items():
            rate = s / n_q
            sel = select_diverse_qids(word_qids.get(w, ()))
            shard_rows[w].append(
                [mi, round(rate, 2),
                 [qid_index[q] for q in sel if q in qid_index]])
            if in_recent:
                win[w][1] += rate
            elif in_prev:
                win[w][0] += rate
        for w, snip in snips.items():
            if w not in watch:
                continue
            if mid in heavy[w]:
                snip_pri[w].append({"t": snip["t"], "m": mi, "q": snip["q"]})
            elif len(snip_oth[w]) < SNIPPET_OTHER:
                snip_oth[w].append({"t": snip["t"], "m": mi, "q": snip["q"]})
    print(f"pass B: question sums for {len(question_sums)} questions; "
          f"shard rows for {len(shard_rows)} words")

    # ---- exact matched baselines
    signatures = {}
    for mid in models:
        pm = per_model[mid]
        cache_qids = None
        cand = candidates[mid]
        baseline = defaultdict(float)
        # equal-weight mean over the model's own questions of others' mean rate
        cache = load_cache(os.path.join(cache_dir, pm["cache_file"]))
        qids = list(cache["questions"].keys())
        for qid in qids:
            denom = question_complete[qid] - 1
            if denom <= 0:
                continue
            qs = question_sums[qid]
            for w in cand:
                v = qs.get(w)
                if v:
                    baseline[w] += v / denom
        rows = []
        for w in cand:
            own = pm["rates_hot"].get(w, 0.0)
            matched = max(0.0, (baseline[w] - own_adjust[mid][w]) / max(len(qids), 1))
            score = log2_ratio(own, matched)
            rows.append({
                "w": w, "rate": round(own, 2), "base": round(matched, 2),
                "ratio": round((own + SMOOTH_PM) / (matched + SMOOTH_PM), 2),
                "score": round(score, 3),
                "docs": pm["doc_counts"][w],
                "domains": pm["domain_counts"].get(w, 0),
                "broad": (pm["doc_counts"][w] >= BROAD_DOCS
                          and pm["domain_counts"].get(w, 0) >= BROAD_DOMAINS),
                "prompty": prompt.get(w, 0.0) > max(10.0, own * 0.25),
            })
        rows.sort(key=lambda r: (-r["score"], -r["docs"]))
        signatures[mid] = rows
    _write_json_gz(os.path.join(art_dir, "signatures.json.gz"), {
        mid: rows[:200] for mid, rows in signatures.items()})
    print("matched signatures computed")

    # ---- shared model index artifact
    index = [{
        "id": mid, "slug": per_model[mid]["slug"], "name": per_model[mid]["name"],
        "lab": per_model[mid]["lab"], "family": per_model[mid]["family"],
        "lineage": per_model[mid]["lineage"],
        "date": per_model[mid]["date"].isoformat(),
        "complete": per_model[mid]["n_complete"],
        "color": lab_meta.get(per_model[mid]["lab"], {}).get("color"),
    } for mid in models]
    _write_json_gz(os.path.join(art_dir, "models.json.gz"), index)
    _write_json_gz(os.path.join(art_dir, "classes.json.gz"), {
        "proper": sorted(proper), "person": sorted(person)})
    _write_json_gz(os.path.join(art_dir, "watch.json.gz"), sorted(watch))

    def word_rates_by_model(w):
        return {row[0]: row[1] for row in shard_rows.get(w, ())}

    def trailing_mean(w, at):
        rates_w = word_rates_by_model(w)
        lo = at - timedelta(days=365)
        inw = [mi for mid, mi in model_index.items()
               if lo <= per_model[mid]["date"] <= at]
        if not inw:
            return 0.0
        return sum(rates_w.get(mi, 0.0) for mi in inw) / len(inw)

    # ---- similarity: centered cosine over shared vocabulary; map + neighbors
    sim_vocab = sorted(
        (w for w in watch
         if w not in proper and prompt.get(w, 0.0) <= 50 and prevalence[w] >= 60),
        key=lambda w: -prevalence[w])[:SIM_VOCAB_SIZE]
    sv_index = {w: j for j, w in enumerate(sim_vocab)}
    X = np.zeros((n_models + 2, len(sim_vocab)))
    for mid in models:
        mi = model_index[mid]
        for w, r in per_model[mid]["rates_hot"].items():
            j = sv_index.get(w)
            if j is not None:
                X[mi, j] = math.log1p(r)
    for k, human in enumerate((cmv, brown)):
        for w, r in human.items():
            j = sv_index.get(w)
            if j is not None:
                X[n_models + k, j] = math.log1p(r)
    X -= X[:n_models].mean(axis=0, keepdims=True)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1
    Xn = X / norms
    S = Xn @ Xn.T
    D2 = (1 - S) ** 2
    n_all = D2.shape[0]
    J = np.eye(n_all) - np.ones((n_all, n_all)) / n_all
    B = -0.5 * J @ D2 @ J
    vals, vecs = np.linalg.eigh(B)
    top2 = np.argsort(vals)[::-1][:2]
    coords = vecs[:, top2] * np.sqrt(np.maximum(vals[top2], 0))

    # era-adjusted projection for the map: subtract each word's trailing-12-
    # month field mean at the model's release date before projecting, so the
    # map shows voice-for-its-time — unadjusted, release date dominates the
    # first axis (r ≈ 0.8) and the map reads as a timeline smear
    date_ords = np.array([per_model[mid]["date"].toordinal() for mid in models])
    lo_bound = np.searchsorted(date_ords, date_ords - 365, side="left")
    Xe = np.zeros((n_models, len(sim_vocab)))
    for j, w in enumerate(sim_vocab):
        arr = np.zeros(n_models)
        for row_ in shard_rows.get(w, ()):
            arr[row_[0]] = row_[1]
        csum = np.concatenate(([0.0], np.cumsum(arr)))
        counts = np.maximum(np.arange(n_models) - lo_bound + 1, 1)
        tmean = (csum[1:] - csum[lo_bound]) / counts
        Xe[:, j] = np.log1p(arr) - np.log1p(tmean)
    Xe -= Xe.mean(axis=0, keepdims=True)
    e_norms = np.linalg.norm(Xe, axis=1, keepdims=True)
    e_norms[e_norms == 0] = 1
    Xen = Xe / e_norms
    Se = Xen @ Xen.T
    De2 = (1 - Se) ** 2
    Je = np.eye(n_models) - np.ones((n_models, n_models)) / n_models
    Bem = -0.5 * Je @ De2 @ Je
    evals, evecs = np.linalg.eigh(Bem)
    etop = np.argsort(evals)[::-1][:2]
    map_coords = evecs[:, etop] * np.sqrt(np.maximum(evals[etop], 0))

    neighbors = {}
    for mid in models:
        mi = model_index[mid]
        row = S[mi, :n_models]
        order_i = [j for j in np.argsort(row)[::-1] if j != mi]
        outside = [j for j in order_i if models[j] and per_model[models[j]]["lab"] != per_model[mid]["lab"]]
        mean_sim = float((row.sum() - row[mi]) / max(n_models - 1, 1))
        neighbors[mi] = {
            "top": [[int(j), round(float(row[j]), 3)] for j in order_i[:3]],
            "outside": [[int(j), round(float(row[j]), 3)] for j in outside[:5]],
            "range": [round(float(row[order_i[-1]]), 3), round(float(row[order_i[0]]), 3)],
            "mean": mean_sim,
        }
    ranked_means = sorted(nb["mean"] for nb in neighbors.values())
    for nb in neighbors.values():
        nb["distinct_pct"] = round(
            100 * sum(1 for v in ranked_means if v > nb["mean"]) / max(n_models - 1, 1))
        del nb["mean"]

    # head-to-head vs closest voice: the words that separate the pair
    h2h = {}
    for mid in models:
        mi = model_index[mid]
        other_mid = models[neighbors[mi]["top"][0][0]]
        a_r = per_model[mid]["rates_hot"]
        b_r = per_model[other_mid]["rates_hot"]
        diffs = []
        for w in set(a_r) | set(b_r):
            if w in proper or w not in watch or prompt.get(w, 0.0) > 50:
                continue
            a, b_ = a_r.get(w, 0.0), b_r.get(w, 0.0)
            if max(a, b_) < 15:
                continue
            diffs.append((math.log2((a + 1.0) / (b_ + 1.0)), w, a, b_))
        diffs.sort()
        h2h[mi] = {
            "vs": neighbors[mi]["top"][0][0],
            "left": [[w, round((a + 1.0) / (b_ + 1.0), 1)] for _s, w, a, b_ in diffs[::-1][:5]],
            "right": [[w, round((b_ + 1.0) / (a + 1.0), 1)] for _s, w, a, b_ in diffs[:5]],
        }

    # ---- lineages: qualifying lines, drift tables, predecessor voice steps
    lineages = defaultdict(list)
    for mid in models:
        pm = per_model[mid]
        if pm["lineage"]:
            lineages[(pm["lab"], pm["lineage"])].append(mid)
    qualifying = {}
    for key, mids in lineages.items():
        fams = defaultdict(list)
        for mid in mids:
            fams[per_model[mid]["family"]].append(mid)
        dates = [per_model[mid]["date"] for mid in mids]
        if len(mids) >= 5 and len(fams) >= 3 and (max(dates) - min(dates)).days >= 270:
            qualifying[key] = sorted(
                fams.items(),
                key=lambda kv: sorted(per_model[m]["date"] for m in kv[1])[len(kv[1]) // 2])

    predecessor = {}
    step_sims = []
    for (lab, lin), fam_list in qualifying.items():
        for fi in range(1, len(fam_list)):
            prev_ms = fam_list[fi - 1][1]
            for mid in fam_list[fi][1]:
                sim = float(np.mean([S[model_index[mid], model_index[p]] for p in prev_ms]))
                predecessor[model_index[mid]] = [fam_list[fi - 1][0], round(sim, 3)]
                step_sims.append(sim)
    step_sims.sort()
    for mi, (fam, sim) in predecessor.items():
        pct = 100 * sum(1 for v in step_sims if v > sim) / max(len(step_sims) - 1, 1)
        predecessor[mi] = [fam, sim, round(pct)]

    lineage_tables = defaultdict(list)
    for (lab, lin), fam_list in sorted(qualifying.items()):
        fam_dates = [sorted(per_model[m]["date"] for m in ms)[len(ms) // 2]
                     for _, ms in fam_list]
        lin_mids = [m for _, ms in fam_list for m in ms]
        lin_words = Counter()
        for mid in lin_mids:
            lin_words.update(w for w in per_model[mid]["rates_hot"]
                             if w in watch and w not in proper)
        cands = []
        for w, c in lin_words.items():
            if len(w) < 3 or c < max(3, len(fam_list)) or prompt.get(w, 0.0) > 50:
                continue
            rates_w = word_rates_by_model(w)
            fam_vals = [sum(rates_w.get(model_index[m], 0.0) for m in ms) / len(ms)
                        for _, ms in fam_list]
            hi = max(fam_vals)
            if hi < 20 or sum(1 for v in fam_vals if v >= 0.2 * hi) < 3:
                continue
            early = sum(fam_vals[:2]) / 2
            late = sum(fam_vals[-2:]) / 2
            score = abs(math.log2((late + 3) / (early + 3))) * math.log10(10 + max(early, late))
            cands.append((score, w, fam_vals, late > early))
        cands.sort(reverse=True)
        picked = [c for c in cands if c[3]][:8] + [c for c in cands if not c[3]][:8]
        rows = []
        for _score, w, fam_vals, rising in picked:
            field_vals = [round(trailing_mean(w, d), 1) for d in fam_dates]
            rows.append({"w": w, "vals": [round(v, 1) for v in fam_vals],
                         "field": field_vals, "up": rising})
        lineage_tables[lab].append({
            "lineage": lin,
            "families": [fam for fam, _ in fam_list],
            "dates": [d.isoformat() for d in fam_dates],
            "n_models": len(lin_mids),
            "rows": rows,
        })
    print(f"lineage tables: {sum(len(v) for v in lineage_tables.values())} lineages, "
          f"{len(lineage_tables)} labs")

    # ---- trend / era / names panels
    spark_dates = []
    d = date(2024, 3, 1)
    while d < newest:
        spark_dates.append(d)
        d = date(d.year + (d.month + 2) // 13, (d.month + 2) % 12 + 1, 1)
    # the series always ends exactly at the newest release, so the chart's
    # right edge means "last model" and the trailing-12-month band aligns
    spark_dates.append(newest)

    def series(w):
        return [round(trailing_mean(w, d), 2) for d in spark_dates]

    trend_rows = []
    for w, (s_prev, s_recent) in win.items():
        if prevalence[w] < 80:
            continue
        r0 = s_prev / max(n_prev, 1)
        r1 = s_recent / max(n_recent, 1)
        if max(r0, r1) < 5:
            continue
        trend_rows.append((math.log2((r1 + 1.0) / (r0 + 1.0)), w, round(r0, 1), round(r1, 1)))
    trend_rows.sort(reverse=True)
    vocab_trends = [t for t in trend_rows if t[1] not in proper]
    # names spread far thinner than vocabulary, so they get their own floors —
    # the vocabulary-calibrated 5/M floor hides a whole register of fading
    # casual names (bob, joe, sam, emily…)
    name_trends = []
    for w in person:
        if prevalence.get(w, 0) < 30 or w not in win:
            continue
        s_prev, s_recent = win[w]
        r0 = s_prev / max(n_prev, 1)
        r1 = s_recent / max(n_recent, 1)
        if max(r0, r1) < 1.5:
            continue
        if w.endswith("'s") and w[:-2] in person:
            continue   # possessive duplicates its base name
        name_trends.append((math.log2((r1 + 0.5) / (r0 + 0.5)), w, round(r0, 1), round(r1, 1)))
    name_trends.sort(reverse=True)
    era = []
    for _s, w, r0, r1 in trend_rows:
        if w in proper or r1 < 60 or prompt.get(w, 0.0) > max(10.0, 0.25 * r1):
            continue
        ratio = (r1 + 1.0) / (r0 + 1.0)
        if ratio >= 2:
            era.append((r1 * math.log2(ratio), w, r1, round(ratio, 1)))
    era.sort(reverse=True)
    panels = {
        "windows": {"recent_models": n_recent, "prev_models": n_prev,
                    "newest": newest.isoformat()},
        "spark_dates": [d.isoformat() for d in spark_dates],
        "rising": [{"w": w, "r0": r0, "r1": r1, "s": series(w)}
                   for _s, w, r0, r1 in vocab_trends[:15]],
        "falling": [{"w": w, "r0": r0, "r1": r1, "s": series(w)}
                    for _s, w, r0, r1 in vocab_trends[-15:][::-1]],
        "era": [{"w": w, "r1": r1, "x": ratio, "s": series(w)}
                for _s, w, r1, ratio in era[:12]],
        # ranked lists with a noise floor, not hard gates — a strict cutoff on
        # a ranked panel yields a one-item list when faders are scarce
        "names_up": [{"w": w, "r0": r0, "r1": r1, "s": series(w)}
                     for s_, w, r0, r1 in name_trends if s_ > 0.3][:10],
        "names_down": [{"w": w, "r0": r0, "r1": r1, "s": series(w)}
                       for s_, w, r0, r1 in sorted(name_trends) if s_ < -0.3][:10],
    }

    # ---- dist-ready client data
    dist_dir = os.path.join(art_dir, "dist-data")
    for sub in ("w", "ctx", "occ"):
        os.makedirs(os.path.join(dist_dir, sub), exist_ok=True)
    json.dump(index, open(os.path.join(dist_dir, "models.json"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump(sorted(watch), open(os.path.join(dist_dir, "vocab.json"), "w"),
              separators=(",", ":"))
    json.dump(all_qids, open(os.path.join(dist_dir, "qids.json"), "w"),
              separators=(",", ":"))
    w_shards = defaultdict(dict)
    for w, rows in shard_rows.items():
        w_shards[fnv1a(w) % SHARD_BUCKETS][w] = {
            "h": round(cmv.get(w, 0.0), 2), "m": rows}
    for b, data in w_shards.items():
        json.dump(data, open(os.path.join(dist_dir, "w", f"{b}.json"), "w"),
                  ensure_ascii=False, separators=(",", ":"))
    ctx_shards = defaultdict(dict)
    for w in set(snip_pri) | set(snip_oth):
        rates_w = word_rates_by_model(w)
        lst = (sorted(snip_pri[w], key=lambda s: -rates_w.get(s["m"], 0.0))
               + snip_oth[w])[:SNIPPET_PRIORITY + SNIPPET_OTHER]
        ctx_shards[fnv1a(w) % SHARD_BUCKETS][w] = lst
    for b, data in ctx_shards.items():
        json.dump(data, open(os.path.join(dist_dir, "ctx", f"{b}.json"), "w"),
                  ensure_ascii=False, separators=(",", ":"))
    occ_shards = defaultdict(dict)
    for w, per in occurrences.items():
        occ_shards[fnv1a(w) % SHARD_BUCKETS][w] = {
            str(model_index[mid]): [qid_index[q] for q in qids]
            for mid, qids in per.items()}
    for b, data in occ_shards.items():
        json.dump(data, open(os.path.join(dist_dir, "occ", f"{b}.json"), "w"),
                  separators=(",", ":"))
    _write_json_gz(os.path.join(art_dir, "similarity.json.gz"), {
        "coords": [[round(float(coords[i, 0]), 4), round(float(coords[i, 1]), 4)]
                   for i in range(n_all)],
        "map_coords": [[round(float(map_coords[i, 0]), 4), round(float(map_coords[i, 1]), 4)]
                       for i in range(n_models)],
        "neighbors": {str(mi): nb for mi, nb in neighbors.items()},
        "predecessor": {str(mi): p for mi, p in predecessor.items()},
        "h2h": {str(mi): h for mi, h in h2h.items()},
    })
    _write_json_gz(os.path.join(art_dir, "panels.json.gz"), panels)
    _write_json_gz(os.path.join(art_dir, "lineages.json.gz"), lineage_tables)
    print(f"artifacts written to {art_dir}; client data in {dist_dir}")
    return {
        "models": n_models, "watch": len(watch), "proper": len(proper),
        "candidate_union": len(candidate_union),
        "sim_vocab": len(sim_vocab), "lineage_labs": len(lineage_tables),
        "rising_top": [r["w"] for r in panels["rising"][:3]],
        "era_top": [r["w"] for r in panels["era"][:3]],
    }


if __name__ == "__main__":
    print(build())
