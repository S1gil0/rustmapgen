#!/bin/sh
# RustMapGen installer for Linux: a Python environment of its own, steamcmd, the Rust dedicated
# server (downloaded from Steam, several GB) and the generator's mod. Run it as an ordinary user,
# from the folder it is in. Running it again updates the game.
set -e
cd "$(dirname "$0")"
PYTHON=${PYTHON:-python3}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.9 or newer is needed (on Debian or Ubuntu: sudo apt install python3 python3-venv)." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    echo "Python 3.9 or newer is needed; $("$PYTHON" --version 2>&1) was found." >&2
    exit 1
fi
if [ "$(uname -m)" != "x86_64" ]; then
    echo "The Rust dedicated server runs on 64-bit x86 only; this machine is $(uname -m)." >&2
    exit 1
fi
# A run that stopped half way can leave an environment without pip: it is made again in place.
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "Making a Python environment of its own in .venv..."
    if ! "$PYTHON" -m venv --clear .venv; then
        echo "Could not make a Python environment (on Debian or Ubuntu: sudo apt install python3-venv)." >&2
        exit 1
    fi
fi
echo "Installing the Python packages it needs (numpy, Pillow, lz4)..."
.venv/bin/python -m pip install --quiet --disable-pip-version-check --upgrade pip
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m rustmapgen install "$@"
chmod +x rustmapgen.sh
echo
echo "Installed. Start the panel with:  ./rustmapgen.sh"
