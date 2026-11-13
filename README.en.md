# STE80 Humanizer RU

Russian-first Agent Skill for clear editing without invented facts or erased
voice. Version 1.0.0. Includes a secondary English profile and mixed-language
editing without automatic translation.

This is an instruction package plus optional Python diagnostics, not a model or
an automatic rewriting program. It does not detect authorship, promise detector
evasion, or certify ASD-STE100 compliance. The number 80 refers to its origin,
not a measured quality score.

## Install and invoke

From the extracted package directory:

```bash
npx skills add . --global --agent codex --copy
```

The command requires Node.js/npm. Other Skills CLI targets include `claude-code`
and `cursor`. Live integration tests in these clients have not been performed.
For Codex, manual installation is also possible under
`$HOME/.agents/skills/ste80-humanizer-ru` without overwriting an existing directory.
Invoke `$ste80-humanizer-ru` with your text and editing requirements.

Modes: `light`, `humanize`, `strict`, `review`. Genres: documentation, web,
social, email, business, UX, creative. Facts and meaning always outrank style.
The main skill and examples are written in Russian.

## Offline diagnostics and tests

Python 3.10+, standard library only:

```bash
python scripts/humanizer_check.py lint examples/before.md
python scripts/humanizer_check.py compare examples/before.md examples/after.md --format json
python scripts/validate_repo.py
python -m unittest discover -s tests -v
python scripts/run_evals.py
```

The checker is read-only and uses no network. It does not implement a complete
Markdown parser or semantic equivalence checker. All comparisons require manual
review even when no issue is reported. See [test report](TEST_REPORT.md).

The 50 synthetic reference cases validate fixtures and heuristic invariants, not
model performance. The 12 activation prompts are a manual evaluation set, not
an executed model test. See [evaluation protocol](references/evaluation.md).

## Publish explicitly

Install Git and GitHub CLI; authenticate using `gh auth login --hostname github.com`.
`python scripts/publish.py` only prints a plan. Add `--execute` to create a NEW
PUBLIC repository under the authenticated personal account, run offline checks,
push allowlisted files, and verify visibility and commit SHA. Defaults:
`lev4ik52/ste80-humanizer-ru`. Existing repositories are never overwritten.

The package does not assert that the repository has already been published.
Remote GitHub Actions and application integration tests are separate steps.

## Attribution

Derived from [Dodaios/ste80-humanizer](https://github.com/Dodaios/ste80-humanizer),
reviewed at commit `99bc468482599f5d75f595e2c1adf893ebff4d8f` (v4.0.0).
[MIT license](LICENSE); inherited [third-party notices](THIRD_PARTY_NOTICES.md)
are retained. This is an independent derivative, not an official upstream release.

[Full Russian documentation](README.md) · [Provenance](UPSTREAM.md) ·
[Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)
