# Support

How to get help with Nojoin, and what to realistically expect from the maintainers.

## Before You Ask

- Read the [documentation index](../docs/README.md); most setup, deployment, capture, and usage questions are answered there.
- Search [existing issues](https://github.com/Valtora/Nojoin/issues) to avoid duplicates.
- Reproduce the problem on the latest released version (see the **Supported Versions** note below).

## Where to Go

- **Bugs and regressions:** open a bug report.
- **Browser-capture or platform problems:** open a platform compatibility report.
- **Feature ideas:** open a feature request.
- **Security vulnerabilities:** do not open a public issue. Use GitHub Private Vulnerability Reporting as described in the [security policy](../docs/SECURITY.md).
- **Code of Conduct concerns:** follow the private reporting process in the [Code of Conduct](../docs/CODE_OF_CONDUCT.md).

## Supported Versions

Nojoin is in active development and should be considered pre-release. Only the latest released version is supported, matching the [security policy](../docs/SECURITY.md). Please reproduce and report issues against the latest version.

## Maintainer Response and Triage

Nojoin is maintained on a best-effort basis by a small team. To set honest expectations:

- **Security reports:** initial acknowledgement within 48 hours, per the [security policy](../docs/SECURITY.md).
- **Bug and platform reports:** triaged and labelled as maintainer time permits. Clear reproduction steps, version, deployment mode, and redacted logs speed this up considerably.
- **Feature requests:** read and considered, but acceptance and scheduling are not guaranteed.
- **Pull requests:** reviewed when maintainer time allows. PRs that pass all required checks and update the affected documentation are the easiest and fastest to merge.

Outside the security acknowledgement window, there is no guaranteed response-time commitment. Well-scoped, reproducible reports and self-contained pull requests receive attention soonest.

## Triage Cadence and Labels

Nojoin is single-maintainer, so triage is a regular self-applied pass rather than a rota. The cadence is deliberately modest so it can be met honestly:

- **Weekly:** sweep new issues and pull requests. Apply a severity, scope, platform (where relevant), and release-impact label, and remove `needs-triage`. Security reports are handled out-of-band on the 48-hour acknowledgement commitment in the [security policy](../docs/SECURITY.md), not on this weekly cadence.
- **Per release:** before tagging, review open `severity:critical` and `severity:high` issues and any `release:breaking` or `release:migration-required` items so release notes and the upgrade guidance are accurate.
- **Quarterly:** the repository-quality re-audit (see [repo-maintenance.md](../docs/repo-maintenance.md)) also reviews stale and unlabelled issues.

New issues opened from the templates should arrive with `needs-triage`; the weekly sweep replaces it with the substantive labels.

### Label Taxonomy

The canonical label set is defined in [labels.yml](labels.yml), grouped as:

- **Severity:** `severity:critical`, `severity:high`, `severity:medium`, `severity:low`.
- **Scope:** `scope:backend`, `scope:worker`, `scope:frontend`, `scope:capture`, `scope:migration`, `scope:deployment`, `scope:docs`, `scope:security` (mirrors [CODEOWNERS](CODEOWNERS)).
- **Platform:** `platform:windows`, `platform:linux`, `platform:macos`, `platform:android`, `platform:ios`, plus the `platform-issue` triage label.
- **Release impact:** `release:breaking`, `release:migration-required`, `release:safe`.
- **Workflow:** `needs-triage`, `flaky`, `slow-test`, `dependencies`, `audit`.

### Applying the Labels (maintainer-action-pending)

Labels exist only in GitHub repository settings, so `labels.yml` must be applied with the GitHub CLI. From a checkout with the project virtual environment active (it provides PyYAML) and an authenticated `gh`:

```bash
source .venv/bin/activate
python3 - <<'PY' | bash
import shlex
import yaml

for label in yaml.safe_load(open(".github/labels.yml")):
    print(
        "gh label create {name} --color {color} --description {desc} --force".format(
            name=shlex.quote(label["name"]),
            color=shlex.quote(label["color"]),
            desc=shlex.quote(label["description"]),
        )
    )
PY
```

`gh label create --force` creates the label or updates it in place, so the command is safe to re-run whenever `labels.yml` changes. This step is a one-time (then on-change) maintainer action and is not enforced from the repository tree.
