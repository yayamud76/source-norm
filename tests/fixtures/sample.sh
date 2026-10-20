#!/usr/bin/env bash
# ── ONESHOT: this header must not survive normalisation
set -euo pipefail

OUT=$(mktemp)            # a trailing comment, also dropped
arr=(a b c)
echo "${#arr}"           # ${#arr} is a length, NOT a comment
echo $#
echo "a # b"
echo 'a # b'
url=http://example.test/y#frag
cleanup() { rm -f "$OUT"; }
trap cleanup EXIT
