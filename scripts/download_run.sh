#!/usr/bin/env bash
# Download a WSA-Enlil run from NOAA's public bucket with s5cmd, skipping its pv-ready-data-* directory.
# Usage: ./download_run.sh <run_id> [extra s5cmd global flags, e.g. --dry-run or --numworkers 16]
set -euo pipefail

run_id="${1:?Usage: $0 <run_id> [extra s5cmd global flags]}"
[[ $run_id =~ ^[0-9]+$ ]] || { echo "run_id must be numeric, got '$run_id'" >&2; exit 1; }
bucket="s3://noaa-wsa-enlil-pds"

# Print the "directories" directly under an S3 prefix
list_dirs() { s5cmd --no-sign-request ls "$1" | awk '$1 == "DIR" {print $2}'; }

# Runs live at <bucket>/wsa_enlil.<YYYYMMDD>_<run_id>/wsa_enlil_<run_id>[.<n>.<host>]/
date_dir=$(list_dirs "$bucket/" | grep -E "^wsa_enlil\.[0-9]{8}_${run_id}/$") \
    || { echo "No run $run_id found in $bucket" >&2; exit 1; }
run_dir=$(list_dirs "$bucket/$date_dir" | grep -E "^wsa_enlil_${run_id}[./]") \
    || { echo "No wsa_enlil_$run_id.* directory under $bucket/$date_dir" >&2; exit 1; }

s5cmd --no-sign-request "${@:2}" cp --exclude "pv-ready-data-*" \
    "$bucket/$date_dir$run_dir*" "./$run_dir"
