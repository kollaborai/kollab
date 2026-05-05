# Homebrew Tap Notes

Maintainer notes for publishing the [Kollab](https://github.com/kollaborai/kollab)
Homebrew formula after a PyPI release exists.

Do not advertise Homebrew installation until the release wheel is uploaded and
the formula has a real SHA256 digest.

## Updating For New Releases

When releasing a new version of Kollab:

1. Publish the `kollab` wheel to PyPI.
2. Generate or update `Formula/kollab.rb` with the PyPI wheel URL.
3. Verify the SHA256 checksum is real, not a placeholder.
4. Commit the formula in the public tap repository.

Find the PyPI URL and SHA256:

```bash
# Get the latest version info from PyPI
curl -s https://pypi.org/pypi/kollab/json | jq -r '.urls | .[] | select(.packagetype == "sdist") | "\(.url) \(.digests.sha256)"'
```

Or use the helper script:

```bash
./update-formula.sh 1.0.0
```
