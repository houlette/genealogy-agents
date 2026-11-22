"""Tests for tools/sources_archive.py: the manifest and the missing-file check (no AWS calls)."""
import os
import tempfile
import unittest

import support

import sources_archive  # noqa: E402  (support puts tools/ on sys.path)


class Manifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "sources", "dna"))
        for rel, data in (("sources/a.jpg", b"one"), ("sources/dna/b.PNG", b"three"), ("sources/a.md", b"note")):
            with open(os.path.join(self.root, rel), "wb") as f:
                f.write(data)
        self.manifest = os.path.join(self.root, "sources-manifest.tsv")

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_images_only_with_size_and_hash(self):
        rows = sources_archive.write_manifest(self.root, self.manifest)
        self.assertEqual(sorted(rows), ["sources/a.jpg", "sources/dna/b.PNG"])
        self.assertEqual(rows["sources/a.jpg"][0], 3)
        self.assertEqual(len(rows["sources/a.jpg"][1]), 64)
        with open(self.manifest, encoding="utf-8") as f:
            self.assertEqual(f.readline().strip(), sources_archive.HEADER)

    def test_keeps_rows_for_files_no_longer_on_disk(self):
        sources_archive.write_manifest(self.root, self.manifest)
        os.remove(os.path.join(self.root, "sources", "a.jpg"))
        rows = sources_archive.write_manifest(self.root, self.manifest)
        self.assertIn("sources/a.jpg", rows)

    def test_rehashes_a_changed_file(self):
        first = sources_archive.write_manifest(self.root, self.manifest)
        with open(os.path.join(self.root, "sources", "a.jpg"), "wb") as f:
            f.write(b"changed")
        second = sources_archive.write_manifest(self.root, self.manifest)
        self.assertNotEqual(first["sources/a.jpg"], second["sources/a.jpg"])

    def test_missing_reports_paths_neither_on_disk_nor_listed(self):
        sources_archive.write_manifest(self.root, self.manifest)
        os.remove(os.path.join(self.root, "sources", "a.jpg"))
        gone = sources_archive.missing(["sources/a.jpg", "sources/never.jpg"], self.root, self.manifest)
        self.assertEqual(gone, ["sources/never.jpg"])

    def test_include_args_cover_upper_case(self):
        args = sources_archive.include_args()
        self.assertEqual(args[:2], ["--exclude", "*"])
        self.assertIn("*.JPG", args)



class NewDuplicatesTest(unittest.TestCase):
    def test_reports_only_new_identical_images(self):
        old = {"sources/a.jpg": (10, "h1"), "sources/b.jpg": (10, "h2")}
        rows = dict(old, **{"sources/c.jpg": (10, "h1"), "sources/d.jpg": (10, "h3")})
        self.assertEqual(sources_archive.new_duplicates(old, rows), [("sources/c.jpg", "sources/a.jpg")])


if __name__ == "__main__":
    unittest.main()
