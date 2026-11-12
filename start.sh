#!/bin/sh
set -eu
cd "$(dirname "$0")"
system_name=$(uname -s)
system_release=$(uname -r)
case "$system_name:$system_release" in
  Darwin:*) ;;
  Linux:*[Mm][Ii][Cc][Rr][Oo][Ss][Oo][Ff][Tt]*|Linux:*[Ww][Ss][Ll]*) echo 'Only native macOS and Linux are supported; WSL is not supported.' >&2; exit 1 ;;
  Linux:*) ;;
  *) echo 'Only native macOS and Linux are supported.' >&2; exit 1 ;;
esac
if ! command -v python3 >/dev/null 2>&1; then echo 'Python 3.10 or newer is required. Install it and try again.' >&2; exit 1; fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3,10))'; then echo 'Python 3.10 or newer is required.' >&2; exit 1; fi
if [ ! -d .venv ]; then
  python3 -m venv .venv || { echo 'Could not create a virtual environment. Install your system Python venv package and retry.' >&2; exit 1; }
fi
if [ ! -x .venv/bin/python ]; then
  echo 'The virtual environment is incomplete. Remove .venv and retry.' >&2
  exit 1
fi
exec .venv/bin/python local_service.py
