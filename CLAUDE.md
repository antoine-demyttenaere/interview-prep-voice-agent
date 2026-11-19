# Project rules

See [plan.md](plan.md) for the goal, stack, phases and repo layout.

## File headers

Every file starts with a short description (1-3 lines) of what it does and why it exists, in the file's own comment syntax:

- Python: a module docstring (`"""..."""`).
- YAML, `.ini`, Dockerfile, `.env*`, `.gitignore`, `.dockerignore`, `requirements.txt`, PowerShell: `#` comments.
- Mako templates: `##` comments (stripped from the output).
- HTML / Markdown: `<!-- ... -->` (Markdown that already opens with a title and purpose line needs nothing more).
- JSON and other formats without comments: skip, and describe the file in the nearest README instead.

Keep the header accurate when the file changes.

## Commits

Commit as `antoine-demyttenaere` (set in this repo's local git config).
