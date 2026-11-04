#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://localhost:8000}"

echo "=== /health ==="
curl -s -o /dev/null -w "API HTTP %{http_code}\n" "$BASE/health"
curl -s -o /dev/null -w "Flower HTTP %{http_code}\n" http://localhost:5555/
curl -s -o /dev/null -w "Docs HTTP %{http_code}\n" "$BASE/docs"

echo ""
echo "=== Login ==="
curl -s -X POST "$BASE/api/v1/auth/register" -H "Content-Type: application/json" \
  -d '{"email":"smoke@test.com","password":"Str0ngPass!","full_name":"Smoke","role":"business_user"}' \
  > /dev/null || true
TOKEN=$(curl -s -X POST "$BASE/api/v1/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"smoke@test.com","password":"Str0ngPass!"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('access_token',''))")
echo "Token length: ${#TOKEN}"

classify () {
  local content="$1"
  local label="$2"
  local resp
  resp=$(curl -s -X POST "$BASE/api/v1/classify" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"content\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$content")}")
  python3 -c "
import json, sys
data = json.loads('''$resp''')
print(f\"  [{'$label'}] -> {data['document_type']:<22} ({data['confidence']:.2%})\")
"
}

echo ""
echo "=== Klasy DocLayNet ==="
classify "Annual report consolidated balance sheet shareholders equity revenue net income earnings per share dividend quarterly" "financial"
classify "Patent claim invention embodiment prior art assignee field of invention abstract apparatus method" "patent"
classify "Article section paragraph statute regulation pursuant amendment legislative" "law"
classify "Installation manual setup procedure warning safety user guide configuration troubleshooting" "manual"

echo ""
echo "=== Klasy syntetyczne ==="
classify "Faktura VAT FV/2024 NIP netto brutto VAT 23% termin platnosci przelew IBAN" "invoice"
classify "AGREEMENT party confidential intellectual property termination governing law obligation contractor" "contract"
classify "DOWOD OSOBISTY PESEL nazwisko obywatelstwo data urodzenia organ wydajacy numer dokumentu" "id_card"
classify "PASSPORT POL surname nationality machine readable zone passport number date of expiry biometric" "passport"
classify "Bank statement IBAN opening balance closing balance transaction transfer ATM withdrawal salary card payment" "bank"
classify "PIT-37 podatnik Urzad Skarbowy podatek dochod zaliczka zwrot ZUS deklaracja podatkowa" "tax"

echo ""
echo "=== Confidence threshold -> UNKNOWN ==="
classify "hello world test" "gibberish"
