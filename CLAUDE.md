# Project rules

See [plan.md](plan.md) for the goal, stack, phases and repo layout, and [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for how the code works.

## Audience

The code and docs are written so that beginners and intermediate developers can understand everything that has been built. Explain the why, define terms the first time they appear (checkpointer, migration, interrupt...), and prefer plain words.

## File headers

Every file starts with a description, in the file's own comment syntax, that lets a beginner understand the file without reading the code:

1. What the file is and what it does (first line, starting with the file's role).
2. Where it fits: what uses it, and what it uses (files, nodes, routes, services).
3. Key ideas a beginner needs to follow it, explained briefly (e.g. "a migration is...").
4. How to run, use or change it, when that applies (commands, which file to edit next).

Usually 3-15 lines; a tiny file needs less. Comment syntax:

- Python: a module docstring (`"""..."""`).
- YAML, `.ini`, Dockerfile, `.env*`, `.gitignore`, `.dockerignore`, `requirements.txt`, PowerShell: `#` comments.
- Mako templates: `##` comments (stripped from the output).
- Prompt files (`prompts/*.md`): a `<!-- ... -->` block; the loader strips it before sending to Claude, so it can be detailed. Say which node uses it, as system or user prompt, the answer shape and the placeholders.
- HTML: a `<!-- ... -->` block (before `<!doctype>`); CSS and JavaScript: a `/* ... */` block.
- Other Markdown: a title and a purpose line are enough.
- JSON and other formats without comments (e.g. `langgraph.json`): describe the file in HOW_IT_WORKS.md instead.
- Sample documents (`samples/`): one first line saying they are fictional (the text is sent to Claude).

Keep the header accurate whenever the file changes.

## HOW_IT_WORKS.md

The guide to the whole codebase for beginners and intermediates. Update it in the same change whenever the flow, a file's role, a command, the data model or the stack changes, and at the end of each phase (update "Where the project is").

## Commits

Commit as `antoine-demyttenaere` (set in this repo's local git config).
