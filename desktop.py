#!/usr/bin/env python3
"""Native macOS shell for Markdown Link Checker."""
from __future__ import annotations

import threading
from pathlib import Path

import AppKit
import webview
from webview.menu import Menu, MenuAction

import server
from scanner import KnowledgeScanner
from desktop_policy import install_policy


def choose_initial_root() -> str | None:
    panel = AppKit.NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(False)
    panel.setCanChooseDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setPrompt_("Scan This Folder")
    return str(panel.URL().path()) if panel.runModal() == AppKit.NSModalResponseOK else None


def _make_local_server(selected_root: str, port: int = 0):
    """Normalize the native picker path to the scanner's Path contract."""
    return server.make_server(Path(selected_root), port=port)


def _scanner_for_selected_root(selected_root: str, entrypoint: str) -> KnowledgeScanner:
    """Normalize the folder-dialog path before creating a replacement scanner."""
    return KnowledgeScanner(Path(selected_root), entrypoint)


def show_error(message: str) -> None:
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_("Markdown Link Checker could not open")
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_("OK")
    alert.runModal()


def run() -> None:
    root = choose_initial_root()
    if not root:
        return
    local_server = None
    server_thread = None
    try:
        local_server = _make_local_server(root, port=0)
        server_thread = threading.Thread(target=local_server.serve_forever, name="knowledge-health-http", daemon=True)
        server_thread.start()
        url = f"http://127.0.0.1:{local_server.server_port}/?desktop=1"
        window = None

        def choose_folder(_window=None):
            if window is None:
                return
            selected = window.create_file_dialog(webview.FOLDER_DIALOG)
            if not selected:
                return
            try:
                replacement = _scanner_for_selected_root(selected[0], local_server.scanner.entrypoint)
            except (OSError, ValueError):
                show_error("That folder cannot be scanned safely. Choose a readable directory and try again.")
                return
            with local_server.state_lock:
                local_server.scanner = replacement
            window.load_url(url)

        menu = [Menu("Markdown Link Checker", [MenuAction("Choose Root Folder…", choose_folder)])]
        window = webview.create_window("Markdown Link Checker", url, width=1080, height=760, min_size=(820, 600), menu=menu)
        install_policy(url)
        webview.start()
    except Exception:
        show_error("The local app failed to open. Restart Markdown Link Checker and try again.")
        raise SystemExit(1)
    finally:
        if local_server is not None:
            if server_thread is not None and server_thread.is_alive():
                local_server.shutdown()
            local_server.server_close()
            if server_thread is not None:
                server_thread.join(timeout=3)


if __name__ == "__main__":
    run()
