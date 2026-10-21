# Support

How to get help with Nojoin, and what to expect from the maintainer.

## Before You Ask

- Read the [documentation index](../docs/README.md), which answers most setup, deployment, capture and usage questions.
- Search [existing issues](https://github.com/Valtora/Nojoin/issues).
- Reproduce the problem on the latest release.

## Where to Go

- **Questions:** ask in [Discussions](https://github.com/Valtora/Nojoin/discussions) under Q&A.
- **Bugs and regressions:** open a bug report.
- **Browser-capture or platform problems:** open a platform compatibility report.
- **Feature ideas:** open a feature request.
- **Security vulnerabilities:** never a public issue. Use GitHub Private Vulnerability Reporting as described in the [security policy](../docs/SECURITY.md).
- **Code of Conduct concerns:** follow the private reporting process in the [Code of Conduct](../docs/CODE_OF_CONDUCT.md).

## Supported Versions

Nojoin is in active development and should be considered pre-release. Only the latest release is supported, as in the [security policy](../docs/SECURITY.md), so report issues against it.

## Maintainer Response and Triage

Nojoin is maintained by one person on a best-effort basis.

- **Security reports:** acknowledged within 48 hours, per the [security policy](../docs/SECURITY.md).
- **Bug and platform reports:** triaged as time permits. Reproduction steps, the version, the deployment mode and redacted logs speed this up.
- **Feature requests:** read and considered, with no promise of acceptance or a date.
- **Pull requests:** reviewed as time allows. A pull request that passes the required checks and updates the affected documentation is the quickest to merge.

Apart from the security acknowledgement, there is no response-time commitment.

## Triage Cadence and Labels

- **Weekly:** sweep new issues and pull requests. Apply severity, scope, platform (where relevant) and release-impact labels, and remove `needs-triage`, which every issue template adds. Security reports follow the 48-hour commitment instead.
- **Per release:** before tagging, review open `severity:critical` and `severity:high` issues and any `release:breaking` or `release:migration-required` items, so the release notes and upgrade guidance are accurate.
- **Quarterly:** the [repository re-audit](CONTRIBUTING.md#periodic-re-audit) also reviews stale and unlabelled issues.

[labels.yml](labels.yml) defines the label set: severity, scope (mirroring [CODEOWNERS](CODEOWNERS)), platform, release impact, workflow and type. GitHub's other default labels and the ecosystem labels Dependabot adds (`docker`, `github_actions`, `javascript`, `python`) are not managed there.

### Applying the Labels

Labels live in the GitHub repository settings. This creates or updates every label in `labels.yml` with an authenticated `gh`, and is safe to re-run after each change to the file:

```bash
python3 - <<'EOF'
import re, subprocess
text = open(".github/labels.yml", encoding="utf-8").read()
pattern = r'- name: "([^"]+)"\n  color: "([0-9a-f]{6})"\n  description: "([^"]*)"'
for name, color, description in re.findall(pattern, text):
    subprocess.run(["gh", "label", "create", name, "--color", color,
                    "--description", description, "--force"], check=True)
EOF
```
