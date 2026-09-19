# Contributing

Contributions are welcome, and they are greatly appreciated! Every little helps, and credit will always be given.

You can contribute in many ways:

## Types of Contributions

### Report Bugs

Report bugs to [our issue page][gh-issues]. If you are reporting a bug, please include:

- Your operating system name and version.
- Any details about your local setup that might be helpful in troubleshooting.
- Detailed steps to reproduce the bug.

### Fix Bugs

Look through the GitHub issues for bugs. Anything tagged with "bug" and "help wanted" is open to whoever wants to implement it.

### Implement Features

Look through the GitHub issues for features. Anything tagged with "enhancement" and "help wanted" is open to whoever wants to implement it.

### Write Documentation

AC Infinity BLE could always use more documentation, whether as part of the official AC Infinity BLE docs, in docstrings, or even on the web in blog posts, articles, and such.

### Submit Feedback

The best way to send feedback [our issue page][gh-issues] on GitHub. If you are proposing a feature:

- Explain in detail how it would work.
- Keep the scope as narrow as possible, to make it easier to implement.
- Remember that this is a volunteer-driven project, and that contributions are welcome 😊

## Get Started!

Ready to contribute? Here's how to set yourself up for local development.

1. Fork the repo on GitHub.

2. Clone your fork locally:

   ```shell
   $ git clone git@github.com:your_name_here/ac-infinity-ble.git
   ```

3. Install the project dependencies with [Poetry](https://python-poetry.org):

   ```shell
   $ poetry install
   ```

4. Create a branch for local development:

   ```shell
   $ git checkout -b name-of-your-bugfix-or-feature
   ```

   Now you can make your changes locally.

5. When you're done making changes, check that your changes pass our tests:

   ```shell
   $ poetry run pytest
   ```

6. Linting is done through [pre-commit](https://pre-commit.com). Provided you have the tool installed globally, you can run them all as one-off:

   ```shell
   $ pre-commit run -a
   ```

   Or better, install the hooks once and have them run automatically each time you commit:

   ```shell
   $ pre-commit install
   ```

7. Commit your changes and push your branch to GitHub:

   ```shell
   $ git add .
   $ git commit -m "feat(something): your detailed description of your changes"
   $ git push origin name-of-your-bugfix-or-feature
   ```

   Note: the commit message should follow [the conventional commits](https://www.conventionalcommits.org). We run [`commitlint` on CI](https://github.com/marketplace/actions/commit-linter) to validate it, and if you've installed pre-commit hooks at the previous step, the message will be checked at commit time.

8. Submit a pull request through the GitHub website or using the GitHub CLI (if you have it installed):

   ```shell
   $ gh pr create --fill
   ```

## Pull Request Guidelines

We like to have the pull request open as soon as possible, that's a great place to discuss any piece of work, even unfinished. You can use draft pull request if it's still a work in progress. Here are a few guidelines to follow:

1. Include tests for feature or bug fixes.
2. Update the documentation for significant features.
3. Ensure tests are passing on CI.

## Tips

To run a subset of tests:

```shell
$ poetry run pytest tests
```

## Making a new release

A push to `main` runs CI. After the required checks pass, the release job uses
Python Semantic Release 10.6.2 to determine the version, build the distributions,
create the tag and GitHub release, and publish to PyPI using `PYPI_TOKEN`.
The job uses the `release` GitHub environment.

Use conventional commits: `feat:` advances the minor version, `fix:` advances
the patch version, and a `BREAKING CHANGE:` footer advances the major version.
Keep that footer in the final squash commit when a PR changes the public API.
Releases start at 1.0.0; zero-major versions are disabled.

Before merging a release PR:

1. Wait for all PR checks to pass and review the upgrade instructions and hardware
   coverage in the documentation.
2. Confirm the repository or `release` environment has a valid `PYPI_TOKEN` for
   this package, and that `GITHUB_TOKEN` can push release commits and tags to
   `main`. Branch protection must permit the release job's push.
3. Mark the PR ready and merge it, preserving its conventional commit message and
   breaking-change footer. The push to `main` starts the release automatically;
   approve the `release` environment if it has required reviewers.
4. Check the CI release job, the new GitHub tag/release, and the PyPI version.
   Install that exact version into a fresh environment before updating consumers.

Preview the next release from `main` without making changes:

```shell
poetry run semantic-release --noop version --no-push --no-vcs-release
```

The preview does not publish anything. Do not create the tag or GitHub release
manually: the release job owns version files, changelog, builds and tagging.
If PyPI upload fails after tagging, recover the tagged distributions and complete
that upload; a rerun may see an already released version and skip publishing.
Publish the library before releasing an integration that pins its new version.

[gh-issues]: https://github.com/hunterjm/ac-infinity-ble/issues

## Development checks

Use Python 3.12+ and Poetry 2.4.3. Ruff owns Python formatting, import sorting,
syntax upgrades and linting; Black, isort, Flake8 and pyupgrade hooks are replaced.
Prettier still formats Markdown, YAML, JSON and JavaScript configuration. Ruff's
lint baseline selects core pycodestyle errors (`E4`, `E7`, `E9`), Pyflakes (`F`),
import sorting (`I`), Python upgrades (`UP`), bugbear (`B`), and unused suppressions
(`RUF100`). Formatting owns whitespace; mypy owns types; Bandit remains separate.
The baseline avoids enabling unrelated or conflicting rules through `ALL`.

```shell
poetry install --with docs
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src tests examples
poetry run pytest --cov=ac_infinity_ble
poetry run sphinx-build -W --keep-going -b html docs docs/_build/html
poetry check --lock
poetry build
```

CI tests Python 3.12, 3.13 and 3.14 on Linux and Windows, then imports a built wheel
from outside the source checkout. Home Assistant tests live in the integration
repository and run against HA 2026.9.3 with mocked transport; live adapter/proxy
validation remains separate.
