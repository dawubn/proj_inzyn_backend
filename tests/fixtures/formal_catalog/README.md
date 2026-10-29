# Przykłady nowych typów dokumentów

`cases.json` zawiera 12 ręcznie przygotowanych, fikcyjnych przykładów po jednym
dla każdego nowego profilu. Klucze oczekiwanych pól są zapisane w przykładach,
nie są generowane z katalogu ekstraktora. Testy zmieniają wartości na puste,
maski i nieprawidłowe dane oraz sprawdzają niezależność sąsiadujących pól.

Pliki `09`–`12` z rozszerzeniem `.ocr.json` są rzeczywistymi odpowiedziami
Azure `prebuilt-read` uzyskanymi 29.10.2026 po lokalnej anonimizacji PDF-ów.
Nie zawierają danych rzeczywistych osób. `cloud_expected.json` określa
oczekiwane wyniki każdego sprawdzenia. Wyniki można odtwarzać bez połączenia
z chmurą. PDF-y znajdują się w katalogu głównym projektu:
`output/pdf/pozostale_typy_2026-10-29`.

Celowe braki: 09 nabywca, 10 numer rachunku, 11 data ważności, 12 streszczenie.
Streszczenie jest kontrolą dodatkową, więc jego brak nie oznacza automatycznie
niekompletności każdego rodzaju artykułu. Zdjęcie i zabezpieczenia dokumentu
tożsamości zawsze wymagają oceny wizualnej.

To ograniczony korpus regresyjny, nie reprezentatywny benchmark jakości na
wszystkich odmianach dokumentów. Osiem pozostałych nowych typów ma testy na
kontrolowanych strukturach OCR, bez nowego wywołania Azure w tej rundzie.

## Reduced fixtures

The four `.ocr.json` files are reduced Azure-derived inputs, not complete
responses. All document text, pages and line geometry are preserved. See
[fixture reduction](../formal_fixtures.md) for the retained data and verification.
