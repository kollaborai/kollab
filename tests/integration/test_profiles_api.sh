#!/bin/bash
# Integration test for kollabor-engine profiles API

set -e

ENGINE_PORT=${ENGINE_PORT:-7433}
ENGINE_HOST=${ENGINE_HOST:-127.0.0.1}
BASE_URL="http://${ENGINE_HOST}:${ENGINE_PORT}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Test counters
TOTAL=0
PASSED=0
FAILED=0

# Track test profile name
TEST_PROFILE="api-test-$$"

# Start server
echo "Starting kollabor-engine server..."
python -m kollabor_engine serve --port "$ENGINE_PORT" >/dev/null 2>&1 &
SERVER_PID=$!
# Wait for server to be ready
for i in $(seq 1 10); do
    if curl -s "$BASE_URL/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# Check server started
if ! curl -s "$BASE_URL/health" >/dev/null 2>&1; then
    echo -e "${RED}FAIL: Server did not start${NC}"
    exit 1
fi

# Get token (server generates it on startup)
sleep 1  # Give token time to be written
TOKEN=$(cat ~/.kollab/engine.token 2>/dev/null || echo "")
if [ -z "$TOKEN" ]; then
    echo -e "${RED}FAIL: No auth token found${NC}"
    kill $SERVER_PID 2>/dev/null || true
    exit 1
fi
echo "Token: ${TOKEN:0:10}..."

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    kill $SERVER_PID 2>/dev/null || true
    # Delete test profile if it exists
    curl -s -X DELETE "$BASE_URL/profiles/$TEST_PROFILE" -H "Authorization: Bearer $TOKEN" >/dev/null 2>&1 || true
    curl -s -X DELETE "$BASE_URL/profiles/renamed-$TEST_PROFILE" -H "Authorization: Bearer $TOKEN" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Test function
test() {
    local name="$1"
    local expected="$2"
    local actual="$3"

    TOTAL=$((TOTAL + 1))

    if [ "$actual" = "$expected" ]; then
        echo -e "  ${GREEN}[PASS]${NC} $name"
        PASSED=$((PASSED + 1))
    else
        echo -e "  ${RED}[FAIL]${NC} $name"
        echo "    Expected: $expected"
        echo "    Got: $actual"
        FAILED=$((FAILED + 1))
    fi
}

test_contains() {
    local name="$1"
    local needle="$2"
    local haystack="$3"

    TOTAL=$((TOTAL + 1))

    if echo "$haystack" | grep -q "$needle"; then
        echo -e "  ${GREEN}[PASS]${NC} $name"
        PASSED=$((PASSED + 1))
    else
        echo -e "  ${RED}[FAIL]${NC} $name"
        echo "    Expected to contain: $needle"
        echo "    Got: $haystack"
        FAILED=$((FAILED + 1))
    fi
}

# Run tests
echo ""
echo "=== Test 1: GET /profiles - list profiles ==="
response=$(curl -s -H "Authorization: Bearer $TOKEN" "$BASE_URL/profiles")
test_contains "Response contains profiles array" "profiles" "$response"
test_contains "Response contains count" "count" "$response"

echo ""
echo "=== Test 2: GET /profiles/{name} - get default profile ==="
response=$(curl -s -H "Authorization: Bearer $TOKEN" "$BASE_URL/profiles/default")
test_contains "Response contains profile name" '"name"' "$response"
test_contains "Response contains provider" "provider" "$response"

echo ""
echo "=== Test 3: GET /profiles/{name} - 404 for unknown ==="
http_code=$(curl -s -w "%{http_code}" -o /dev/null -H "Authorization: Bearer $TOKEN" "$BASE_URL/profiles/unknown-profile-xyz")
test "Returns 404 for unknown profile" "404" "$http_code"

echo ""
echo "=== Test 4: POST /profiles - create new profile ==="
response=$(curl -s -X POST "$BASE_URL/profiles" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$TEST_PROFILE\", \"provider\": \"custom\", \"model\": \"test-model\", \"description\": \"Test profile\"}")
test_contains "Create returns profile name" "\"name\"" "$response"
test_contains "Create returns created=true" '"created":true' "$response"

echo ""
echo "=== Test 5: POST /profiles - 400 for duplicate name ==="
http_code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/profiles" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$TEST_PROFILE\", \"provider\": \"custom\", \"model\": \"test-model\"}")
test "Returns 400 for duplicate" "400" "$http_code"

echo ""
echo "=== Test 6: PUT /profiles/{name} - update profile ==="
response=$(curl -s -X PUT "$BASE_URL/profiles/$TEST_PROFILE" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"description": "Updated via API", "temperature": 0.5}')
test_contains "Update returns updated=true" '"updated":true' "$response"
test_contains "Description was updated" "Updated via API" "$response"

echo ""
echo "=== Test 7: PUT /profiles/{name} - rename profile ==="
response=$(curl -s -X PUT "$BASE_URL/profiles/$TEST_PROFILE" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"new_name\": \"renamed-$TEST_PROFILE\"}")
test_contains "Rename returns new name" "\"renamed-$TEST_PROFILE\"" "$response"

echo ""
echo "=== Test 8: DELETE /profiles/{name} - delete profile ==="
response=$(curl -s -X DELETE "$BASE_URL/profiles/renamed-$TEST_PROFILE" \
    -H "Authorization: Bearer $TOKEN")
test_contains "Delete returns deleted=true" '"deleted":true' "$response"

echo ""
echo "=== Test 9: DELETE /profiles/{name} - 400 for built-in ==="
http_code=$(curl -s -w "%{http_code}" -o /dev/null -X DELETE "$BASE_URL/profiles/default" \
    -H "Authorization: Bearer $TOKEN")
test "Returns 400 for built-in profile" "400" "$http_code"

echo ""
echo "=== Test 10: POST /profiles/{name}/test - test endpoint ==="
response=$(curl -s -X POST "$BASE_URL/profiles/default/test" \
    -H "Authorization: Bearer $TOKEN")
test_contains "Test endpoint returns success" "success" "$response"

echo ""
echo "=== Test 11: Auth required - 401 without token ==="
http_code=$(curl -s -w "%{http_code}" -o /dev/null "$BASE_URL/profiles")
test "Returns 401 without auth" "401" "$http_code"

echo ""
echo "=== Test 12: Health endpoint works without auth ==="
response=$(curl -s "$BASE_URL/health")
test_contains "Health endpoint accessible" "healthy" "$response"

# Summary
echo ""
echo "=============================================="
echo "Test Summary"
echo "=============================================="
echo "Total: $TOTAL | Passed: $PASSED | Failed: $FAILED"

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed${NC}"
    exit 1
fi
