#!/bin/sh
# Start the panel (no arguments), or any rustmapgen command: ./rustmapgen.sh generate --recipe large
# The panel ends with 3 once it has updated itself: it is started again here.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Not installed yet: run ./install.sh first." >&2
    exit 1
fi
if [ $# -eq 0 ]; then
    set -- panel
fi
if [ "$1" != "panel" ]; then
    exec .venv/bin/python -m rustmapgen "$@"
fi
export RUSTMAPGEN_LAUNCHER=1
while :; do
    .venv/bin/python -m rustmapgen "$@"
    code=$?
    [ "$code" -eq 3 ] || exit "$code"
done
