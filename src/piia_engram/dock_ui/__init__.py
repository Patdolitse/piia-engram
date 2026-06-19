"""Engram Dock GUI: a local, loopback-only browser GUI to view, edit, and archive
lessons/decisions and carry cross-tool context (接续), without the CLI. Archive is a
reversible soft-archive, not a delete. Playbooks / settings / bulk + restore are
planned, not yet wired.

Security-first: a local HTTP server on the user's private memory store. The gate is
the HTTP layer itself (Codex Option A): one-time token -> server-side session
(HttpOnly + SameSite=Strict, server-enforced TTL), Host allowlist (DNS-rebinding
defense), and Origin + CSRF on writes — an authenticated dock session IS the local
owner. A refused request returns before any writable Engram is opened (zero
side-effect). See ``dock_ui.security`` + ``dock_ui.app``; design in
engram-dock-gui-design.md ("Build 1 安全实现方案").
"""
