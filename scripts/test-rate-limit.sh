#!/usr/bin/env bash
set -uo pipefail

BASE_URL="${BASE_URL:-https://localhost:14443/api/v1}"

perform_request() {
  local method="$1"
  local path="$2"
  local content_type="$3"
  local body="$4"
  local header_file
  header_file="$(mktemp)"

  local code
  if [[ -n "$content_type" ]]; then
    code=$(curl -skS -o /dev/null -D "$header_file" -X "$method" "$BASE_URL$path" -H "Content-Type: $content_type" --data "$body" -w "%{http_code}")
  else
    code=$(curl -skS -o /dev/null -D "$header_file" -X "$method" "$BASE_URL$path" -w "%{http_code}")
  fi

  local retry_after
  retry_after=$(awk 'BEGIN { IGNORECASE = 1 } /^Retry-After:/ { gsub("\r", "", $2); print $2 }' "$header_file")
  rm -f "$header_file"

  printf '%s|%s\n' "$code" "${retry_after:--}"
}

run_scenario() {
  local name="$1"
  local attempts="$2"
  local method="$3"
  local path="$4"
  local content_type="$5"
  local body="$6"

  echo "=== $name ==="

  declare -A counts=()
  local first_429_attempt="-"

  for attempt in $(seq 1 "$attempts"); do
    local result
    result=$(perform_request "$method" "$path" "$content_type" "$body")

    local code="${result%%|*}"
    local retry_after="${result##*|}"

    counts["$code"]=$(( ${counts["$code"]:-0} + 1 ))

    if [[ "$code" == "429" && "$first_429_attempt" == "-" ]]; then
      first_429_attempt="$attempt"
    fi

    printf '[%02d] status=%s retry_after=%s\n' "$attempt" "$code" "$retry_after"
  done

  echo "Summary:"
  for status_code in 200 400 401 403 404 429 500; do
    if [[ -n "${counts[$status_code]:-}" ]]; then
      printf '  %s => %s\n' "$status_code" "${counts[$status_code]}"
    fi
  done
  printf '  first_429_attempt => %s\n' "$first_429_attempt"
  echo
}

echo "Testing rate limits against: $BASE_URL"
echo "Note: counters are keyed by client IP, so previous requests from the same IP may affect when 429 appears."
echo

run_scenario \
  "Browser session login" \
  12 \
  "POST" \
  "/login/session" \
  "application/x-www-form-urlencoded" \
  "username=rate-limit-test&password=bad-password"

run_scenario \
  "Public registration" \
  12 \
  "POST" \
  "/users/register" \
  "application/json" \
  '{"username":"rate-limit-test","password":"bad-password"}'

run_scenario \
  "Invitation validation" \
  35 \
  "GET" \
  "/invitations/validate/not-a-real-invite" \
  "" \
  ""
