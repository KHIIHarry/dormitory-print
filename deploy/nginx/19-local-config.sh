#!/bin/sh
set -eu
# /tmp is a fresh writable tmpfs on every start. The Nginx template entrypoint
# requires its output directory to exist before rendering templates.
mkdir -p /tmp/conf.d
