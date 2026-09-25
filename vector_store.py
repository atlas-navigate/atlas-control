"""
Ray's passage vectors as one normalized float32 matrix.

Scoring a question against every stored passage is a single matrix-vector
product (about 0.2 ms for the shipped knowledge base) instead of converting
each stored vector from a Python list and taking cosines one at a time
(about 50 ms, all of it holding the event loop's GIL). The per-passage float
lists are not kept, which also saves ~15 MB of RAM.

The index is rebuilt only when documents or embeddings change
(database.ai_docs_version()), with a slow timer as a safety net for writes
from another process. Storage is unchanged — the JSON embedding column is
still the source of truth — so rolling back to an older release is safe.
"""
import json
import threading
import time

import numpy as np

import atlas_blocking
import database

REFRESH_S = 600

_lock = threading.Lock()
_current = {"index": None, "version": None, "built": 0.0}


def _chunks(doc):
    """[{h, t, e}] from a stored embedding: chunked {"chunks": [...]} or a
    legacy whole-document vector."""
    raw = doc.get("embedding")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if isinstance(parsed, dict) and isinstance(parsed.get("chunks"), list):
        return [c for c in parsed["chunks"] if isinstance(c, dict) and c.get("e")]
    if isinstance(parsed, list) and parsed:
        return [{"h": "", "t": doc.get("content") or "", "e": parsed}]
    return []


class PassageIndex:
    def __init__(self, docs):
        """docs: rows of database.ai_get_documents_with_embeddings()."""
        self.entries = []            # [(doc without its embedding, [{"h", "t"}, ...])]
        by_dim = {}                  # dim -> (vectors, [(entry, chunk)])
        for doc in docs:
            chunks = _chunks(doc)
            if not chunks:
                continue
            i = len(self.entries)
            self.entries.append(({k: v for k, v in doc.items() if k != "embedding"},
                                 [{"h": c.get("h", ""), "t": c.get("t", "")} for c in chunks]))
            for j, c in enumerate(chunks):
                vecs, where = by_dim.setdefault(len(c["e"]), ([], []))
                vecs.append(c["e"])
                where.append((i, j))
        self._by_dim = {}
        for dim, (vecs, where) in by_dim.items():
            m = np.asarray(vecs, dtype=np.float32)
            norms = np.linalg.norm(m, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._by_dim[dim] = (m / norms, where)

    def __len__(self):
        return len(self.entries)

    def scores(self, query):
        """Cosine of `query` against every passage, shaped like `entries`
        (None where a stored vector has another dimension — left over from a
        different embedding model)."""
        out = [[None] * len(chunks) for _doc, chunks in self.entries]
        q = np.asarray(query, dtype=np.float32)
        found = self._by_dim.get(q.shape[0]) if q.ndim == 1 else None
        if found is None:
            return out
        matrix, where = found
        n = float(np.linalg.norm(q))
        values = (matrix @ (q / n)).tolist() if n else [0.0] * len(where)
        for (i, j), v in zip(where, values):
            out[i][j] = v
        return out

    def doc_similarity(self):
        """(doc ids, matrix) where matrix[a][b] is the best cosine between any
        passage of doc a and any passage of doc b, over the most common vector
        dimension. Diagonal is meaningless."""
        if not self._by_dim:
            return [], np.zeros((0, 0), dtype=np.float32)
        matrix, where = max(self._by_dim.values(), key=lambda mw: len(mw[1]))
        # Rows were added document by document, so each doc's rows are contiguous.
        starts, ids, last = [], [], None
        for row, (i, _j) in enumerate(where):
            if i != last:
                starts.append(row)
                ids.append(self.entries[i][0]["id"])
                last = i
        sims = matrix @ matrix.T
        per_doc = np.maximum.reduceat(np.maximum.reduceat(sims, starts, axis=0), starts, axis=1)
        return ids, per_doc


def _current_locked():
    with _lock:
        version = database.ai_docs_version()
        c = _current
        if c["index"] is None or c["version"] != version or time.monotonic() - c["built"] > REFRESH_S:
            c["index"] = PassageIndex(database.ai_get_documents_with_embeddings())
            c["version"] = version
            c["built"] = time.monotonic()
        return c["index"]


def current():
    """The up-to-date index. Built off the event loop when it must be rebuilt."""
    c = _current
    if c["index"] is not None and c["version"] == database.ai_docs_version() \
            and time.monotonic() - c["built"] <= REFRESH_S:
        return c["index"]
    return atlas_blocking.call(_current_locked)


def invalidate():
    _current["index"] = None
