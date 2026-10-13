# ADR-0005: Where this repo differs from the maintainer's earlier projects

## Context
verdrift, layerdelta and lastgreen kept their mission brief in a workspace folder and named
`thanatchon36` as the copyright holder. upfront, built with the same brief as this project,
keeps `MISSION.md` in the repo and names "Thanatchon" in the LICENSE.

## Decision
Follow upfront: `MISSION.md` at the repository root (it doubles as the design doc), LICENSE
holder "Thanatchon". Keep the rest of the house style: commits under the GitHub noreply
address, Actions pinned to commit SHAs with `persist-credentials: false`, a CI job that runs
the README example and the issue-named examples, a generated `docs/api.md` checked in CI, and a
README with "How it works" and "Limitations" sections.

## Consequences
The LICENSE holder string differs from the three older repos. All refer to the same person.
