set -euo pipefail

# Where to clone this repo from and to, when running with no local
# backing file (e.g. curl | bash, where the script has no sibling
# neuralyzer.py to symlink from). Overridable for testing.
NEURALYZER_REPO_URL="${NEURALYZER_REPO_URL:-https://github.com/mjonuschat/moonraker-neuralyzer.git}"
NEURALYZER_CLONE_DEST="${NEURALYZER_CLONE_DEST:-$HOME/moonraker-neuralyzer}"

# script_dir dies when there's no real file backing this script (curl |
# bash reads from stdin, so BASH_SOURCE has no file to point at). That
# failure is expected there, not a bug, so it's caught rather than
# left to trip set -e.
REPO_ROOT="$(script_dir 2>/dev/null)" || REPO_ROOT=""
if [ -z "$REPO_ROOT" ]; then
    log "no local checkout found; cloning $NEURALYZER_REPO_URL to $NEURALYZER_CLONE_DEST"
    git_ensure_clone "$NEURALYZER_REPO_URL" "$NEURALYZER_CLONE_DEST" "neuralyzer.py"
    REPO_ROOT="$NEURALYZER_CLONE_DEST"
fi

MOONRAKER_ROOT="${1:-$HOME/moonraker}"

COMPONENTS_DIR="$MOONRAKER_ROOT/moonraker/components"
TARGET="$COMPONENTS_DIR/neuralyzer.py"
EXCLUDE_FILE="$MOONRAKER_ROOT/.git/info/exclude"
EXCLUDE_LINE="moonraker/components/neuralyzer.py"

if [ ! -d "$COMPONENTS_DIR" ]; then
    die "$COMPONENTS_DIR does not exist (is the moonraker root path argument correct? usage: install.sh [path-to-moonraker-checkout])"
fi

link_safe "$REPO_ROOT/neuralyzer.py" "$TARGET"
log "symlinked $TARGET -> $REPO_ROOT/neuralyzer.py"

config_block_ensure "$EXCLUDE_FILE" neuralyzer "$EXCLUDE_LINE"
log "excluded $EXCLUDE_LINE in $EXCLUDE_FILE"

log "Add a [neuralyzer] section to moonraker.conf, then restart Moonraker."
