"""Lexical Fingerprints data layer.

Collects per-model lexical statistics from the analysis records that
`preprocess.iter_preprocessed_us_hard_data` already streams, so a full build
reads each analysis file exactly once. Results are cached per model under
`.cache/lexical/`, keyed by the source file's mtime+size; models with a fresh
cache cost nothing beyond the yield the build was doing anyway.

Cache contents per model (COMPLETE responses only, English word tokens):
- per-question token counts and word counts (case-folded) — enough to compute
  mean within-response rates and the question-matched leave-one-out baseline
- mid-sentence capitalization tallies (proper-noun classification)
- first-occurrence context snippets for the model's most-used words

Standalone cache warm:  uv run python lexical.py
"""
from __future__ import annotations

import gzip
import json
import os
import re
from collections import Counter, defaultdict

# Latin letters incl. diacritics (coöperate, naïve, café stay whole words —
# ASCII-only tokenization shatters them into fragments); CJK/Cyrillic/Arabic
# stay excluded, preserving the English-analysis boundary.
TOKEN_RE = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ']*")
SENTENCE_BREAK = set('.!?:;"“*-•)(')
# a period after an honorific is not a sentence boundary — names predominantly
# follow honorifics, so treating "Dr." as a boundary suppresses exactly the
# capitalization evidence that identifies person names
ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "prof", "st", "jr", "sr", "gen", "sen", "rep",
    "gov", "lt", "col", "sgt", "capt", "rev", "hon", "fr", "vs", "v",
}
CACHE_VERSION = 3
SNIPPET_BEFORE = 70
SNIPPET_AFTER = 110
MIN_TOKEN_LEN = 2         # single letters are markdown/list noise


def _heading_like(line):
    s = line.strip()
    return s.startswith("#") or (s.startswith("**") and s.endswith("**"))


def _snippet_at(text, start, end):
    lo = max(0, start - SNIPPET_BEFORE)
    if lo > 0:
        sp = text.find(" ", lo)
        lo = sp + 1 if 0 <= sp < start else lo
    hi = min(len(text), end + SNIPPET_AFTER)
    if hi < len(text):
        sp = text.rfind(" ", end, hi)
        hi = sp if sp > end else hi
    snip = " ".join(text[lo:hi].split())
    return ("…" if lo > 0 else "") + snip + ("…" if hi < len(text) else "")


def source_path_for_model(analysis_dir, model):
    """Analysis filename convention: provider/model -> provider_model."""
    return os.path.join(
        analysis_dir, f"compliance_us_hard_{model.replace('/', '_', 1)}.jsonl"
    )


class _ModelAccumulator:
    def __init__(self, model, source_file=None):
        self.model = model
        self.source_file = source_file
        self.questions = {}          # qid -> [token_count, Counter]
        self.caps = defaultdict(lambda: [0, 0])   # word -> [midcap, midlow]
        self.word_totals = Counter()
        self.snippets = {}           # word -> {"t": snippet, "q": qid}
        self.domains = defaultdict(set)           # word -> set of domains
        self.complete_seen = 0
        self.skipped_no_tokens = 0

    def add(self, record):
        text = record.get("response_text") or ""
        qid = str(record.get("original_question_id") or "")
        domain = record.get("domain")
        if not text or not qid:
            return
        self.complete_seen += 1
        counts = Counter()
        n_tokens = 0
        text = text.replace("’", "'").replace("‘", "'")
        for line in text.split("\n"):
            heading = _heading_like(line)
            for m in TOKEN_RE.finditer(line):
                tok = m.group(0)
                word = tok.lower()
                counts[word] += 1
                n_tokens += 1
                if word not in self.snippets:
                    self.snippets[word] = {"t": _snippet_at(line, m.start(), m.end()), "q": qid}
                if heading or len(tok) < MIN_TOKEN_LEN:
                    continue
                prev = line[: m.start()].rstrip()
                initial = not prev or prev[-1] in SENTENCE_BREAK
                if initial and prev.endswith("."):
                    prior = prev[:-1].rsplit(None, 1)[-1].lower() if prev[:-1].strip() else ""
                    if prior in ABBREVIATIONS:
                        initial = False
                if not initial:
                    self.caps[word][0 if tok[0].isupper() else 1] += 1
        if not n_tokens:
            self.skipped_no_tokens += 1
            return
        self.questions[qid] = [n_tokens, dict(counts)]
        self.word_totals.update(counts)
        if isinstance(domain, str):
            for word in counts:
                self.domains[word].add(domain)

    def to_cache(self, source_stat):
        # every word keeps its first-occurrence snippet: which snippets are
        # worth SHIPPING (common vs globally-rare words) is a build-time
        # decision — the scanner can't know what's rare across the corpus
        return {
            "version": CACHE_VERSION,
            "model": self.model,
            "source_file": self.source_file,
            "source_mtime_ns": source_stat.st_mtime_ns,
            "source_size": source_stat.st_size,
            "complete_responses": self.complete_seen,
            "skipped_no_tokens": self.skipped_no_tokens,
            "questions": self.questions,
            # no per-model floor: globally common but per-model sparse names
            # (one stock character per response) need every model's evidence
            "caps": dict(self.caps),
            "domain_counts": {w: len(d) for w, d in self.domains.items()},
            "snippets": self.snippets,
        }


_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(_MODULE_DIR, ".cache", "lexical")


class LexicalCollector:
    """Consumes preprocess records; writes per-model lexical caches.

    Models whose cache matches the source file's mtime+size are skipped
    entirely — observe() becomes a constant-time no-op for their records.
    Paths are anchored to this module's directory, never the cwd — the site
    build chdirs into dist/ while streaming records.
    """

    def __init__(self, analysis_dir, cache_dir=DEFAULT_CACHE_DIR):
        self.analysis_dir = analysis_dir
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._fresh = set()
        self._done = set()
        self._current = None
        self._stats_written = 0
        self._stats_fresh = 0

    def _cache_path(self, model):
        from preprocess import generate_safe_id
        return os.path.join(self.cache_dir, f"{generate_safe_id(model)}.json.gz")

    def _is_fresh(self, model, source_file=None):
        cpath = self._cache_path(model)
        if not os.path.exists(cpath):
            return False
        try:
            with gzip.open(cpath, "rt", encoding="utf-8") as handle:
                head = json.loads(handle.readline())
        except (OSError, json.JSONDecodeError):
            return False
        # the recorded source filename is authoritative (filenames don't always
        # follow the provider_model convention); fall back to the convention
        # for caches written before the filename was recorded
        src_name = source_file or head.get("source_file")
        src = (os.path.join(self.analysis_dir, src_name) if src_name
               else source_path_for_model(self.analysis_dir, model))
        if not os.path.exists(src):
            return False
        stat = os.stat(src)
        return (
            head.get("version") == CACHE_VERSION
            and head.get("source_mtime_ns") == stat.st_mtime_ns
            and head.get("source_size") == stat.st_size
        )

    def observe(self, record):
        model = record.get("model")
        if not model or model in self._fresh:
            return
        if self._current is not None and self._current.model != model:
            self._flush()
        if self._current is None:
            if model in self._done:
                raise RuntimeError(
                    f"Records for {model!r} arrived after its cache was written; "
                    "analysis files are expected to be contiguous per model"
                )
            if self._is_fresh(model, record.get("source_file")):
                self._fresh.add(model)
                self._stats_fresh += 1
                return
            self._current = _ModelAccumulator(model, record.get("source_file"))
        if record.get("compliance") == "COMPLETE":
            self._current.add(record)

    def _flush(self):
        acc = self._current
        self._current = None
        if acc is None:
            return
        src = (os.path.join(self.analysis_dir, acc.source_file) if acc.source_file
               else source_path_for_model(self.analysis_dir, acc.model))
        try:
            stat = os.stat(src)
        except OSError:
            print(f"lexical: no source file for {acc.model!r}; cache not written")
            return
        payload = acc.to_cache(stat)
        # header line first so freshness checks read one line, not the blob
        header = {k: payload[k] for k in
                  ("version", "model", "source_file", "source_mtime_ns", "source_size")}
        cpath = self._cache_path(acc.model)
        tmp = cpath + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as handle:
            handle.write(json.dumps(header, separators=(",", ":")) + "\n")
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, cpath)
        self._done.add(acc.model)
        self._stats_written += 1

    def finalize(self):
        self._flush()
        print(
            f"lexical: caches written {self._stats_written}, "
            f"fresh (skipped) {self._stats_fresh}"
        )


def load_cache(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        handle.readline()
        return json.load(handle)


def stale_analysis_files(analysis_dir, cache_dir=DEFAULT_CACHE_DIR):
    """Analysis files whose lexical cache is missing or out of date.

    Cache headers carry the source file's mtime+size, so freshness is decided
    from one gzip header line per cache — no analysis file is opened here.
    Lets a standalone warm skip fresh files entirely instead of streaming the
    whole corpus to discover there is nothing to do.
    """
    import glob as _glob
    fresh_sources = {}
    for cpath in _glob.glob(os.path.join(cache_dir, "*.json.gz")):
        try:
            with gzip.open(cpath, "rt", encoding="utf-8") as handle:
                head = json.loads(handle.readline())
        except (OSError, json.JSONDecodeError):
            continue
        if head.get("version") != CACHE_VERSION:
            continue
        src = (os.path.join(analysis_dir, head["source_file"]) if head.get("source_file")
               else source_path_for_model(analysis_dir, head.get("model", "")))
        fresh_sources[src] = (head.get("source_mtime_ns"), head.get("source_size"))
    stale = []
    for src in sorted(_glob.glob(os.path.join(analysis_dir, "compliance_us_hard_*.jsonl"))):
        expected = fresh_sources.get(src)
        stat = os.stat(src)
        if expected != (stat.st_mtime_ns, stat.st_size):
            stale.append(src)
    return stale


def main():
    data_root = os.environ.get("SPEECHMAP_DATA_ROOT", os.path.join("..", "speechmap-data"))
    analysis_dir = os.path.join(data_root, "analysis")
    from preprocess import iter_preprocessed_us_hard_data
    stale = stale_analysis_files(analysis_dir)
    if not stale:
        print("lexical: all caches fresh — nothing to scan")
        return
    print(f"lexical: {len(stale)} analysis file(s) need scanning")
    collector = LexicalCollector(analysis_dir)
    for record in iter_preprocessed_us_hard_data(analysis_dir, only_files=stale):
        collector.observe(record)
    collector.finalize()


if __name__ == "__main__":
    main()
