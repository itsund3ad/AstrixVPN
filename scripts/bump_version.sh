#!/usr/bin/env bash
# Astrix — Version bumper
# Usage: ./scripts/bump_version.sh [major|minor|patch]
#   or:  ./scripts/bump_version.sh 0.2.0

set -euo pipefail

VERSION_FILE="VERSION"

if [ ! -f "$VERSION_FILE" ]; then
    echo "Error: $VERSION_FILE not found" >&2
    exit 1
fi

current=$(cat "$VERSION_FILE" | tr -d ' \n')
major=$(echo "$current" | cut -d. -f1)
minor=$(echo "$current" | cut -d. -f2)
patch=$(echo "$current" | cut -d. -f3)

if [ $# -eq 0 ]; then
    echo "Usage: $0 [major|minor|patch]"
    echo "  or:   $0 X.Y.Z"
    echo ""
    echo "Current version: $current"
    exit 0
fi

case "$1" in
    major)
        major=$((major + 1))
        minor=0
        patch=0
        ;;
    minor)
        minor=$((minor + 1))
        patch=0
        ;;
    patch)
        patch=$((patch + 1))
        ;;
    *)
        if echo "$1" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
            new_version="$1"
        else
            echo "Error: invalid version '$1'. Use major|minor|patch or X.Y.Z" >&2
            exit 1
        fi
        ;;
esac

new_version="${new_version:-${major}.${minor}.${patch}}"

# Update VERSION file
echo "$new_version" > "$VERSION_FILE"

# Update pyproject.toml files
for f in astrix-client/pyproject.toml astrix-server/pyproject.toml; do
    if [ -f "$f" ]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/^version = \".*\"/version = \"$new_version\"/" "$f"
        else
            sed -i "s/^version = \".*\"/version = \"$new_version\"/" "$f"
        fi
        echo "Updated $f → $new_version"
    fi
done

echo ""
echo "Version bumped: $current → $new_version"
echo ""
echo "Commit with: git add . && git commit -m \"chore: bump version to ${new_version}\""
echo "Tag with:    git tag v${new_version}"
