# Azure OCR regression inputs

Five responses captured on 2026-10-26 from the application's real local anonymization → Azure `prebuilt-read` path, using synthetic one-page Polish powers of attorney. All names are fictional. The corresponding PDFs are in `output/pdf/formal_validation_v2/` in the working copy.

The JSON files contain OCR results only, without credentials, request headers, resource URLs or operation IDs. Tests use these fixtures offline; they never call Azure. This is a regression set, not a representative accuracy benchmark or a held-out evaluation corpus.

Expected failures:

- `01`: none; masked values pass presence checks; only the signature remains unknown.
- `02`: empty principal field.
- `03`: empty authorization scope.
- `04`: empty document date, despite a date in a quoted statute.
- `05`: none; masked values pass presence checks; only the signature remains unknown.

Regenerate intentionally after reviewing OCR/model changes; do not automatically replace expected results with current predictions.

## Reduced fixtures

`01_pelnomocnictwo_wypelnione.ocr.json` is the complete recorded response. The other
files are reduced Azure-derived inputs, not complete responses. See
[fixture reduction](../formal_fixtures.md) for the retained data and verification.
