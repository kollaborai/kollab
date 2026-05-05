#!/usr/bin/env bash
# Update Homebrew formula for a new Kollab release

set -euo pipefail

VERSION="${1:-}"

if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 0.4.12"
    exit 1
fi

# Remove 'v' prefix if present
VERSION="${VERSION#v}"

echo "Fetching PyPI info for kollab==$VERSION..."

PYPI_INFO=$(curl -s "https://pypi.org/pypi/kollab/json")

# Get the wheel URL and SHA256 (prefer wheel over sdist for Homebrew)
RELEASE_INFO=$(echo "$PYPI_INFO" | jq -r --arg version "$VERSION" '
  .urls |
  map(select(.packagetype == "bdist_wheel" and .version == $version)) |
  .[0]
')

# Fall back to sdist if wheel not found
if [[ "$RELEASE_INFO" == "null" ]]; then
    echo "Wheel not found, trying sdist..."
    RELEASE_INFO=$(echo "$PYPI_INFO" | jq -r --arg version "$VERSION" '
      .urls |
      map(select(.packagetype == "sdist" and .version == $version)) |
      .[0]
    ')
fi

if [[ "$RELEASE_INFO" == "null" ]]; then
    echo "Error: Version $VERSION not found on PyPI"
    echo "Available versions:"
    echo "$PYPI_INFO" | jq -r '.releases | keys[]' | sort -V
    exit 1
fi

URL=$(echo "$RELEASE_INFO" | jq -r '.url')
SHA256=$(echo "$RELEASE_INFO" | jq -r '.digests.sha256')

echo "Found release:"
echo "  URL: $URL"
echo "  SHA256: $SHA256"
echo

# Update the formula file
FORMULA_FILE="Formula/kollab.rb"
mkdir -p "$(dirname "$FORMULA_FILE")"

cat > "$FORMULA_FILE" <<FORMULA
# typed: strict
# frozen_string_literal: true

class Kollab < Formula
  desc "Terminal AI workspace with hooks, plugins, providers, and agents"
  homepage "https://github.com/kollaborai/kollab"
  url "$URL"
  sha256 "$SHA256"
  license "MIT"

  depends_on "python@3.12"

  def install
    system "pip3", "--python", Formula["python@3.12"].opt_bin/"python3",
           "install", *std_pip_args(build_isolation: true), "--prefix=#{prefix}", "./"
    bin.install_symlink libexec/"bin/kollab" => "kollab"
  end

  test do
    system bin/"kollab", "--version"
  end
end
FORMULA

echo "Updated $FORMULA_FILE"
echo
echo "To apply changes:"
echo "  1. Commit: git add Formula/kollab.rb && git commit -m \"Update to $VERSION\""
echo "  2. Push: git push"
echo "  3. Users run: brew upgrade kollab"
