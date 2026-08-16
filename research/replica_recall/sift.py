"""
SIFT1M loader for the replica-recall experiment.

Every number this package has produced so far came from a vector generator
written for this project. `--dist uniform` measures distance concentration
rather than the index; `--dist lowdim` was built specifically to dodge that.
Both are defensible, and neither answers the first question a reader asks:
does the effect survive on real embeddings?

SIFT1M is the standard answer. It is 128-dimensional, which matches
`config::VECTOR_DIM` exactly, so it substitutes for the synthetic generators
with no dimensionality surgery anywhere in the stack.

Two jobs: fetch the prefix we need, and parse fvecs. numpy plus the standard
library, nothing else.

    from sift import load
    base, queries = load(cache_dir, n_base=200_000)

Usage as a script (pre-warm the cache before a sweep, so the download is not
sitting inside a timed run):

    python research/replica_recall/sift.py --vectors 200000
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import urllib.request

import numpy as np

# fvecs: each record is an int32 dimension header followed by that many
# float32 components. For 128-d that is a fixed 516-byte stride, which is what
# makes the ranged fetch below exact rather than approximate -- we can ask for
# the first N vectors by byte offset and know we got whole records.
EXPECTED_DIM = 128
HEADER_BYTES = 4

# The canonical distribution is ftp://ftp.irisa.fr/local/texmex/corpus/sift.tar.gz,
# which is a 161 MB tarball of the whole 1M base set. This mirror serves the
# fvecs files individually over HTTPS with byte-range support, so we can pull
# just the prefix we use -- ~103 MB for 200k vectors instead of 516 MB.
BASE_URL = "https://huggingface.co/datasets/qbo-odp/sift1m/resolve/main"
BASE_FILE = "sift_base.fvecs"
QUERY_FILE = "sift_query.fvecs"

# SIFT components are integers 0..255, so ||v||^2 reaches ~8.3e6 -- right at
# the ceiling below which float32 represents integers exactly (2^24 = 1.68e7,
# and sums of squares get there fast). exact_topk_rows in metrics.py uses the
# expanded form ||v||^2 - 2*q.v, whose cancellation is worst precisely when
# the terms are largest.
#
# 128 is a power of two, so dividing by it is a bare exponent decrement: the
# mantissa is untouched, no rounding occurs, and the ranking is therefore
# provably identical to the ranking on the raw values. It lands the components
# in [0, 2), which is the range the index, the JSON write path and the
# synthetic distributions have always operated in.
#
# Consequence worth knowing: distances computed here are smaller than published
# SIFT distances by a factor of 128^2 = 16384. Ranks are unaffected; absolute
# distances are not comparable to the literature without multiplying back.
SCALE = 1.0 / 128.0


def default_cache_dir() -> str:
    """Where the fvecs files live.

    `data/` is already in .gitignore unanchored, so this path is ignored at any
    depth -- a 103 MB dataset cannot wander into a commit. NANODB_SIFT_DIR
    overrides, following the NANODB_* convention the C++ binaries already use.
    """
    env = os.environ.get("NANODB_SIFT_DIR")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def record_bytes(dim: int = EXPECTED_DIM) -> int:
    return HEADER_BYTES + 4 * dim


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def read_fvecs(src, limit: int | None = None,
               dim: int = EXPECTED_DIM) -> np.ndarray:
    """Parse fvecs into an (n, dim) float32 array. Raw values, unscaled.

    src   : path, bytes, or a file-like object with .read()
    limit : stop after this many vectors. Reads only the bytes needed, so a
            200k prefix of a 1M file costs 200k records of I/O.

    Every record's dimension header is checked. A truncated download, a mirror
    that ignored our Range request, or a GIST file renamed to look like SIFT
    all produce a wrong header or a partial record, and all of them raise here
    rather than silently yielding a shorter or malformed corpus. Silently
    shrinking the corpus is the failure mode that would quietly invalidate a
    sweep, so it is the one worth being loud about.
    """
    rec = record_bytes(dim)
    want = None if limit is None else limit * rec
    buf = _read_bytes(src, want)

    if len(buf) == 0:
        raise ValueError(f"{_name(src)}: empty")
    if len(buf) % rec:
        raise ValueError(
            f"{_name(src)}: {len(buf)} bytes is not a whole number of "
            f"{rec}-byte records (truncated download, or dim != {dim})")

    n = len(buf) // rec
    if limit is not None and n < limit:
        raise ValueError(
            f"{_name(src)}: asked for {limit} vectors, only {n} available")

    # int32 and float32 share an itemsize, so the header column and the data
    # columns are two views over one buffer -- no copy of the 103 MB.
    words = np.frombuffer(buf, dtype=np.int32).reshape(n, dim + 1)
    bad = np.nonzero(words[:, 0] != dim)[0]
    if bad.size:
        raise ValueError(
            f"{_name(src)}: record {int(bad[0])} declares dim="
            f"{int(words[bad[0], 0])}, expected {dim}"
            + (f" ({bad.size} records disagree)" if bad.size > 1 else ""))

    return np.ascontiguousarray(words[:, 1:].view(np.float32))


def _read_bytes(src, want: int | None) -> bytes:
    if isinstance(src, (bytes, bytearray, memoryview)):
        b = bytes(src)
        return b if want is None else b[:want]
    if hasattr(src, "read"):
        return src.read() if want is None else src.read(want)
    with open(src, "rb") as fh:
        return fh.read() if want is None else fh.read(want)


def _name(src) -> str:
    if isinstance(src, (bytes, bytearray, memoryview)):
        return "<buffer>"
    if isinstance(src, io.IOBase):
        return getattr(src, "name", "<stream>")
    return str(src)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch(cache_dir: str | None = None, n_base: int = 200_000,
          dim: int = EXPECTED_DIM, quiet: bool = False) -> tuple[str, str]:
    """Ensure the base prefix and the full query set are cached locally.

    Returns (base_path, query_path). Idempotent: a cache file already large
    enough is left alone, so this is safe to call at the top of every run.
    """
    cache_dir = cache_dir or default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)

    base_path = os.path.join(cache_dir, BASE_FILE)
    query_path = os.path.join(cache_dir, QUERY_FILE)

    _ensure(base_path, f"{BASE_URL}/{BASE_FILE}",
            n_base * record_bytes(dim), quiet)
    # The query set is 10,000 x 516 = 5.16 MB. Small enough to just take whole.
    _ensure(query_path, f"{BASE_URL}/{QUERY_FILE}", None, quiet)
    return base_path, query_path


def _ensure(path: str, url: str, want_bytes: int | None, quiet: bool) -> None:
    have = os.path.getsize(path) if os.path.exists(path) else 0
    if have and (want_bytes is None or have >= want_bytes):
        if not quiet:
            print(f"[sift] cached  {os.path.basename(path)} ({have/1e6:.0f} MB)")
        return

    req = urllib.request.Request(url)
    if want_bytes is not None:
        req.add_header("Range", f"bytes=0-{want_bytes - 1}")

    if not quiet:
        size = "whole file" if want_bytes is None else f"{want_bytes/1e6:.0f} MB"
        print(f"[sift] fetching {os.path.basename(path)} ({size})...")

    # Download to .part and rename only on success. An interrupted fetch must
    # not leave a short file behind that the size check above would later read
    # as a cache hit.
    tmp = path + ".part"
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            # A mirror that ignores Range answers 200 and starts streaming all
            # 516 MB. Catch that before writing anything, rather than
            # discovering it via the disk filling up.
            if want_bytes is not None and resp.status != 206:
                raise RuntimeError(
                    f"{url}: asked for a byte range, server answered "
                    f"{resp.status} (no range support); refusing to download "
                    f"the whole file")
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)

        got = os.path.getsize(tmp)
        if want_bytes is not None and got != want_bytes:
            raise RuntimeError(
                f"{url}: requested {want_bytes} bytes, received {got}")
        os.replace(tmp, path)
    except BaseException:
        # Includes KeyboardInterrupt: a Ctrl-C mid-download must not leave a
        # .part behind, and must never leave a short file at `path`.
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if not quiet:
        print(f"[sift] wrote    {os.path.basename(path)} ({got/1e6:.0f} MB)")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(cache_dir: str | None = None, n_base: int = 200_000,
         dim: int = EXPECTED_DIM, scale: float = SCALE,
         quiet: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Fetch if needed, then return (base, queries) as scaled float32 arrays.

    base    : (n_base, dim)
    queries : (10000, dim)

    See SCALE for why the values are divided by 128 and why that leaves the
    ranking untouched.
    """
    base_path, query_path = fetch(cache_dir, n_base, dim, quiet)
    base = read_fvecs(base_path, limit=n_base, dim=dim)
    queries = read_fvecs(query_path, dim=dim)
    if scale != 1.0:
        base = base * np.float32(scale)
        queries = queries * np.float32(scale)
    return np.ascontiguousarray(base), np.ascontiguousarray(queries)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pre-warm the SIFT1M cache used by --dist sift.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vectors", type=int, default=200_000,
                    help="how many base vectors to cache (default 200000, "
                         "~103 MB)")
    ap.add_argument("--dir", default=None,
                    help=f"cache directory (default {default_cache_dir()})")
    args = ap.parse_args()

    base, queries = load(args.dir, args.vectors)
    print(f"[sift] base    {base.shape} {base.dtype}  "
          f"range [{base.min():.4f}, {base.max():.4f}]")
    print(f"[sift] queries {queries.shape} {queries.dtype}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
