import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from scanner import KnowledgeScanner


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix().encode()
        digest.update(rel + b"\0")
        if path.is_symlink():
            digest.update(b"LINK:" + str(path.readlink()).encode())
        elif path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "vault"
        self.root.mkdir()
        (self.root / "README.md").write_text("# Start\n\n[good](guide.md#next) [bad](missing.md) [fragment](guide.md#lost)\n[escape](../outside.md) [via ref][broken]\n[broken]: missing-ref.md\n[[Alias Page]]\n", encoding="utf-8")
        (self.root / "guide.md").write_text("# Guide\n\n## Next\n", encoding="utf-8")
        (self.root / "private-export.csv").write_text("synthetic\n", encoding="utf-8")
        (self.root / ".env.example").write_text("placeholder\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "README.md", "guide.md", "private-export.csv", ".env.example"], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_reports_missing_links_fragments_privacy_and_unknown_rules(self):
        report = KnowledgeScanner(self.root, "README.md").scan()
        rules = [item["rule"] for item in report["findings"]]
        self.assertGreaterEqual(rules.count("links.missing-target"), 2)
        self.assertIn("links.missing-fragment", rules)
        self.assertIn("links.outside-root", rules)
        self.assertIn("privacy.tracked-path", rules)
        self.assertIn("conventions.wikilinks-unknown", rules)
        self.assertIn("conventions.frontmatter-unknown", rules)
        self.assertTrue(report["read_only"])

    def test_missing_entrypoint_is_source_finding(self):
        report = KnowledgeScanner(self.root, "START.md").scan()
        issue = next(item for item in report["findings"] if item["rule"] == "structure.entrypoint")
        self.assertEqual((issue["source"], issue["line"]), ("START.md", 1))

    def test_symlink_escape_is_not_followed(self):
        outside = Path(self.temp.name) / "private.md"
        outside.write_text("# Sensitive title\n", encoding="utf-8")
        (self.root / "outside.md").symlink_to(outside)
        (self.root / "README.md").write_text("[through link](outside.md#sensitive-title)\n", encoding="utf-8")
        report = KnowledgeScanner(self.root).scan()
        issue = next(item for item in report["findings"] if item["rule"] == "links.symlink-skipped")
        self.assertEqual(issue["severity"], "unknown")
        self.assertNotIn("Sensitive title", issue["evidence"])

    def test_scan_leaves_every_file_byte_identical(self):
        before = tree_hash(self.root)
        KnowledgeScanner(self.root).scan()
        after = tree_hash(self.root)
        self.assertEqual(before, after)

    def test_repeated_scans_close_directory_descriptors(self):
        baseline = len(os.listdir("/dev/fd"))
        scanner = KnowledgeScanner(self.root)
        for _ in range(8):
            scanner.scan()
        after = len(os.listdir("/dev/fd"))
        self.assertLessEqual(after, baseline + 1, f"directory descriptors leaked: {baseline} -> {after}")


    def test_parent_relative_link_inside_root_is_valid(self):
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "child.md").write_text("[home](../README.md)\n", encoding="utf-8")
        report = KnowledgeScanner(self.root).scan()
        source_findings = [item for item in report["findings"] if item["source"] == "nested/child.md"]
        self.assertEqual(source_findings, [])


    def test_invalid_url_reference_is_unknown_not_server_error(self):
        (self.root / "README.md").write_text("[broken](http://[bad)\n", encoding="utf-8")
        report = KnowledgeScanner(self.root).scan()
        issue = next(item for item in report["findings"] if item["rule"] == "links.invalid-reference")
        self.assertEqual(issue["severity"], "unknown")

    def test_code_examples_are_not_treated_as_links(self):
        (self.root / "README.md").write_text("Inline `[fake](lost.md)`\n\n```md\n[example](gone.md)\n# Not a heading\n```\n", encoding="utf-8")
        report = KnowledgeScanner(self.root).scan()
        self.assertFalse(any(item["rule"] == "links.missing-target" for item in report["findings"]))

    def test_unicode_heading_fragment_is_unknown_and_wiki_finding_has_location(self):
        (self.root / "international.md").write_text("# Привет\n", encoding="utf-8")
        (self.root / "README.md").write_text("[foreign](international.md#привет)\n\n[[Alias Page]]\n", encoding="utf-8")
        report = KnowledgeScanner(self.root).scan()
        unknown_fragment = next(item for item in report["findings"] if item["rule"] == "links.fragment-convention-unknown")
        wiki = next(item for item in report["findings"] if item["rule"] == "conventions.wikilinks-unknown")
        self.assertEqual(unknown_fragment["severity"], "unknown")
        self.assertEqual((wiki["source"], wiki["line"]), ("README.md", 3))
        self.assertTrue(wiki["context"])




    def test_root_replaced_by_symlink_after_open_stays_on_open_directory(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "README.md").write_text("# Outside secret\n", encoding="utf-8")
        (outside / ".env").write_text("hidden\n", encoding="utf-8")
        scanner = KnowledgeScanner(self.root)
        enumerate_paths = scanner._walk_markdown
        backup = self.root.with_name("vault-opened")
        def swap_root(root_fd):
            self.root.rename(backup)
            self.root.symlink_to(outside, target_is_directory=True)
            return enumerate_paths(root_fd)
        scanner._walk_markdown = swap_root
        report = scanner.scan()
        self.assertEqual(report["files_checked"], 2)
        self.assertIsNone(report["tracked_files"])
        self.assertFalse(any("Outside secret" in str(item) or item["source"] == ".env" for item in report["findings"]))
        self.assertIn("policy.tracking-unknown", [item["rule"] for item in report["findings"]])

    def test_symlink_replaced_after_enumeration_is_not_followed(self):
        outside = Path(self.temp.name) / "outside-private.md"
        outside.write_text("# secret text not read\n", encoding="utf-8")
        raced = self.root / "race.md"
        raced.write_text("# Safe\n", encoding="utf-8")
        scanner = KnowledgeScanner(self.root)
        enumerate_paths = scanner._walk_markdown
        def enumerate_then_swap(root_fd):
            paths = enumerate_paths(root_fd)
            raced.unlink()
            raced.symlink_to(outside)
            return paths
        scanner._walk_markdown = enumerate_then_swap
        report = scanner.scan()
        issue = next(item for item in report["findings"] if item["rule"] == "files.unreadable" and item["source"] == "race.md")
        self.assertNotIn("secret text", str(issue))

    def test_fifo_markdown_is_rejected_without_blocking(self):
        fifo = self.root / "pipe.md"
        import os
        os.mkfifo(fifo)
        report = KnowledgeScanner(self.root).scan()
        issue = next(item for item in report["findings"] if item["source"] == "pipe.md")
        self.assertEqual(issue["rule"], "files.unreadable")

    def test_nested_selected_root_only_reports_its_tracked_paths(self):
        outer = Path(self.temp.name) / "outer"
        selected = outer / "selected"
        selected.mkdir(parents=True)
        (outer / ".env").write_text("synthetic\n", encoding="utf-8")
        (selected / "README.md").write_text("# Selected\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(outer), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(outer), "add", "."], check=True)
        report = KnowledgeScanner(selected).scan()
        self.assertEqual(report["tracked_files"], 1)
        self.assertEqual(report["privacy_hits"], 0)
        self.assertFalse(any(item["source"] == ".env" for item in report["findings"]))

    def test_rejects_entrypoint_escape(self):
        with self.assertRaises(ValueError):
            KnowledgeScanner(self.root, "../outside.md")


if __name__ == "__main__":
    unittest.main()
