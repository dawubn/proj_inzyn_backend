# Złożone formularze — rzeczywiste odczyty Azure

Nagrania z 28.10.2026, uzyskane z pięciu testowych PDF-ów z katalogu
`output/pdf/trudniejsze_2026-10-27` w głównym workspace projektu.
Osoby, dane i zdarzenia w tych dokumentach są fikcyjne.

Przepływ: PDF → lokalna anonimizacja → TIFF → Azure prebuilt-read.
Nagrania obejmują wszystkie 11 stron. Dokumenty wielostronicowe przesłano
w partiach po maksymalnie dwie strony; numery stron i offsety tekstu zostały scalone.
Żaden wynik lokalnego OCR nie jest źródłem faktów dla walidatora formalnego.

| Dokument | Typ | Strony | Oczekiwany brak tekstowy |
| --- | --- | --- | --- |
| 01_pelnomocnictwo_programowe | power_of_attorney | 3 | data dokumentu |
| 02_pelnomocnictwo_formularz | power_of_attorney | 1 | dane pełnomocnika |
| 03_pelnomocnictwo_opisowe | power_of_attorney | 2 | zakres pełnomocnictwa |
| 04_umowa_uslugowa | contract | 3 | data zawarcia |
| 05_pozew_o_zaplate | lawsuit | 2 | żądanie pozwu |

W każdym przypadku podpis pozostaje `unknown`: odczyt tekstu nie rozstrzyga,
czy w danym miejscu jest podpis graficzny. Pozostałe reguły danego profilu
muszą zwracać `pass`, w tym załącznik umowy na trzeciej stronie.

Testy: `tests/services/test_formal_complex.py`. Nagrania umożliwiają powtarzalne
testy bez połączenia z Azure i bez ponownego naliczania odczytów.

Szczególne regresje: podpisy pól pod wartościami, fragmenty wiersza odczytane
w innej kolejności, opisowe dane osób, maski o niskiej pewności odczytu,
daty pomocnicze, puste żądanie obok wniosku dowodowego oraz podpisy stron
umowy, które nie są kolejnymi pustymi polami identyfikacyjnymi.

## Reduced fixtures

`02_pelnomocnictwo_formularz.ocr.json` is the complete recorded response. The other
files are reduced Azure-derived inputs, not complete responses. They retain all
pages, text, line geometry and spans, plus the lowest-confidence overlapping word
for each line. See [fixture reduction](../formal_fixtures.md).
