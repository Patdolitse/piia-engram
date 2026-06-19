"""Engram Dock GUI (Build 1+): a local, loopback-only browser GUI to view/edit/
delete lessons/playbooks + settings without the CLI.

Security-first: a local HTTP server on the user's private memory store. The
security gate (one-time token -> server-side session, CSRF, Host/Origin allowlist,
owner governance gate) is the contract; see ``dock_ui.security`` + ``dock_ui.app``.
Design locked with Codex (engram-dock-gui-design.md, "Build 1 安全实现方案").
"""
