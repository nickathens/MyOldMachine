# Git

Version control operations with Git and GitHub CLI.

## Capabilities

- **Repository**: Clone, init, status, log
- **Branches**: Create, switch, merge, delete
- **Commits**: Add, commit, amend, revert
- **Remote**: Push, pull, fetch
- **GitHub**: Create PRs, issues, view PRs, releases

## Commands

```bash
# Status and log
git status
git log --oneline -10

# Branching
git checkout -b feature/new-feature
git merge feature/new-feature

# Commits
git add .
git commit -m "message"

# GitHub CLI
gh pr create --title "Title" --body "Description"
gh pr list
gh issue create --title "Bug" --body "Description"
gh repo clone owner/repo
```

## Examples

"Show me the git status"
"Create a new branch called feature/login"
"Commit these changes with message 'Add login feature'"
"Create a pull request for this branch"
"Show recent commits"
"Push to remote"

## Notes

- Always check status before committing
- Use meaningful commit messages
- GitHub CLI (gh) requires authentication: `gh auth login`
