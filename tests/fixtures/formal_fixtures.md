# Formal validation OCR fixtures

Three complete recorded Azure responses cover the word-level input format:

- `formal_complex/02_pelnomocnictwo_formularz.ocr.json`: form layout, empty attorney
  field and redacted values.
- `formal_language/07_sam_zakaz.ocr.json`: a prohibition that must not establish
  an affirmative authorization scope.
- `formal_v2/01_pelnomocnictwo_wypelnione.ocr.json`: filled, anonymized fields.

The remaining 14 recordings are reduced regression inputs derived from Azure
responses. They are not full service responses and must not be used to test
word-level OCR reconstruction or anonymization.

## Retained data

The reduced inputs keep the complete document text, every page and line, page
dimensions and units, original line order, polygons, and text spans. For each
line, they retain the original overlapping word with the lowest confidence.
Words selected for multiple lines are stored once, in their original order.
The retained words keep their original text, confidence, polygon, and span.
Unused page metadata and other words are removed.

This preserves the minimum word confidence used by `DocumentContext` for each
line, including low-confidence cases. No confidence scores are invented or
increased. This reduction requires text spans for every line; it must not be
applied to fixtures that rely on geometry-only word matching.

Each OCR record occupies one JSON line so text and geometry can be reviewed
together. The reduction removes redundant records as well as formatting overhead.

## Verification

Before replacement, all 14 document contexts and extracted fields for all 17
document types (238 comparisons) matched the original recordings. Only input
fingerprints differed, as expected when the input JSON changes. Existing expected
results were kept unchanged, including checks for page counts, field states,
evidence, line ordering, and coordinate scaling.

The fixtures contain fictional data and run offline. They are regression cases,
not a representative accuracy benchmark. When OCR behavior changes, review the
original documents and expected outcomes before updating these inputs.
