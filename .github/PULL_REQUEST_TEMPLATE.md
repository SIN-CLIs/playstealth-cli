<!-- Thanks for contributing! Keep sections short — reviewers read the diff first. -->

## Summary

<!-- What problem does this solve? 1-3 sentences. -->

## Changes

<!-- Bullet list of the meaningful changes. Keep code-style tweaks out unless the PR is explicitly about them. -->

- 
- 
- 

## Why

<!-- Link to the issue, design doc, or describe the root cause. "Just because" is not a reason. -->

## How it was tested

- [ ] `make lint`
- [ ] `make typecheck`
- [ ] `make test`
- [ ] Added/updated tests for new behaviour
- [ ] Tried the legacy shim (`python heypiggy_vision_worker.py`) when touching it

## Risk + rollback

<!-- How do we revert if this breaks prod? Any feature flag / config switch? -->

## Checklist

- [ ] `.env.example` updated when a new env var was introduced
- [ ] `CHANGELOG.md` entry under "Unreleased"
- [ ] Docs (`README.md`, `AGENTS.md`, etc.) updated
- [ ] No secrets/personal data in diff (`make secrets`)
