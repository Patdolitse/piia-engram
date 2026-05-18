# Contributing to Engram

Thanks for considering a contribution to Engram.

Engram is a local-first memory layer for AI coding tools. The project values small, reviewable changes, clear documentation, and user-owned data.

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING.zh-CN.md)

## Development Setup

```bash
git clone https://github.com/<your-username>/engram.git
cd engram
pip install -e ".[dev]"
```

## Run Tests

```bash
python -m pytest tests/ -v
```

## Code Guidelines

- Use Python 3.10+.
- Keep changes focused and easy to review.
- Prefer readable code over clever abstractions.
- Add or update tests when behavior changes.
- Preserve local-first, user-editable data as a core design principle.

## Pull Requests

1. Fork the repository.
2. Create a feature branch:

```bash
git checkout -b feature/your-feature
```

3. Make your changes.
4. Run the test suite.
5. Open a pull request and explain what changed and why.

## Reporting Issues

When opening an issue, please include:

- operating system
- Python version
- steps to reproduce
- expected behavior
- actual behavior

## Conduct

Be respectful, practical, and constructive. The goal is to make AI tools remember people better while keeping that memory under the user's control.
