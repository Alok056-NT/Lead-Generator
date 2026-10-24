#!/usr/bin/env bash
# Lead Generator installer for macOS and Linux.
#
# One-line install (paste into Terminal):
#   curl -fsSL https://raw.githubusercontent.com/Alok056-NT/Lead-Generator/main/install.sh | bash
#
# Safe to run again: it upgrades the existing installation in place.
#
# Optional settings (set before running, for example: LEADGEN_REF=v1.0.0 bash install.sh):
#   LEADGEN_REF         branch or tag to install (default: main)
#   LEADGEN_SOURCE      install from this pip source instead (a folder or URL; for testing)
#   LEADGEN_VENV        where to put the private Python environment
#   LEADGEN_YES=1       answer "yes" to the Claude Desktop question
#   LEADGEN_NO_CONNECT=1  do not offer to connect Claude Desktop

set -u

REPO="Alok056-NT/Lead-Generator"
REF="${LEADGEN_REF:-main}"
VENV_DIR="${LEADGEN_VENV:-$HOME/.local/share/LeadGenerator/venv}"
OS="$(uname -s)"

say() { printf '%s\n' "$*"; }
fail() { printf '\n%s\n\n' "$*" >&2; exit 1; }

python_ok() {
    # True when "$1" is Python 3.11 or newer.
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

say ""
say "Lead Generator installer"
say "------------------------"

# 1. Python
if ! PYTHON="$(find_python)"; then
    say ""
    say "Python 3.11 or newer is needed and was not found." >&2
    say "I will not install system software without asking you. To install Python:" >&2
    say ""
    if [ "$OS" = "Darwin" ]; then
        say "    brew install python@3.12"
        say "    (no Homebrew? get Python from https://www.python.org/downloads/)"
    else
        say "    sudo apt install python3 python3-venv     (Debian, Ubuntu)"
        say "    sudo dnf install python3                   (Fedora)"
    fi
    say ""
    say "Then open a new Terminal window and run the installer again."
    exit 1
fi
say "Python found: $("$PYTHON" --version 2>&1)"

# 2. Private environment (keeps this away from your other Python programs)
VENV_PY="$VENV_DIR/bin/python"
if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import sys' >/dev/null 2>&1; then
    say "Using the existing environment in $VENV_DIR (upgrading in place)."
else
    say "Creating the private Python environment in $VENV_DIR ..."
    mkdir -p "$(dirname "$VENV_DIR")"
    if ! "$PYTHON" -m venv --clear "$VENV_DIR"; then
        if [ "$OS" = "Linux" ]; then
            fail "I could not create the Python environment. On Debian or Ubuntu run: sudo apt install python3-venv"
        fi
        fail "I could not create the Python environment. Check that you can write to $(dirname "$VENV_DIR")."
    fi
fi

# 3. Install / upgrade Lead Generator
"$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1
if [ -n "${LEADGEN_SOURCE:-}" ]; then
    REQUIREMENT="$LEADGEN_SOURCE"
elif command -v git >/dev/null 2>&1; then
    REQUIREMENT="lead-generator @ git+https://github.com/$REPO.git@$REF"
else
    # No git on this computer: GitHub's zip download works without it.
    REQUIREMENT="lead-generator @ https://github.com/$REPO/archive/$REF.zip"
fi
say "Installing Lead Generator (this can take a few minutes the first time) ..."
# Remove the old copy first so an upgrade always takes the new files (its libraries stay).
"$VENV_PY" -m pip uninstall --yes lead-generator >/dev/null 2>&1
if ! "$VENV_PY" -m pip install --quiet --upgrade "$REQUIREMENT"; then
    fail "The installation failed. Check your internet connection and run the installer again."
fi
LEADGEN="$VENV_DIR/bin/leadgen"
[ -x "$LEADGEN" ] || fail "The installation finished but the leadgen program is missing. Run the installer again."

# 4. Health check
say ""
say "Checking the installation ..."
if "$LEADGEN" doctor; then DOCTOR_OK=1; else DOCTOR_OK=0; fi

# 4b. A "Lead Generator" button on the Desktop (macOS)
if [ "$OS" = "Darwin" ] && [ -d "$HOME/Desktop" ]; then
    BUTTON="$HOME/Desktop/Lead Generator.command"
    printf '#!/bin/bash\n"%s" app\n' "$LEADGEN" > "$BUTTON"
    chmod +x "$BUTTON"
    say ""
    say "Created the Lead Generator button on your Desktop."
fi

# 5. Offer to connect Claude Desktop (macOS only: its settings location on Linux is not documented)
if [ "$OS" = "Darwin" ] && [ -z "${LEADGEN_NO_CONNECT:-}" ]; then
    say ""
    ANSWER="y"
    if [ -z "${LEADGEN_YES:-}" ]; then
        if [ -r /dev/tty ]; then
            printf 'Connect Claude Desktop to Lead Generator now? (Y/n) '
            read -r REPLY </dev/tty || REPLY=""
            case "$REPLY" in [nN]*) ANSWER="n" ;; esac
        else
            ANSWER="n"
        fi
    fi
    if [ "$ANSWER" = "y" ]; then
        "$LEADGEN" connect claude
    else
        say "Skipped. You can do it later with:"
        say "    \"$LEADGEN\" connect claude"
    fi
fi

# 6. Next steps
say ""
say "=============================================================="
if [ "$DOCTOR_OK" = "1" ]; then
    say "Lead Generator is installed."
else
    say "Lead Generator is installed, but the health check found a problem (see above)."
fi
say "=============================================================="
say ""
say "What to do next:"
say "  - To open the app: double-click Lead Generator on your Desktop (Mac), or run: \"$LEADGEN\" app"
if [ "$OS" = "Darwin" ]; then
    say "  1. If Claude Desktop is open, quit it completely (Claude menu, then Quit) and open it again."
    say "  2. In a new chat, ask:  What is my Lead Generator status?"
else
    say "  1. To use Claude Code, run:"
    say "       \"$LEADGEN\" connect claude-code"
fi
say "  - To add your free API keys on a simple web page, run:"
say "       \"$LEADGEN\" dashboard --open"
say "    (the page opens at http://127.0.0.1:8765 - or ask Claude to \"open the dashboard\")"
say "  - To use ChatGPT, run:"
say "       \"$LEADGEN\" connect chatgpt"
if [ "$OS" = "Darwin" ]; then
    say "  - To use Claude Code, run:"
    say "       \"$LEADGEN\" connect claude-code"
fi
say ""
say "Run this installer again at any time to upgrade."
say "Your leads and keys stay in ${LEADGEN_HOME:-$HOME/LeadGenerator} and are not touched by upgrades."
say ""
