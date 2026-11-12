# Zakładka „Budowa” — jak działa automat

Opis całej logiki rozbudowy wiosek: co automat liczy, w jakiej kolejności działa i od czego zależy każda decyzja. Liczby w nawiasach to wartości domyślne albo stałe w kodzie. Na końcu jest tabela ustawień i mapa plików.

---

## 1. Zasady nadrzędne

- **Tylko klikanie.** Każda akcja w grze (budowa, dokończenie, handlarz, wysyłka, rekrutacja, budowniczy, bohater) to symulowane kliknięcia i klawisze w osobnej karcie w tle (karta „lane”), tak jak robi to gracz. Automat nigdy nie wysyła żądań do API gry. Dane są tylko **odczytywane** z modeli gry (`Village`, `Building`, `BuildingQueue`, `Hero` itd.).
- **Sprawdzone kliknięcie.** Przed każdym kliknięciem automat sprawdza, czy pod kursorem jest dokładnie ten element. Jeśli jest coś innego (okno, ekran ładowania), klik zostaje wstrzymany. Okna zamyka klawiszem Escape, nigdy klikaniem „na ślepo”.
- **Blokada złota.** Przed każdą akcją automat zapisuje saldo złota, a po akcji porównuje. Jeśli spadło bardziej, niż akcja mogła kosztować, automat się zatrzymuje i pokazuje okno z ostrzeżeniem.
- **Potwierdzenie złota w grze.** Okno „Na pewno wydać złoto?” automat zatwierdza tylko wtedy, gdy ikona i cena się zgadzają, a opcja „Nie pokazuj potwierdzeń” jest odznaczona.
- **Rezerwa złota** (`gold_reserve`, 50). Automat nigdy nie zejdzie poniżej tej kwoty.

---

## 2. Cykl automatu

Automat rusza **co 3 minuty**. Dodatkowo „budzi się” wtedy, gdy:
- budowa wchodzi w okno płatnego albo darmowego dokończenia;
- nadchodzący atak wchodzi w okno ochrony (punkt 11a), a potem w tym oknie co minutę;
- poprzedni cykl coś zrobił (wtedy po kilku sekundach robi świeży odczyt).

Jeden cykl (`BuildAutomation.tick`) wygląda tak:

1. **Odczyt stanu gry** (`read_build_state`). Dla każdej wioski: pola i budynki z kosztami, czasami, efektami i zużyciem zboża kolejnych poziomów; kolejki budowy, w tym budowniczy, oraz informacja, które wpisy mają zarezerwowane surowce; surowce, pojemność, produkcja; nadchodzące surowce (kupcy, łupy); kolejki rekrutacji; kupcy; puste działki. Dane globalne: złoto, cennik dokończeń, cena handlarza, dane nowych budynków, jednostki plemienia, bohater.
2. **Plan** (`refresh`), opisany w punktach 3–9:
   - plan każdej wioski;
   - bilans sieci;
   - wstawienie targowiska;
   - transfery;
   - oferty dokończeń;
   - budżet złota;
   - handlarz.
3. **Działania w każdej wiosce** (`_act`), w tej kolejności:
   1. dokończenie budowy (za darmo albo za złoto, punkt 5);
   2. rezerwacja surowców na wpis u budowniczego;
   3. podmiana niezarezerwowanego wpisu u budowniczego, gdy plan ma lepszy krok;
   4. dopełnienie kolejki budowniczego kolejnymi krokami planu;
   5. handlarz NPC, gdy przyspiesza następny krok;
   6. rozbudowa albo postawienie nowego budynku, gdy kolejka jest wolna i surowce są.
4. **Szkolenie wojska** (`_train`, punkt 10).
5. **Bohater** (`_hero`, punkt 11).
5a. **Ochrona przed grabieżą** (`_protect`, punkt 11a), tylko gdy atak dotrze w ciągu ustawionego czasu.
5b. **Bank zboża: mielenie** (`_mill`, punkt 11b), gdy spichlerze banku są zapełnione do progu.
6. **Wysyłki surowców** (`_send`, punkt 7). Najwyżej jedna z danej wioski na cykl, a potem 5 min przerwy dla tej wioski.
7. **Spalanie nadmiaru w rekrutacji** (`_burn_overflow`, punkt 8).

Po dokończeniu budowy w wiosce automat nic więcej w niej nie robi w tym cyklu. Czeka na świeży odczyt, bo kolejka właśnie się zmieniła.

---

## 3. Planer wioski (`plan_village`)

### 3.1 Cel
- Stolica: pola do `capital_target` (14). Pozostałe wioski: do `village_target` (10).
- Wioska z listy wyłączonych jest planowana i pokazywana, ale automat w niej nie działa.

### 3.2 Czy wioska czeka na kolejkę, czy na surowce
Planer porównuje dwa czasy:
- **czas kolejki:** suma czasów budowy pozostałych pól;
- **czas surowców:** ile godzin produkcji potrzeba na ich koszt.

Jeśli kolejka trwa dłużej, wioska jest **ograniczona kolejką**, a jeśli surowce, to **ograniczona surowcami**. Od tego zależy ranking:
- **Ograniczona surowcami:** najpierw krok o najkrótszym **czasie zwrotu**, czyli koszt (ważony rzadkością) podzielony przez przyrost produkcji.
- **Ograniczona kolejką:** najpierw krok, który daje **najwięcej produkcji na godzinę kolejki**.

### 3.3 Wagi surowców (`balance_resources`)
- Surowiec, którego do celu brakuje najbardziej (koszty pozostałych pól wobec produkcji), dostaje wyższą wagę, a nadmiarowy niższą. Wagi mieszczą się w przedziale 0,25–4.
- Pola rzadkiego surowca dzięki temu awansują w rankingu. To samo z kosztami: krok płacony nadmiarowym surowcem wydaje się tańszy.
- Gdy handlarz jest na „automat”, wagi są wyłączone, bo każdą nadwyżkę i tak da się wymienić 1:1.

### 3.4 Zboże
Zysk netto pola to produkcja minus dodatkowe zboże, które zjada ludność. Jeśli wybrany krok zepchnąłby bilans zboża poniżej zera, planer bierze zamiast niego pole zboża.

### 3.5 Magazyn i spichlerz
- **Krok się nie mieści:** jeśli koszt kroku przekracza pojemność, najpierw idzie rozbudowa magazynu albo spichlerza.
- **Zapas produkcji** (`storage_hours`, Twoje ustawienie 12): magazyn i spichlerz mają pomieścić tyle godzin produkcji. Jeśli nie mieszczą, rozbudowa wchodzi przed pola.
- **Brak budynku:** jeśli wioska w ogóle nie ma magazynu albo spichlerza (np. nowa wioska, pojemność 800), planer stawia nowy na wolnej działce. Wyjątki:
  - nie stawia go na miejscach placu zbiórki i muru (39 i 40);
  - nie stawia go, gdy brakuje budynku głównego.

### 3.6 Budynek główny (`main_building`)
- Działa tylko w wiosce ograniczonej kolejką.
- Kolejny poziom wchodzi do planu, gdy skrócenie pozostałych budów pól jest co najmniej **2 razy** dłuższe niż czas budowy samego budynku głównego.
- Każdy poziom przyspiesza wszystkie dalsze kroki planu.

### 3.7 Budynki bonusowe (`bonus_buildings`)
- Tartak, cegielnia, huta, młyn i piekarnia dają +5% produkcji na poziom. Rywalizują z polami tą samą miarą (zwrot albo zysk na godzinę kolejki).
- Nowy budynek bonusowy planer stawia, gdy gra spełnia wymagania (np. młyn wymaga pola zboża na poziomie 5). Każdy typ tylko raz.

### 3.8 Koszary i stajnia
- Dotyczy tylko wiosek z sekcji Wojsko, do poziomu z ustawień (punkt 10).
- Nic nie produkują, więc nie konkurują w rankingu. Gdy wioska ma nadwyżkę surowców, kroki wojskowe **przeplatają się** z resztą planu (co drugi krok). Bez nadwyżki wchodzą dopiero po polach.
- Koszary i stajnia rosną równo, najpierw niższy z nich.

### 3.9 Targowisko
- **Postawienie:** gdy wioska nie ma targowiska, a ma nadwyżkę dla sieci albo w ciągu 4 h groziłoby jej przepełnienie, nowe targowisko idzie na początek planu.
- **Rozbudowa:** gdy kupcy nie nadążają z wywozem nadwyżki (bilans sieci, punkt 7.1), targowisko dostaje kolejny poziom, czyli +1 kupiec.

Plan sięga **8 kroków** naprzód. Tabela w zakładce pokazuje je z czasem, kosztem, powodem i informacją o złocie.

---

## 4. Budowniczy (kolejka „master builder”)

Ustawienie `master_builder_slots` (4) określa, ile kolejnych kroków trzyma się w kolejce budowniczego.

- **Dopełnianie:** gdy kolejka jest zajęta, kolejne kroki planu trafiają do budowniczego. Ruszą same, gdy kolejka się zwolni. Nowe budynki trafiają tam przez okno budowy.
- **Rezerwacja:** gdy na pierwszy niezarezerwowany wpis są już surowce, automat klika „zarezerwuj”. Surowce opuszczają magazyn, więc nie mogą się przelać ani zostać wysłane.
- **Podmiana** (`master_replan`):
  - Automat planuje wioskę tak, jakby niezarezerwowanych wpisów nie było.
  - Wpis, którego budynku nie ma wśród najbliższych kroków planu, zostaje usunięty, a na jego miejsce wchodzi lepszy krok.
  - Pole zamienia się na inne pole tylko wtedy, gdy zwraca się co najmniej o 15% wolniej.
  - Wpisów zarezerwowanych automat nigdy nie rusza.
  - Budynek (nie pole), którego plan już w ogóle nie chce, jest usuwany od razu. Przykład: magazyn wpisany, gdy wioska była ograniczona kolejką.
  - Kroki doklejone przed planem wioski (nowe targowisko, rozbudowa targowiska) trafiają do budowniczego jako pierwsze.
- **Wpis, który blokuje kolejkę:** usuwany, gdy spełnione są wszystkie warunki:
  - pierwszy niezarezerwowany wpis zaraz będzie na czele kolejki;
  - zabraknie na niego surowców na co najmniej 30 min;
  - na inny krok planu lub inny wpis surowce są od razu;
  - żaden transport do wioski nie jest w drodze.

  Wraca do kolejki, gdy plan do niego dojdzie.
- **Bez kręcenia się w kółko:** miejsca dodanego albo usuniętego automat nie dotyka przez 30 min.

---

## 5. Złoto: dokończenia

### 5.1 Progi (`finish_*`)
Gra liczy: poniżej 5 min za darmo, poniżej 2 h za 1 złoto, powyżej za 2 złota. Ustawienia określają, z czego automat korzysta:

| Pozostały czas budowy | Zachowanie |
|---|---|
| < `finish_free_minutes` (5) | dokończenie za darmo (`finish_free`) |
| `finish_reduced_min_minutes` … `finish_reduced_minutes` (Twoje 110…119 min) | 1 złoto (`finish_reduced`) |
| krótsza budowa | czeka na darmowe dokończenie |
| między progiem 1 złota a `finish_full_min_minutes` (180) | czeka, aż spadnie do ceny 1 złota |
| ≥ `finish_full_min_minutes` | 2 złota (`finish_full`, u Ciebie wyłączone) |

`finish_mode`:
- **„zalecenia”:** płatne dokończenia są tylko pokazywane, a darmowe wykonywane.
- **„automat”:** płatne też są klikane.

### 5.2 Budżet złota (`gold_budget`, Twoje 225)
Jeśli budżet jest ustawiony, decyduje on, a nie same progi:

- **Rozdział:** `plan_gold_budget` liczy dla każdej przyszłej budowy, ile godzin kolejki da jedno złoto. Budżet idzie na najlepsze z nich, a **próg** to najsłabsza budowa, która się zmieściła (np. 1,5 h na złoto).
- **Kiedy płaci:** dokończenie jest płatne tylko wtedy, gdy:
  - jest warte co najmniej progu,
  - na następny krok w wiosce już są surowce (inaczej przyspieszenie nic nie da),
  - cena mieści się w pozostałym budżecie i w rezerwie.
- **Teraz czy później:** automat wybiera, czy zapłacić pełną cenę od razu, czy poczekać na tańszą, zależnie od tego, co daje więcej godzin na złoto.
- **Licznik wydatków:** `gold_budget_spent` sumuje faktycznie wydane złoto, czyli różnicę salda przed i po kliknięciu, i zapisuje się w ustawieniach.

### 5.3 Ratowanie magazynu złotem (`overflow_finish_per_gold`, 2000)
Gdy budujący się magazyn albo spichlerz kończy się dopiero po przelaniu surowców, automat może go dokończyć złotem. Dotyczy to też kilku magazynów po kolei w kolejce. Robi to, jeśli każde złoto ratuje co najmniej 2000 surowców.

---

## 6. Handlarz NPC (`npc_mode`)

- **Kiedy:** gdy suma surowców starcza na następny krok, a proporcje nie, wymiana u handlarza (5 złota) pozwala zacząć od razu.
- **Warunek:** wymiana musi przyspieszyć krok o co najmniej `npc_min_saved_minutes` (Twoje 59 min) albo bez niej krok w ogóle by się nie uzbierał.
- **Tryby:** „zalecenia” tylko pokazuje taką wymianę, „automat” ją klika. Obowiązuje rezerwa złota.

---

## 7. Transfery surowców (`transfer_mode`)

### 7.1 Pojęcia
- **Wolne surowce nadawcy** (`_spare`):
  - Zawsze zostaje **rezerwa na budowę** (następny krok plus pierwszy niezarezerwowany wpis u budowniczego) i zapas na stałą rekrutację (`recruitment_reserve_hours`).
  - Wioska ograniczona kolejką (albo z osiągniętym celem) oddaje całą resztę, bo jej kolejka i tak nie zdąży jej użyć.
  - Wioska ograniczona surowcami oddaje tylko to, co przekracza koszt wszystkich jej planowanych kroków, albo to, co w ciągu 2 h by się przelało.
  - Przy ujemnym bilansie zboża zostaje zapas zboża na 2 h.
- **Kiedy odbiorca może wydawać** (`idle_hours`):
  - od razu, jeśli kolejka jest wolna, budowniczy ma wolne miejsce albo jest wpis do zarezerwowania;
  - w przeciwnym razie po bieżącej budowie **i wszystkich opłaconych wpisach** u budowniczego.
- **Miejsce u odbiorcy** (`_room`): pojemność minus stan, minus to, co już jedzie, minus jego własna produkcja do momentu, gdy zacznie wydawać, i jeszcze 1 h zapasu.
- **Potrzeby odbiorcy** (`_need`): koszt planowanych kroków minus stan, minus to, co jedzie, minus to, co wioska sama wyprodukuje, zanim zwolni się jej kolejka.
- **Bilans sieci** (`network_balance`): kto ma nadwyżkę, kto niedobór i czy kupcy nadążają z przewozem. Brakujących kupców uzupełnia rozbudowa targowiska.

### 7.2 Przebiegi (`plan_transfers`), w tej kolejności
1. **Odblokowanie kolejki:** pierwszemu niezarezerwowanemu wpisowi u budowniczego zabraknie surowców, gdy kolejka do niego dojdzie. Najbliższa wioska wysyła brakujące wolne surowce.
   - Pomijane, gdy wioska wyprodukuje brak w 15 min albo wysyłka byłaby mniejsza niż 300.
2. **Pożyczka z rezerwy sąsiada:** jeśli po przebiegu 1 dalej brakuje, sąsiad może pożyczyć ze swojej rezerwy na budowę. Warunki:
   - pożycza najwyżej tyle, ile sam wyprodukuje w 30 min;
   - kolejka odbiorcy stałaby co najmniej 2 razy dłużej, niż przesunie się budowa sąsiada;
   - sąsiad sam nie czeka na surowce.
3. **Przyspieszenie lub nadwyżka:**
   - wysyłka przyspieszy następny krok odbiorcy o co najmniej `transfer_min_saved_minutes` (30), z czasem drogi kupców i czasem zajętej kolejki odbiorcy;
   - **albo** przenosi wolne surowce, co najmniej `transfer_min_amount` (Twoje 1000), do wioski ograniczonej surowcami, która **zacznie je wydawać w ciągu 1 h**. Do wioski z długo zajętą kolejką nadwyżka nie idzie, bo leżałaby bezczynnie.
4. **Przepełnienie:** gdy nadawcy w ciągu 2 h coś się przeleje, nadmiar idzie do najbliższej wioski, która ma miejsce, niezależnie od jej kolejki.

Tryby: „zalecenia” pokazuje tabelę wysyłek, „automat” klika je na targowisku.

---

## 8. Nadmiar surowców → rekrutacja

- **Kiedy:** gdy magazyn i tak się przeleje w ciągu `overflow_hours` (1 h), a budowa nie może tych surowców użyć (kolejka zajęta, budowniczy pełny). Automat szkoli wtedy jednostki z nadmiaru.
- **Co zostaje nietknięte:** rezerwa na następny krok i to, co odpłynie w zaplanowanych wysyłkach.
- **Jaka jednostka:**
  - wybrana w tabeli „Nadmiar surowców” (`overflow_units`);
  - albo, przy `overflow_auto`, automatycznie ta, która spala nadmiar, marnując najmniej pozostałych surowców.
- **Limit:** kolejka rekrutacji w budynku najwyżej `overflow_max_queue_hours` (24 h).

---

## 9. Bezpieczeństwo działania

- **Watchdog:** cykl zawieszony dłużej niż 4 min jest porzucany i startuje nowy automat. Zrzut stosu trafia do dziennika.
- **Przerwy po błędzie:** po nieudanej akcji wioska ma 10 min przerwy, a gdy dane gry się jeszcze odświeżały, tylko 30 s.
- **Karta „lane”:** jedna, oznaczona. Stare karty z poprzednich uruchomień są zamykane.
- **Automatyczne połączenie:**
  - przy starcie aplikacja uruchamia przeglądarkę (odłączoną, więc restart aplikacji jej nie zamyka);
  - otwiera stronę gry;
  - łączy się ponownie po utracie połączenia.
- **Budowa i rekrutacja się wykluczają:** po włączeniu automatu budowy automat w zakładce Rekrutacja się wyłącza. Rekrutacja dostaje wtedy tylko nadmiar (punkt 8) i stałe szkolenie wojska (punkt 10).

---

## 10. Wojsko (sekcja „Wojsko: koszary i stajnia”)

- **Kolejność wiosek:** lista wiosek w ustalonej kolejności (u Ciebie Aden, potem Dion). Każda ma jednostkę do szkolenia w koszarach i w stajni oraz docelowy poziom budynków (20).
- **Która wioska jest aktywna:** pierwsza na liście. Kolejna dołącza, gdy wszystkie wybrane budynki poprzedniej mają kolejkę szkolenia co najmniej `military_saturated_hours` (Twoje 12 h).
- **W aktywnej wiosce:**
  - koszary i stajnia rosną w planie (punkt 3.8);
  - automat dosyła szkolenie, aby kolejka sięgała `military_queue_hours` (24 h);
  - szkolenie jest płacone tylko z tego, czego budowa nie potrzebuje teraz, czyli poza rezerwą na następny krok i odblokowanie kolejki;
  - wolne surowce dzielone są po równo między budynki;
  - automat nie szkoli partii mniejszej niż 5 jednostek.

---

## 11. Bohater (`hero_production_auto`)

- **Rachunek:** co 10 min automat liczy dla każdego z 5 ustawień produkcji bohatera (wszystkie surowce albo jeden z czterech), po ilu godzinach produkcji całej sieci najrzadszy surowiec pokryje wszystko, co zostało do celów (pola plus planowane magazyny i wojsko).
- **Ilości:** jeden surowiec daje 4 razy więcej niż jeden surowiec przy „wszystkich”. Ilości automat odczytuje z okna bohatera, teraz 1 080 albo 4 320.
- **Kiedy zmienia:** wybiera najlepsze ustawienie i przestawia je kliknięciami (Atrybuty → Hero production → Save changes), ale tylko przy zysku co najmniej 3% i nie częściej niż co 2 h.
- **Kiedy przestaje:** po osiągnięciu wszystkich celów.
- **Przestój we wsi bohatera:** gdy kolejka wioski, w której jest bohater (Aden), stoi pusta i czeka na surowce do następnego kroku, bohater na chwilę produkuje to, czego brakuje (np. glinę). Warunek: przestój skraca się co najmniej o 3 min. Takie przełączenie jest możliwe najwyżej co 10 min. Gdy budowa ruszy, bohater od razu wraca do najlepszego ustawienia długoterminowego, bez czekania 2 h.

---

## 11a. Ochrona przed grabieżą (`raid_protection`, `raid_lead_minutes`)

- **Skąd wie o ataku:** z nasłuchu zakładki Ataki (ruchy wojsk w grze, na żywo). Liczą się atak, napad i oblężenie, także od zbójców. Zwiad nie zabiera surowców, więc jest pomijany.
- **Kiedy działa:** gdy atak dotrze do wioski w ciągu ustawionego czasu (domyślnie 10 min). Automat budzi się na początku tego okna, a potem sprawdza wioskę co minutę aż do ataku.
- **Co robi, po kolei:**
  1. Zwykły cykl buduje i rezerwuje u budowniczego, co się da. To już zabiera surowce z magazynu.
  2. Resztę surowców, które da się złupić, wysyła do najbliższych własnych wiosek, które nie są atakowane i mają miejsce w magazynie. Gdy kupców jest za mało, najpierw jadą drewno, glina i żelazo, a zboże na końcu. Surowce u kupców są bezpieczne. Kolejne kursy idą, gdy kupcy wrócą.
  3. Z tego, czego nie dało się wysłać, szkoli jednostkę z sekcji nadmiaru (u Ciebie falanga) albo dobraną automatycznie.
  4. Gdy nic się nie da zrobić, zapisuje w dzienniku, ile zostaje do złupienia i dlaczego (brak targowiska, kupców albo miejsca).
- **Co zostaje w wiosce:** to, co chroni kryjówka (żadna z Twoich wiosek jej teraz nie ma), oraz przy ujemnej produkcji zboża zapas zboża na 2 h.
- **Transporty do atakowanej wioski:** automat nie planuje wysyłki, która dotarłaby do wioski w ciągu 2 h przed atakiem, bo zabrałby ją atakujący.
- **Po ataku:** surowce zostają w wioskach, do których pojechały. Zwykłe transfery (punkt 7) odeślą je tam, gdzie będą potrzebne.
- Wioski z listy „bez działań” są pomijane.

---

## 11b. Bank zboża (`crop_bank`, `crop_bank_fill_percent`)

Zboże rośnie szybciej, niż jest potrzebne, a jednostki w tym świecie nie kosztują zboża. Bank zbiera więc nadwyżkę zboża w stolicy i zamienia ją u handlarza NPC na to, czego brakuje.

- **Spichlerze:** w stolicy (Aden) oba spichlerze idą do maksymalnego poziomu 20, czyli 2 × 120k = 240k. Spichlerze mają pełne pierwszeństwo: dopóki nie są na maksimum, w planie stolicy są przed wojskiem, budynkiem głównym i resztą budynków. Nowego spichlerza automat nie stawia.
- **Magazyn razem ze spichlerzami:** zmielone zboże musi się zmieścić. Jeśli magazyn stolicy mieści mniej niż 1/3 pojemności spichlerzy plus 2 h produkcji, krok banku to najpierw magazyn (przy 240k spichlerzy: około 100k, czyli poziom 19–20).
- **Zbieranie:** pozostałe wioski wysyłają do stolicy zboże, którego same nie potrzebują (te same „wolne surowce” co w punkcie 7.1), aż spichlerz będzie pełny. Liczy się też produkcja stolicy w czasie drogi kupców. Ta wysyłka idzie po wysyłkach na potrzeby budowy, więc ich nie blokuje.
- **Bank nie oddaje zboża:** stolica nie wysyła zboża jako nadwyżki ani przy przepełnieniu, bo to zboże jest do zmielenia.
- **Bez złota na ratowanie zboża:** w stolicy automat nie płaci za dokończenie budowy tylko po to, żeby spichlerz nie przepełnił się zbożem (punkt 5.3). Jedno mielenie za 5 złota zamienia całą porcję, a dokończenie za 2 złota ratuje kilka tysięcy.
- **Kiedy mieli (jak najmniej złota):** mielenie kosztuje tyle samo niezależnie od ilości, więc automat czeka na największą porcję:
  - zboże musi dojść do progu (domyślnie 99% pojemności spichlerzy);
  - jeśli rozbudowa spichlerza skończy się, zanim spichlerz by się przepełnił (z zapasem 15 min), automat czeka na większy spichlerz;
  - nigdy nie mieli tylko dlatego, że kolejka stoi.
- **Jak mieli:** targowisko → „NPC merchant”. Automat wpisuje zboże i je blokuje, wpisuje drewno i je blokuje, wpisuje glinę, a żelazo dostaje resztę. Sprawdza podział w formularzu i dopiero wtedy klika „Convert” i potwierdza złoto (5).
  - Zostaje zboże na następne kroki budowy, rezerwa rekrutacji i 2 h zboża przy ujemnej produkcji.
  - Podział, w granicach wolnego miejsca w magazynie stolicy (godzina produkcji zostaje wolna):
    1. Najpierw to, czego zabraknie w najbliższych 6 h budowy: dla każdego planowanego kroku to, czego brakuje w chwili, gdy ma ruszyć. Potrzeby stolicy są liczone w całości, a innych wiosek tylko tyle, ile kupcy stolicy zdążą w tym czasie rozwieźć.
    2. Reszta w proporcji do tego, czego sieci brakuje do celów.
  - Wymiana idzie tylko wtedy, gdy zamienia co najmniej 30k (`crop_bank_min_amount`) i złoto po niej nie spada poniżej rezerwy. Wydane złoto liczy `crop_bank_gold_spent`, osobno od budżetu dokończeń.
- **Po mieleniu:** drewno, glina i żelazo w stolicy idą na jej budowę i są rozsyłane zwykłymi transferami (punkt 7) do wiosek, którym brakuje.

---

## 12. Mapa zależności

```
odczyt gry ──► plan wioski ◄── ustawienia (cele, wagi, magazyn, budynek główny, bonusy)
                  │  ▲
                  │  └── wojsko (aktywne wioski i poziomy)
                  ▼
        bilans sieci ──► targowisko w planie
                  │
                  ▼
   transfery (odblokowanie → pożyczka → przyspieszenie/nadwyżka → przepełnienie)
                  │                 ▲
                  │                 └── wolne surowce = stan − rezerwa na budowę
                  ▼
   budżet złota ──► oferty dokończeń (+ ratowanie magazynu)
                  │
                  ▼
   działania: dokończenie → budowniczy (rezerwacja, podmiana, dopełnienie)
              → handlarz → rozbudowa/nowy budynek
              → szkolenie wojska → bohater → ochrona przed grabieżą
              → bank zboża (mielenie) → wysyłki → nadmiar w rekrutacji
```

Najważniejsze zależności:
- **Rezerwa na budowę** (następny krok plus pierwszy niezarezerwowany wpis u budowniczego) chroni surowce przed wysyłką, szkoleniem wojska i spalaniem w rekrutacji.
- **Czas zajętej kolejki** (`idle_hours`) decyduje, czy wiosce warto cokolwiek wysyłać i ile zmieści, zanim zacznie wydawać.
- **Wagi rzadkości** wpływają na kolejność pól i na wybór bohatera. Handlarz na „automat” wyłącza wagi.
- **Budżet złota** wymaga, żeby na następny krok były surowce. Transfery, pożyczki i handlarz pośrednio decydują więc, kiedy złoto się opłaca.
- **Wojsko** zabiera kolejkę (koszary i stajnia w planie) i surowce (szkolenie). Dotyczy tylko aktywnych wiosek.

---

## 13. Ustawienia (`config/settings.json`, sekcja `build`)

| Ustawienie | Domyślnie | Twoje | Opis |
|---|---|---|---|
| `enabled` | wył. | wł. | automat budowy |
| `capital_target` / `village_target` | 14 / 10 | 14 / 10 | cel pól |
| `disabled_villages` | — | — | wioski bez działań |
| `main_building` | wł. | wł. | budynek główny w wioskach ograniczonych kolejką |
| `balance_resources` | wł. | wł. | wagi rzadkości |
| `storage_hours` | 8 | 12 | magazyn na tyle godzin produkcji |
| `bonus_buildings` | wł. | wł. | tartak, cegielnia, huta, młyn, piekarnia |
| `hero_production_auto` | wł. | wł. | przestawianie bohatera |
| `raid_protection` / `raid_lead_minutes` | wł. / 10 | wł. / 10 | ochrona przed grabieżą i jej okno |
| `crop_bank` / `crop_bank_fill_percent` / `crop_bank_min_amount` | wył. / 99 / 30 000 | wł. / 99 / 30 000 | bank zboża w stolicy |
| `transfer_mode` | zalecenia | automat | wysyłki |
| `transfer_min_saved_minutes` | 30 | 30 | minimalne przyspieszenie wysyłką |
| `transfer_min_amount` | 2000 | 1000 | minimalna wysyłka nadwyżki |
| `recruitment_reserve_hours` | 2 | 2 | zapas na stałą rekrutację |
| `npc_mode` / `npc_min_saved_minutes` | zalecenia / 60 | zalecenia / 59 | handlarz |
| `finish_mode` | zalecenia | automat | dokończenia |
| `finish_free` / `_reduced` / `_full` | wł. / wył. / wył. | wł. / wł. / wył. | dozwolone ceny |
| `finish_free_minutes` | 5 | 5 | próg darmowy |
| `finish_reduced_minutes` / `_min_minutes` | 120 / 60 | 119 / 110 | okno ceny 1 złota |
| `finish_full_min_minutes` | 180 | 180 | od kiedy 2 złota |
| `gold_budget` / `gold_budget_spent` | 0 / 0 | 225 / 1 | budżet i wydane |
| `gold_reserve` | 50 | 50 | nietykalne złoto |
| `overflow_finish_per_gold` | 2000 | 2000 | ratowanie magazynu złotem |
| `master_builder_slots` / `master_replan` | 4 / wł. | 4 / wł. | budowniczy |
| `overflow_units` / `overflow_auto` | — / wł. | Aden, Giran, Dion: falanga / wł. | nadmiar → rekrutacja |
| `overflow_hours` / `overflow_max_queue_hours` | 1 / 24 | 1 / 24 | horyzont i limit kolejki |
| `military_villages` | Aden, Dion | Aden, Dion | wojsko |
| `military_queue_hours` / `military_saturated_hours` | 24 / 20 | 24 / 12 | kolejka szkolenia i próg kolejnej wioski |

---

## 14. Gdzie w kodzie

| Plik | Zawartość |
|---|---|
| `src/core/village_builder.py` | planer: plan wioski, wagi, magazyny, bonusy, nowe budynki, transfery, pożyczki, budżet złota, budowniczy, nadmiar, bohater, ochrona przed grabieżą, bank zboża |
| `src/core/build_automation.py` | cykl automatu: odczyt, plan, działania, wojsko, bohater, ochrona przed grabieżą, wysyłki, nadmiar |
| `src/core/build_clicker.py` | odczyt stanu gry i wszystkie kliknięcia (budowa, dokończenie, handlarz, handlarz NPC w targowisku, wysyłka, budowniczy, rekrutacja, nowy budynek, bohater) |
| `src/app.py` (`VillageBuildWidget`) | zakładka Budowa: tabele, ustawienia, dziennik |
| `src/config/settings.py` (`BuildSettings`) | ustawienia i wartości domyślne |
| `tests/test_village_builder.py` | testy planera |
