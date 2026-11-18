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
  -d '{"email":"smoke@test.com","password":"Str0ngPass!","name":"Smoke","surname":"Test"}' \
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
echo "=== Kategorie dokumentów ==="
classify "Faktura VAT FV/2024 NIP netto brutto VAT 23% termin platnosci przelew IBAN" "invoice"
classify "AGREEMENT party confidential intellectual property termination governing law obligation contractor" "contract"
classify "Patent claim invention embodiment prior art assignee field of invention abstract apparatus method" "patent"
classify "POZEW O ZAPLATE Sad Rejonowy powod pozwany wnosze o zasadzenie koszty procesu uzasadnienie" "lawsuit"
classify "PELNOMOCNICTWO mocodawca pelnomocnik upowazniam do reprezentowania power of attorney principal" "power_of_attorney"
classify "CURRICULUM VITAE doswiadczenie zawodowe wyksztalcenie umiejetnosci work experience education skills" "application"
classify "PRZEDSADOWE WEZWANIE DO ZAPLATY wierzyciel dluznik zaleglosc w terminie 7 dni letter before action" "demand_letter"

echo ""
echo "=== Confidence threshold -> UNKNOWN ==="
classify "hello world test" "gibberish"
