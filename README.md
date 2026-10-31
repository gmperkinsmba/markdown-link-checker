# Markdown Link Checker

**First principle:** Find broken Markdown links and risky tracked paths without changing source files.

Markdown Link Checker scans one selected folder. It reports missing entrypoints, broken relative links, missing heading fragments, and tracked paths that match default privacy review rules. It reads source files only. A CSV is written only when you export a review.

## Install the native Mac app

Build and install with Python 3.12 on macOS:

```sh
source .desktop-venv/bin/activate
python build_desktop.py
python install_desktop.py
```

The bundle is ad-hoc signed; it is not Developer ID signed or notarized. To uninstall, quit the app and remove `~/Applications/Markdown Link Checker.app`. Build output stays in `dist/`.

## Use it

Launch **Markdown Link Checker** from Applications. Choose a folder in the startup picker, then select **Scan folder**. Use **Markdown Link Checker → Choose Root Folder…** to switch folders; a change clears old results. **Export review CSV** opens the native save panel. Quit with ⌘Q.

The app uses a local loopback server on a temporary port and does not retain scan results or settings after quit.

## Browser development

For local development, use Python 3.10 or later:

```sh
python3 server.py --root /path/to/markdown-vault --entrypoint README.md
```

Open `http://127.0.0.1:8346`. The selected root is fixed for that server session. Stop it with Ctrl+C.

## Tests

```sh
node tests/test_csv.js
python3 -m unittest discover -s tests -v
```

## Rules and data

The scanner checks relative file targets, GitHub-style Markdown heading fragments, and tracked paths matching default patterns for `.env`, secret, credential, private, or export names. A match is for review; the name alone does not prove sensitive content. Wiki links, frontmatter, templates, plugin rules, and tracking in non-Git folders may be unknown. Symlinks, outside-root links, and files over 1 MB are not opened. Each scan is read-only.
