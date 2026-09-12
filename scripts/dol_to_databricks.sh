#!/usr/bin/env bash
#
# Push the DOL Parquet into the Databricks volume, and pull the summary back.
#
# Push rather than pull, deliberately. Free Edition restricts outbound access to
# a set of trusted domains and does not document whether an external S3 bucket is
# among them, so nothing here depends on the cluster reaching out to AWS. It is
# ~30 MB either way, against 576 MB of source XLSX that stays local because Spark
# cannot read XLSX at all.
#
# Uses the Files API over curl rather than the Databricks CLI, so there is no
# extra tool to install and the same two variables in .env drive everything.
#
# Usage:
#     bash scripts/dol_to_databricks.sh push
#     bash scripts/dol_to_databricks.sh pull
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${DATABRICKS_HOST:-$(grep '^DATABRICKS_HOST=' .env | cut -d= -f2-)}"
TOKEN="${DATABRICKS_TOKEN:-$(grep '^DATABRICKS_TOKEN=' .env | cut -d= -f2-)}"
VOLUME="/Volumes/workspace/signal_dol/lake"
LOCAL_IN="data/dol_parquet"
LOCAL_OUT="data/dol_employer_summary"

[ -n "$HOST" ] && [ -n "$TOKEN" ] || { echo "DATABRICKS_HOST/TOKEN not set" >&2; exit 1; }

upload() {  # upload <local file> <volume path>
    curl -sf -X PUT -H "Authorization: Bearer $TOKEN" \
         --data-binary "@$1" "$HOST/api/2.0/fs/files$2?overwrite=true"
}

case "${1:-}" in
  push)
    n=0
    # Hive layout preserved, because the Spark job reads fiscal_year and
    # fiscal_quarter as partition columns rather than as file contents.
    while IFS= read -r f; do
        rel="${f#"$LOCAL_IN"/}"
        printf '  %s\n' "$rel"
        upload "$f" "$VOLUME/parquet/$rel"
        n=$((n + 1))
    done < <(find "$LOCAL_IN" -name '*.parquet' | sort)
    echo "pushed $n file(s) to $VOLUME/parquet"
    ;;
  pull)
    # Walk the Hive layout the job wrote and mirror it locally, so
    # storage/load_dol.py reads exactly what it reads after a laptop run.
    rm -rf "$LOCAL_OUT"; mkdir -p "$LOCAL_OUT"
    n=0
    walk() {
        local remote="$1" local_dir="$2"
        local listing
        listing=$(curl -sf -H "Authorization: Bearer $TOKEN" \
                  "$HOST/api/2.0/fs/directories$remote") || return 0
        # One path per line, with a trailing marker for directories.
        while IFS=$'\t' read -r path isdir; do
            [ -n "$path" ] || continue
            local name="${path##*/}"
            [ -n "$name" ] || name="${path%/}"; name="${name##*/}"
            if [ "$isdir" = "true" ]; then
                mkdir -p "$local_dir/$name"
                walk "${path%/}" "$local_dir/$name"
            else
                case "$name" in _SUCCESS|_committed*|_started*) continue ;; esac
                curl -sf -H "Authorization: Bearer $TOKEN" \
                     "$HOST/api/2.0/fs/files$path" -o "$local_dir/$name"
                printf '  %s\n' "${local_dir#"$LOCAL_OUT"/}/$name"
                n=$((n + 1))
            fi
        done < <(printf '%s' "$listing" | python3 -c "
import json, sys
for e in json.load(sys.stdin).get('contents', []):
    print(e['path'], 'true' if e.get('is_directory') else 'false', sep='\t')
")
    }
    walk "$VOLUME/employer_summary" "$LOCAL_OUT"
    echo "pulled $n file(s) into $LOCAL_OUT"
    ;;
  *) echo "usage: $0 push|pull" >&2; exit 2 ;;
esac
