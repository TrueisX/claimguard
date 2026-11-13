#!/usr/bin/env bash
# usage: deploy/smoke.sh http://localhost:8080
BASE="$1"; fails=0
code() { curl -s -o /dev/null -w "%{http_code}" "$BASE$1"; }
check() { if [ "$2" = "$3" ]; then echo "  PASS  $1 (got $2)"; else echo "  FAIL  $1 (got $2, want $3)"; fails=$((fails+1)); fi; }
for i in $(seq 1 40); do [ "$(code /healthz)" = "200" ] && break; sleep 1; done
check "health probe" "$(code /healthz)" 200
check "website loads" "$(code /)" 200
check "stylesheet loads" "$(code /static/app.css)" 200
check "demo accounts load (proves AWS access)" "$(code /api/demo-accounts)" 200
check "queue refuses no sign-in" "$(code /api/queue)" 401
[ "$fails" = 0 ] && echo "ALL CHECKS PASSED" || echo "FAILED: $fails check(s)"
exit "$fails"
