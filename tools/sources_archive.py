#!/usr/bin/env python3
"""Keep the record images of sources/ in an S3 bucket instead of in git.

    python3 tools/sources_archive.py manifest   # write sources-manifest.tsv from the files on disk
    python3 tools/sources_archive.py push       # upload new or changed images (never deletes)
    python3 tools/sources_archive.py verify     # compare the bucket with the manifest
    python3 tools/sources_archive.py pull       # download images the manifest lists but the disk lacks
    python3 tools/sources_archive.py missing PATH...   # cited paths that are neither on disk nor in the manifest

Image files (the extensions in archive.extensions) under sources/ are listed
in .gitignore and are not committed. The manifest, which is committed, lists
each one's path, size in bytes and SHA-256, so a citation in tree.ged or a
dossier can be checked against it, and a fresh clone can get its images back
with `pull`. The sidecar .md files, dossiers and JSON readings stay in git.

Settings in project.json:

    "archive": {
      "bucket":  "my-family-archive",     # required for push, verify, pull
      "prefix":  "",                      # key prefix inside the bucket
      "profile": "family-archive",        # AWS CLI profile; null uses the default
      "region":  "us-east-1",
      "storage_class": "STANDARD_IA"
    }

The upload uses the AWS CLI (`aws s3 sync`), which must be installed. The
tool never passes --delete, so nothing in the bucket is removed; give the
profile a policy without s3:DeleteObject and turn on bucket versioning, and
the archive cannot be lost through the agents.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_config import PROJECT_DIR, load  # noqa: E402

EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".pdf", ".heic", ".bmp")
SOURCES = "sources"
HEADER = "# path\tbytes\tsha256"


def settings():
    a = load().get("archive") or {}
    return {
        "bucket": a.get("bucket"),
        "prefix": (a.get("prefix") or "").strip("/"),
        "profile": a.get("profile"),
        "region": a.get("region") or "us-east-1",
        "storage_class": a.get("storage_class") or "STANDARD_IA",
        "manifest": a.get("manifest") or "sources-manifest.tsv",
    }


def is_image(path):
    return path.lower().endswith(EXTENSIONS)


def image_files(root=PROJECT_DIR):
    base = os.path.join(root, SOURCES)
    out = []
    for d, _, files in os.walk(base):
        for f in files:
            if is_image(f):
                out.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
    return sorted(out)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path):
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            p, size, digest = line.rstrip("\n").split("\t")
            rows[p] = (int(size), digest)
    return rows


def write_manifest(root=PROJECT_DIR, manifest=None):
    """Rewrite the manifest from the images on disk. Rows for images no longer
    on disk are kept, since the bucket still holds them and a citation may
    still name them; the hash of an unchanged file (same size) is reused."""
    manifest = manifest or os.path.join(root, settings()["manifest"])
    old = read_manifest(manifest)
    rows = dict(old)
    for p in image_files(root):
        size = os.path.getsize(os.path.join(root, p))
        if p in old and old[p][0] == size:
            continue
        rows[p] = (size, sha256(os.path.join(root, p)))
    with open(manifest, "w", encoding="utf-8") as f:
        f.write(HEADER + "\n")
        for p in sorted(rows):
            f.write(f"{p}\t{rows[p][0]}\t{rows[p][1]}\n")
    return rows


def new_duplicates(old, rows):
    """New images whose SHA-256 matches an image already listed: (new path, earlier path).
    Two entries on one page are often saved under two names on purpose, so this is a note."""
    by_hash = {}
    for p, (_, digest) in old.items():
        by_hash.setdefault(digest, p)
    out = []
    for p in sorted(rows):
        if p not in old and rows[p][1] in by_hash:
            out.append((p, by_hash[rows[p][1]]))
    return out


def missing(paths, root=PROJECT_DIR, manifest=None):
    """The cited image paths that are neither on disk nor in the manifest."""
    manifest = manifest or os.path.join(root, settings()["manifest"])
    listed = read_manifest(manifest)
    return [p for p in paths if not os.path.exists(os.path.join(root, p)) and p not in listed]


def aws(args, s):
    cmd = ["aws"] + args + ["--region", s["region"]]
    if s["profile"]:
        cmd += ["--profile", s["profile"]]
    return cmd


def s3_url(s, sub=SOURCES):
    key = "/".join(x for x in (s["prefix"], sub) if x)
    return f"s3://{s['bucket']}/{key}/"


def include_args():
    out = ["--exclude", "*"]
    for e in EXTENSIONS:
        out += ["--include", "*" + e, "--include", "*" + e.upper()]
    return out


def need_bucket(s):
    if not s["bucket"]:
        sys.exit("sources_archive: set archive.bucket in project.json first")


def push(s, dry_run=False):
    need_bucket(s)
    cmd = aws(["s3", "sync", os.path.join(PROJECT_DIR, SOURCES), s3_url(s), "--storage-class",
               s["storage_class"], "--only-show-errors"] + include_args() + (["--dryrun"] if dry_run else []), s)
    return subprocess.call(cmd)


def pull(s):
    need_bucket(s)
    cmd = aws(["s3", "sync", s3_url(s), os.path.join(PROJECT_DIR, SOURCES), "--only-show-errors"]
              + include_args(), s)
    return subprocess.call(cmd)


def verify(s):
    need_bucket(s)
    prefix = "/".join(x for x in (s["prefix"], SOURCES) if x) + "/"
    r = subprocess.run(aws(["s3api", "list-objects-v2", "--bucket", s["bucket"], "--prefix", prefix,
                            "--query", "Contents[].[Key,Size]", "--output", "json"], s),
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr.strip())
    strip = len(s["prefix"]) + 1 if s["prefix"] else 0      # keys become "sources/..."
    remote = {k[strip:]: n for k, n in (json.loads(r.stdout) or [])}
    rows = read_manifest(os.path.join(PROJECT_DIR, s["manifest"]))
    absent = [p for p in rows if p not in remote]
    wrong = [p for p in rows if p in remote and remote[p] != rows[p][0]]
    for p in absent[:20]:
        print("NOT IN BUCKET", p)
    for p in wrong[:20]:
        print("SIZE DIFFERS ", p)
    print(f"sources_archive: {len(rows)} in manifest, {len(remote)} in bucket, "
          f"{len(absent)} missing from the bucket, {len(wrong)} with a different size")
    return 1 if absent or wrong else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["manifest", "push", "verify", "pull", "missing"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="push: list what would be uploaded")
    a = ap.parse_args()
    s = settings()
    if a.command == "manifest":
        old = read_manifest(os.path.join(PROJECT_DIR, s["manifest"]))
        rows = write_manifest()
        for new, earlier in new_duplicates(old, rows):
            print(f"NOTE duplicate: {new} is byte-identical to {earlier}, saved earlier; "
                  "keep both only if they are saved for different entries")
        print(f"sources_archive: {len(rows)} images in {s['manifest']}")
        return 0
    if a.command == "missing":
        gone = missing(a.paths)
        for p in gone:
            print(p)
        return 1 if gone else 0
    if a.command == "push":
        return push(s, a.dry_run)
    if a.command == "pull":
        return pull(s)
    return verify(s)


if __name__ == "__main__":
    sys.exit(main())
