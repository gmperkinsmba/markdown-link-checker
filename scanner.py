"""Read-only checks for a configured Markdown knowledge base."""
from __future__ import annotations
import fnmatch
import os
import posixpath
import re
import subprocess
import stat
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

LINK = re.compile(r'!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)')
WIKI = re.compile(r'!?\[\[([^\]]+)\]\]')
REF_DEF = re.compile(r'^\s{0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))')
REF_USE = re.compile(r'!?\[([^\]]*)\]\[([^\]]*)\]')
HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$')
FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})')
INLINE_CODE = re.compile(r'(`+).*?\1')
PRIVACY_PATTERNS = ("**/.env", "**/.env.*", "**/*.env", "**/*secret*", "**/*credential*", "**/*private*", "**/*export*")
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 25_000_000
MAX_MARKDOWN_FILES = 2500


class _FileTooLarge(Exception):
    pass


class _NotRegularFile(Exception):
    pass


def _slug(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 -]", "", text).replace(" ", "-").replace("--", "-").strip("-")


def _has_symlink(root: Path, candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _safe_rel(value: str) -> bool:
    p = PurePosixPath(value)
    return not p.is_absolute() and ".." not in p.parts and "\\" not in value and "\x00" not in value


def _safe_link_path(value: str) -> bool:
    p = PurePosixPath(value)
    return not p.is_absolute() and "\\" not in value and "\x00" not in value


def _visible_lines(lines: list[str]):
    fence_char = ""
    fence_size = 0
    for line_no, original in enumerate(lines, 1):
        fence = FENCE.match(original)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_size:
                fence_char, fence_size = "", 0
                continue
        if fence_char:
            continue
        parsed = INLINE_CODE.sub("", original)
        yield line_no, original, parsed


def _iter_links(lines: list[str]):
    visible = list(_visible_lines(lines))
    refs: dict[str, str] = {}
    for _, _, parsed in visible:
        match = REF_DEF.match(parsed)
        if match:
            refs[match.group(1).strip().lower()] = match.group(2) or match.group(3) or ""
    for line_no, _, parsed in visible:
        for match in LINK.finditer(parsed):
            yield line_no, (match.group(1) or match.group(2) or "").strip().strip('"\''), "link"
        for match in REF_USE.finditer(parsed):
            key = (match.group(2).strip() or match.group(1).strip()).lower()
            if key in refs:
                yield line_no, refs[key], "reference"
        for match in WIKI.finditer(parsed):
            yield line_no, match.group(1), "wiki"


class KnowledgeScanner:
    def __init__(self, root: Path, entrypoint: str = "README.md"):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Selected root must be a directory")
        if not _safe_rel(entrypoint):
            raise ValueError("Entrypoint must stay inside the selected directory")
        self.entrypoint = str(PurePosixPath(entrypoint))

    def _walk_markdown(self, root_fd: int) -> tuple[list[str], list[str]]:
        files: list[str] = []
        skipped_links: list[str] = []
        pending: list[tuple[int, str, bool]] = [(os.dup(root_fd), "", True)]
        try:
            while pending:
                directory_fd, prefix, owned = pending.pop()
                try:
                    scan_fd = os.dup(directory_fd)
                    try:
                        with os.scandir(scan_fd) as iterator:
                            entries = sorted(list(iterator), key=lambda entry: entry.name)
                    finally:
                        os.close(scan_fd)
                    for entry in entries:
                        if entry.name == ".git":
                            continue
                        rel = f"{prefix}/{entry.name}" if prefix else entry.name
                        if not _safe_rel(rel):
                            continue
                        if entry.is_symlink():
                            skipped_links.append(rel)
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            try:
                                child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                            except OSError:
                                skipped_links.append(rel)
                                continue
                            pending.append((child_fd, rel, True))
                        elif entry.name.lower().endswith(".md"):
                            files.append(rel)
                finally:
                    if owned:
                        os.close(directory_fd)
        finally:
            for descriptor, _prefix, owned in pending:
                if owned:
                    os.close(descriptor)
        return files, skipped_links

    def _root_path_matches(self, root_fd: int) -> bool:
        try:
            path_info = os.stat(self.root, follow_symlinks=False)
            fd_info = os.fstat(root_fd)
            return stat.S_ISDIR(path_info.st_mode) and (path_info.st_dev, path_info.st_ino) == (fd_info.st_dev, fd_info.st_ino)
        except OSError:
            return False

    def _tracked(self, root_fd: int) -> tuple[list[str], bool]:
        if not self._root_path_matches(root_fd):
            return [], False
        try:
            result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(self.root), "ls-files", "-z"], capture_output=True, timeout=4, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return [], False
        if result.returncode != 0 or not self._root_path_matches(root_fd):
            return [], False
        paths = [p.decode("utf-8", "replace") for p in result.stdout.split(b"\0") if p]
        return [p for p in paths if _safe_rel(p)], True

    @staticmethod
    def _target_kind(root_fd: int, rel: str) -> str:
        parts = () if rel in ("", ".") else PurePosixPath(rel).parts
        if not parts:
            return "directory"
        parent_fd = os.dup(root_fd)
        opened = [parent_fd]
        try:
            for component in parts[:-1]:
                try:
                    parent_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                except FileNotFoundError:
                    return "missing"
                except OSError:
                    return "symlink-or-unavailable"
                opened.append(parent_fd)
            try:
                info = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return "missing"
            if stat.S_ISLNK(info.st_mode):
                return "symlink"
            if stat.S_ISDIR(info.st_mode):
                return "directory"
            if stat.S_ISREG(info.st_mode):
                return "file"
            return "other"
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)

    @staticmethod
    def _read_regular(root_fd: int, rel: str) -> tuple[str, int]:
        parts = PurePosixPath(rel).parts
        directory_fd = root_fd
        opened_dirs: list[int] = []
        file_fd = None
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        if not nofollow or not directory_flag or os.open not in os.supports_dir_fd:
            raise OSError("Secure descriptor-relative reads are not supported on this platform")
        try:
            for component in parts[:-1]:
                directory_fd = os.open(component, os.O_RDONLY | directory_flag | nofollow, dir_fd=directory_fd)
                opened_dirs.append(directory_fd)
            file_fd = os.open(parts[-1], os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=directory_fd)
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise _NotRegularFile
            if info.st_size > MAX_FILE_BYTES:
                raise _FileTooLarge
            chunks = []
            total = 0
            while total <= MAX_FILE_BYTES:
                chunk = os.read(file_fd, min(65536, MAX_FILE_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise _FileTooLarge
            return b"".join(chunks).decode("utf-8"), total
        finally:
            if file_fd is not None:
                os.close(file_fd)
            for descriptor in reversed(opened_dirs):
                os.close(descriptor)

    @staticmethod
    def _context(lines: list[str], line: int) -> list[dict]:
        start, stop = max(1, line - 2), min(len(lines), line + 2)
        return [{"line": number, "text": lines[number - 1][:240]} for number in range(start, stop + 1)]

    def settings(self) -> dict:
        return {"root_path": str(self.root), "entrypoint": self.entrypoint, "privacy_rules": list(PRIVACY_PATTERNS),
                "limits": {"markdown_files": MAX_MARKDOWN_FILES, "per_file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES},
                "checks": ["Markdown entrypoint", "inline and reference-style relative links", "GitHub-style Markdown heading fragments", "tracked privacy paths"]}

    def scan(self) -> dict:
        findings: list[dict] = []
        try:
            if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0) or os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd:
                raise OSError("Safe descriptor-relative reads are not supported on this platform")
            root_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            root_fd = os.open(self.root, root_flags)
        except OSError:
            findings.append(self._finding("root.read-blocked", "unknown", ".", 0, "Selected root could not be opened as a safe directory."))
            return self._report(findings, 0, 0, None, 0)
        try:
            found_markdown, skipped_symlinks = self._walk_markdown(root_fd)
        except OSError:
            os.close(root_fd)
            findings.append(self._finding("root.read-blocked", "unknown", ".", 0, "Selected root could not be enumerated safely."))
            return self._report(findings, 0, 0, None, 0)
        try:
            for rel in skipped_symlinks:
                findings.append(self._finding("files.symlink-skipped", "unknown", rel, 0, "Symlink path was skipped; its target was not opened."))
            markdown = found_markdown[:MAX_MARKDOWN_FILES]
            if len(found_markdown) > MAX_MARKDOWN_FILES:
                findings.append(self._finding("limits.file-count", "unknown", ".", 0, f"Only the first {MAX_MARKDOWN_FILES} Markdown paths were considered."))
            headings: dict[str, set[str]] = {}
            ambiguous_headings: set[str] = set()
            bodies: dict[str, list[str]] = {}
            total_bytes = 0
            try:
                for rel in markdown:
                    try:
                        text, size = self._read_regular(root_fd, rel)
                        if total_bytes + size > MAX_TOTAL_BYTES:
                            findings.append(self._finding("limits.byte-count", "unknown", ".", 0, "The 25 MB total read limit was reached; later Markdown paths were skipped."))
                            break
                        total_bytes += size
                    except _FileTooLarge:
                        findings.append(self._finding("files.size-limit", "unknown", rel, 1, "Markdown file exceeds the 1 MB scan limit and was skipped."))
                        continue
                    except (_NotRegularFile, OSError, UnicodeError):
                        findings.append(self._finding("files.unreadable", "unknown", rel, 1, "Markdown path was not a readable regular UTF-8 file and was skipped."))
                        continue
                    bodies[rel] = text.splitlines()
                    slugs: set[str] = set()
                    for line_no, original, _ in _visible_lines(bodies[rel]):
                        match = HEADING.match(original)
                        if not match:
                            continue
                        title = match.group(2)
                        slug = _slug(title)
                        if not title.isascii() or "{#" in title or "<" in title:
                            ambiguous_headings.add(rel)
                        if slug in slugs:
                            count = 1
                            while f"{slug}-{count}" in slugs:
                                count += 1
                            slug = f"{slug}-{count}"
                        slugs.add(slug)
                    headings[rel] = slugs
            except BaseException:
                raise

            if self.entrypoint not in found_markdown:
                findings.append(self._finding("structure.entrypoint", "error", self.entrypoint, 1, "Configured Markdown entrypoint was not found."))
            elif self.entrypoint not in bodies:
                findings.append(self._finding("structure.entrypoint-unchecked", "unknown", self.entrypoint, 1, "Configured entrypoint exists but was skipped by a file-size or total-scan limit."))

            links_checked = 0
            for source, lines in bodies.items():
                for line_no, raw, kind in _iter_links(lines):
                    if kind == "wiki":
                        findings.append(self._finding("conventions.wikilinks-unknown", "unknown", source, line_no, "Wiki-style links may depend on aliases or plugins; this target was not judged.", self._context(lines, line_no)))
                        continue
                    try:
                        parts = urlsplit(raw)
                    except ValueError:
                        findings.append(self._finding("links.invalid-reference", "unknown", source, line_no, "Link syntax could not be parsed safely.", self._context(lines, line_no)))
                        continue
                    if parts.scheme or parts.netloc or not parts.path and not parts.fragment:
                        continue
                    decoded = unquote(parts.path)
                    if decoded and not _safe_link_path(decoded):
                        findings.append(self._finding("links.outside-root", "error", source, line_no, "Link leaves the selected directory; target was not opened.", self._context(lines, line_no)))
                        links_checked += 1
                        continue
                    source_dir = posixpath.dirname(source)
                    rel_target = posixpath.normpath(posixpath.join(source_dir, decoded)) if decoded else source
                    if rel_target == ".." or rel_target.startswith("../") or rel_target.startswith("/"):
                        findings.append(self._finding("links.outside-root", "error", source, line_no, "Link leaves the selected directory; target was not opened.", self._context(lines, line_no)))
                        links_checked += 1
                        continue
                    try:
                        kind = self._target_kind(root_fd, rel_target)
                    except OSError:
                        kind = "unavailable"
                    if kind == "missing":
                        findings.append(self._finding("links.missing-target", "error", source, line_no, f"Relative link target is missing: {rel_target or '.'}.", self._context(lines, line_no)))
                    elif kind in ("symlink", "symlink-or-unavailable"):
                        findings.append(self._finding("links.symlink-skipped", "unknown", source, line_no, "Link crosses a symlink; its target was not opened.", self._context(lines, line_no)))
                    elif kind == "unavailable":
                        findings.append(self._finding("links.target-unavailable", "unknown", source, line_no, "Target metadata could not be checked safely.", self._context(lines, line_no)))
                    elif kind == "directory":
                        findings.append(self._finding("links.directory-target-unknown", "unknown", source, line_no, "Directory landing conventions are not configured; this target was not judged.", self._context(lines, line_no)))
                    elif kind == "other":
                        findings.append(self._finding("links.target-type-unknown", "unknown", source, line_no, "Link target is not a regular file; its behavior was not judged.", self._context(lines, line_no)))
                    elif parts.fragment and PurePosixPath(rel_target).suffix.lower() == ".md":
                        fragment = unquote(parts.fragment).lower()
                        if rel_target not in headings:
                            findings.append(self._finding("links.fragment-unchecked", "unknown", source, line_no, "The Markdown target was not scanned, so its heading fragment is unknown.", self._context(lines, line_no)))
                        elif fragment not in headings[rel_target]:
                            severity = "unknown" if rel_target in ambiguous_headings else "warning"
                            evidence = "Heading fragment could not be checked against this target's heading conventions." if severity == "unknown" else f"Heading fragment was not found in {rel_target}."
                            findings.append(self._finding("links.missing-fragment" if severity == "warning" else "links.fragment-convention-unknown", severity, source, line_no, evidence, self._context(lines, line_no)))
                    links_checked += 1

            tracked, is_git = self._tracked(root_fd)
            privacy_hits = 0
            if is_git:
                for rel in tracked:
                    name = PurePosixPath(rel).name.lower()
                    if any(fnmatch.fnmatch(rel.lower(), pattern) or fnmatch.fnmatch(name, pattern.removeprefix("**/")) for pattern in PRIVACY_PATTERNS):
                        findings.append(self._finding("privacy.tracked-path", "warning", rel, 1, "Tracked path matches a default privacy review rule."))
                        privacy_hits += 1
            else:
                findings.append(self._finding("policy.tracking-unknown", "unknown", ".", 0, "Git tracking state is unavailable; tracked-path privacy rules are unknown."))
            findings.append(self._finding("conventions.frontmatter-unknown", "unknown", ".", 0, "Frontmatter, templates, and plugin-specific rules are not configured and were not judged."))
            return self._report(findings, len(bodies), links_checked, len(tracked) if is_git else None, privacy_hits)
        finally:
            os.close(root_fd)

    def _report(self, findings: list[dict], files_checked: int, links_checked: int, tracked_files: int | None, privacy_hits: int) -> dict:
        counts = {key: sum(1 for f in findings if f["severity"] == key) for key in ("error", "warning", "unknown")}
        return {"root_name": self.root.name, "root_path": str(self.root), "entrypoint": self.entrypoint,
                "files_checked": files_checked, "links_checked": links_checked, "tracked_files": tracked_files,
                "privacy_hits": privacy_hits, "counts": counts, "findings": findings, "privacy_rules": list(PRIVACY_PATTERNS),
                "limits": {"markdown_files": MAX_MARKDOWN_FILES, "per_file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES},
                "read_only": True}

    @staticmethod
    def _finding(rule: str, severity: str, source: str, line: int, evidence: str, context: list[dict] | None = None) -> dict:
        finding = {"rule": rule, "severity": severity, "source": source, "line": line, "evidence": evidence}
        if context is not None:
            finding["context"] = context
        return finding
