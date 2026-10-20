#!/usr/bin/env bash
# docker/_backup_lib.sh — helpers of backup.sh and verify-backup.sh. Source this; do not run directly.
# It defines functions only. db_connection_from_env and compose_network also
# need docker/_lib.sh (_env_var, PROJECT_NAME) sourced first.

# _url_decode STRING VARNAME — percent-decode STRING into VARNAME. A "%" that
# does not start an escape stays as it is, like urllib's unquote, which is what
# SQLAlchemy (so CMS) applies to the user and password of the URL.
_url_decode() {
  local rest="$1" out="" byte
  while [[ "$rest" == *%* ]]; do
    out+="${rest%%\%*}"
    rest="${rest#*%}"
    if [[ "$rest" =~ ^[0-9A-Fa-f]{2} ]]; then
      printf -v byte '%b' "\\x${rest:0:2}"
      out+="$byte"
      rest="${rest:2}"
    else
      out+="%"
    fi
  done
  printf -v "$2" '%s' "$out$rest"
}

# parse_db_url URL — export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE from
# postgresql[+psycopg2]://USER[:PASSWORD]@HOST[:PORT]/DBNAME[?query]. The
# password may hold a raw ":" or "/" (SQLAlchemy accepts them) but "@" must be
# written %40. Nothing is set on failure, and the URL is never printed because
# it holds the password.
# shortcut: no IPv6 literal host, switch to a real URL parser if one is needed.
parse_db_url() {
  local url="${1:-}" user password host port database
  if [[ -z "$url" ]]; then
    echo "ERROR: the database URL is empty" >&2
    return 1
  fi
  local pattern='^postgresql(\+psycopg2)?://([^:/@]+)(:([^@]*))?@([^:/?@]+)(:([0-9]+))?/([^?]+)(\?.*)?$'
  if [[ ! "$url" =~ $pattern ]]; then
    echo "ERROR: the database URL does not look like postgresql://USER:PASSWORD@HOST[:PORT]/DBNAME" >&2
    return 1
  fi
  # _url_decode matches its own regex, which overwrites BASH_REMATCH.
  user="${BASH_REMATCH[2]}" password="${BASH_REMATCH[4]}"
  host="${BASH_REMATCH[5]}" port="${BASH_REMATCH[7]:-5432}" database="${BASH_REMATCH[8]}"
  _url_decode "$user" user
  _url_decode "$password" password
  export PGHOST="$host" PGPORT="$port" PGDATABASE="$database" \
         PGUSER="$user" PGPASSWORD="$password"
}

# db_connection_from_env — export the PG* variables of the CMS database: the
# bundled container ("db") when CMS_USE_LOCALDB=true, CMS_DB_URL otherwise.
db_connection_from_env() {
  if [[ "$(_env_var CMS_USE_LOCALDB false)" != "true" ]]; then
    parse_db_url "$(_env_var CMS_DB_URL "")"
    return
  fi
  local user password database
  user="$(_env_var POSTGRES_USER "")"
  password="$(_env_var POSTGRES_PASSWORD "")"
  database="$(_env_var POSTGRES_DB "")"
  if [[ -z "$user" || -z "$password" || -z "$database" ]]; then
    echo "ERROR: POSTGRES_USER, POSTGRES_PASSWORD and POSTGRES_DB must be set (env or .env) with CMS_USE_LOCALDB=true" >&2
    return 1
  fi
  export PGHOST=db PGPORT=5432 PGUSER="$user" PGPASSWORD="$password" PGDATABASE="$database"
}

# pick_pg_major SERVER_VERSION_NUM — print the client image major: the server's,
# but never below 17 (an older pg_dump cannot read a newer server, and 17 has
# the options the scripts use).
pick_pg_major() {
  if [[ ! "${1:-}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: server_version_num must be a number (got: ${1:-})" >&2
    return 1
  fi
  local major=$(( 10#$1 / 10000 ))
  echo $(( major > 17 ? major : 17 ))
}

# backup_name [LABEL] — print cms-YYYYmmdd-HHMMSS[-LABEL]. The label is checked
# so the name can never leave backups/.
backup_name() {
  local label="${1:-}"
  if [[ -n "$label" && ! "$label" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "ERROR: the label may only hold letters, digits, \"_\" and \"-\"" >&2
    return 1
  fi
  echo "cms-$(date +%Y%m%d-%H%M%S)${label:+-$label}"
}

# compose_network — print the default network of the compose project.
compose_network() {
  local network
  network="$(docker network ls \
    --filter "label=com.docker.compose.project=$PROJECT_NAME" \
    --filter "label=com.docker.compose.network=default" \
    --format '{{.Name}}')"
  if [[ -z "$network" ]]; then
    echo "ERROR: no Docker network for project $PROJECT_NAME; run ./up.sh first" >&2
    return 1
  fi
  echo "$network"
}

# pg_tool MAJOR CMD... — run CMD in a postgres:MAJOR container on the compose
# network. The PG* variables go in by name, so the password stays out of argv.
# The fixed name lets a trap remove the container.
pg_tool() {
  local major="$1" network
  shift
  network="$(compose_network)" || return
  docker run --rm --init --name "cms-pgtool-$$" --network "$network" \
    -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE \
    "postgres:$major" "$@"
}

# manifest_compare A B — A and B are count sections ("table<TAB>count" lines).
# Print one line per table that differs or is missing on a side; return 1 if
# there is any.
manifest_compare() {
  if [[ -z "$1" || -z "$2" ]]; then
    echo "ERROR: a count section is empty" >&2
    return 1
  fi
  local differences
  differences="$(awk -F'\t' '
    NR == FNR { first[$1] = $2; next }
    { second[$1] = $2 }
    END {
      for (table in first) {
        if (!(table in second)) print table ": " first[table] " vs (absent)"
        else if (first[table] != second[table]) print table ": " first[table] " vs " second[table]
      }
      for (table in second)
        if (!(table in first)) print table ": (absent) vs " second[table]
    }' <(printf '%s\n' "$1") <(printf '%s\n' "$2") | LC_ALL=C sort)"
  [[ -z "$differences" ]] || { echo "$differences"; return 1; }
}
