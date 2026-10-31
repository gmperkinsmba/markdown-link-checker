# Context

- A selected root is the only repository scanned during a server session.
- The root and entrypoint are chosen at startup, not through a browser request.
- Markdown source files are opened read-only; the scanner creates no files in the selected root.
- A finding records a rule, severity, relative source path, line, and evidence.
- Link checks cover relative file targets and GitHub-style Markdown heading fragments.
- Git tracking checks are unknown when the selected root is not a Git repository.
- Default privacy rules match tracked paths whose names suggest secrets, credentials, private material, exports, or environment files.
- Wiki links, frontmatter, templates, and plugin conventions remain unknown until configured.
