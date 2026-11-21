#!/bin/sh
# Wait until the gateway answers (401 without a key means it is up and enforcing).
i=0
while [ $i -lt 60 ]; do
  code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' -X POST http://127.0.0.1:13000/mcp || true)
  [ "$code" = "401" ] && echo "gateway is up" && exit 0
  i=$((i + 1)); sleep 1
done
echo "gateway did not come up on 127.0.0.1:13000" >&2
exit 1
