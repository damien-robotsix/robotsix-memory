# Agent conventions

Repository-specific rules for agents working in this repository.

## CI workflows

- Before adding a `dependency-review` gate to a repository, confirm that
  Dependency graph is enabled under Settings → Code security; otherwise the
  check fails on every pull request with "Dependency review is not supported
  on this repository".
