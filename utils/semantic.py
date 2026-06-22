#!/usr/bin/env python3
"""Semantic similarity backend for memory dedup and corroboration.

Runs under the mempalace venv, which ships chromadb's offline ONNX MiniLM model
(no torch, ~0.8s import, ~0.27s per encode, fully offline). It is invoked by the
memory layer via subprocess to find semantic near-duplicate observations that the
lexical Jaccard pass misses entirely (two observations that mean the same thing
but share almost no words).

Why 0.80 is the default threshold (calibrated on a real observation corpus, not
guessed): across the calibration pairs the median pair scores ~0.23 and the only
pairs at cos>=0.80 were genuine near-restatements. Same-theme-but-distinct-
incident pairs sit at 0.73 to 0.79, so 0.80 stays deep in the tail and the
false-merge risk is near zero. Crucially the calibration also showed semantic
magnitude alone cannot prove a DUPLICATE (the top pair was two distinct facts),
so the caller uses this signal only to record corroboration and never to
silently delete an observation.

CLI contract (stdin JSON -> stdout JSON), so the heavy import stays isolated in
its own process and out of the caller's venv:

    echo '{"new": "...", "candidates": ["...", "..."], "threshold": 0.8,
           "cache": "/path/.embcache.json"}' | <mempalace_python> semantic.py
    -> {"match_index": <int|null>, "score": <float>}

The pure helpers (cosine, best_match_by_vectors) import without chromadb, so they
are unit-testable from any venv. _embed() lazy-imports chromadb only when an
actual embedding is needed.
"""

import hashlib
import json
import math
import os
import sys
from pathlib import Path

DEFAULT_THRESHOLD = 0.80


def cosine(u, v) -> float:
    """Cosine similarity of two equal-length float vectors. 0.0 if either is zero."""
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u))
    nv = math.sqrt(sum(b * b for b in v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return dot / (nu * nv)


def best_match_by_vectors(new_vec, cand_vecs) -> tuple:
    """Return (index, score) of the candidate most similar to new_vec.

    (None, 0.0) when there are no candidates.
    """
    best_i, best_s = None, -1.0
    for i, cv in enumerate(cand_vecs):
        s = cosine(new_vec, cv)
        if s > best_s:
            best_i, best_s = i, s
    if best_i is None:
        return None, 0.0
    return best_i, best_s


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


_EF = None


def _embed(texts: list) -> list:
    """Embed list[str] -> list[list[float]] via chromadb's offline ONNX MiniLM.

    Lazy import: chromadb is only loaded when a real embedding is required, so
    importing this module for its pure helpers stays cheap and dependency-free.
    """
    global _EF
    if _EF is None:
        from chromadb.utils import embedding_functions
        _EF = embedding_functions.DefaultEmbeddingFunction()
    return [[float(x) for x in vec] for vec in _EF(texts)]


def embed_with_cache(texts: list, cache_path) -> list:
    """Embed texts, reusing and persisting vectors in a hash-keyed JSON cache.

    Each unique observation is embedded once, ever. The cache is pruned to
    exactly the current text set after every call so it stays bounded to the
    recent observation window rather than growing without limit.
    """
    cache = {}
    p = Path(cache_path) if cache_path else None
    if p and p.exists():
        try:
            cache = json.loads(p.read_text())
            if not isinstance(cache, dict):
                cache = {}
        except (json.JSONDecodeError, OSError, ValueError):
            cache = {}

    hashes = [_hash(t) for t in texts]
    missing = [t for t, h in zip(texts, hashes) if h not in cache]
    if missing:
        new_vecs = _embed(missing)
        for t, v in zip(missing, new_vecs):
            cache[_hash(t)] = v

    vecs = [cache[h] for h in hashes]

    if p:
        # Prune to only the hashes seen this call (bounds the cache to the window).
        pruned = {h: cache[h] for h in set(hashes)}
        try:
            tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(pruned))
            tmp.replace(p)
        except OSError:
            pass

    return vecs


def check(new: str, candidates: list, threshold: float = DEFAULT_THRESHOLD,
          cache=None) -> dict:
    """Return {"match_index": int|None, "score": float}.

    match_index is the index into candidates of the best semantic match when its
    cosine meets threshold, else None (score is still the best cosine seen).
    """
    if not candidates:
        return {"match_index": None, "score": 0.0}
    vecs = embed_with_cache(list(candidates) + [new], cache)
    new_vec = vecs[-1]
    cand_vecs = vecs[:-1]
    idx, score = best_match_by_vectors(new_vec, cand_vecs)
    score = round(score, 4)
    if idx is not None and score >= threshold:
        return {"match_index": idx, "score": score}
    return {"match_index": None, "score": score}


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(json.dumps({"match_index": None, "score": 0.0, "error": "bad input"}))
        return
    try:
        res = check(
            payload.get("new", ""),
            payload.get("candidates", []),
            float(payload.get("threshold", DEFAULT_THRESHOLD)),
            payload.get("cache"),
        )
    except Exception as e:  # noqa: BLE001 - never let the backend crash the caller
        print(json.dumps({"match_index": None, "score": 0.0, "error": f"{type(e).__name__}: {e}"}))
        return
    print(json.dumps(res))


if __name__ == "__main__":
    main()
