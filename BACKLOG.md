# Backlog

Every expansion idea that arrives during a milestone lands here and is not
built. Scope creep is what kills tools like this one.

## Deferred from v1

- GitHub Action that comments the staleness report on pull requests.
- Languages beyond Python and TypeScript/JavaScript.
- Monorepo support (multiple packages, workspace resolution).
- Function-level analysis. v1 stays at file level deliberately.
- Private repositories and authentication.

## Rejected on principle — do not revisit

- A question-and-answer interface over the repository. That is DeepWiki's
  product, and building it would delete the reason this tool exists.
- Letting the model select or order stations. The ordering being computed
  rather than opined is the entire thesis.
- A hosted website or database. No server in v1.
