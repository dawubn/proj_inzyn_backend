# Dodatkowe przypadki językowe

Rzeczywiste odpowiedzi Azure prebuilt-read z 28.10.2026. Źródła to trzy nowe,
jednostronicowe, fikcyjne PDF-y z `output/pdf/testy_jezykowe_2026-10-28`
w głównym workspace projektu. Przeszły lokalną anonimizację przed Azure.

| Plik | Oczekiwany wynik poza podpisem |
| --- | --- |
| 06_instrukcje_zamiast_danych | brak mocodawcy i zakresu; pozostałe kontrole zaliczone |
| 07_sam_zakaz | zakres do sprawdzenia; pozostałe kontrole zaliczone |
| 08_zakres_z_ograniczeniem | wszystkie kontrole tekstowe zaliczone |

Podpis zawsze wymaga weryfikacji wizualnej. Testy nie oceniają prawnej
skuteczności zakresu, tylko rozpoznanie obecności afirmatywnej czynności.

Regresje odkryte podczas rzeczywistego przepływu:
- serializacja żądania SDK musi wysyłać `base64Source`, a nie `bytesSource`;
- spaCy potrafi uznać etykietę `Mocodawca:` albo błędnie odczytaną
  `Petnomocnik:` za nazwisko; etykiety nie mogą być maskowane;
- małe napisy masek mogą być przesunięte powyżej głównej linii tekstu;
- instrukcje zamiast danych nie stanowią wypełnienia pola.

Testy: `tests/services/test_formal_language.py`. Obejmują również odwróconą
kolejność linii Azure i przeskalowane współrzędne oraz pary zdań różniące się
przeczeniem, ograniczeniem albo rodzajem maski.
