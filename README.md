# 🧺 PicNicNow – gespreksgestuurde boodschappenhulp voor Picnic

Plan samen de week, gewoon door erover te praten. PicNicNow luistert mee met het gesprek tussen jou
en je partner. Daaruit maakt het een weekmenu en een boodschappenlijst, met producten die jullie al
bij Picnic kopen, bij voorkeur biologisch. Voor de baby komen er hapjes "met de pot mee" bij, en
een seintje als iets duurs deze week goedkoper is bij AH, Vomar of DekaMarkt. Daarna zet de app de
boodschappen met één tik in je Picnic-mandje. Afrekenen doe je zelf in de Picnic-app.

Het is een kleine webapp die je thuis draait en op beide telefoons opent (zet hem op je beginscherm,
dan werkt hij als een app).

## Wat zit erin

| Wens | Hoe |
|---|---|
| **1. Live gesprek → lijst & gerechten** | Gedeelde chat op beide telefoons (live via websocket). Typ, of **houd 🎙 ingedrukt** om te praten. Met **luistermodus** ligt één telefoon op tafel en schrijft het hele gesprek mee. De assistent (Claude) haakt in na een pauze, of als je "assistent" zegt, en past menu en lijst direct aan. Antwoorden kunnen voorgelezen worden. |
| **2. Gebaseerd op eerdere Picnic-bestellingen** | Je bestelgeschiedenis wordt opgehaald. Bij elk lijst-item kiest de app eerst het product dat jullie al kopen, en vult het daarna aan met Picnic-zoekresultaten. **Vaste boodschappen** volgen jullie bestelritme: "bananen koop je elke 7 dagen, nu 12 dagen geleden". |
| **3. Intensiteitsniveaus 1–5** | **1** vertrouwd (vaak gemaakt) · **2** bekend · **3** kant-en-klaar/maaltijdpakket van Picnic · **4** nieuw maar doordeweeks haalbaar · **5** nieuw & uitdagend. Er zitten 36 recepten in de bibliotheek. Gerechten schuiven vanzelf naar niveau 1–2 als je ze kookt (✅) of markeert. De app stelt ook voor welke gerechten jullie waarschijnlijk al maken, op basis van wat jullie bestellen. |
| **4. Zelf kiezen** | Per product: **auto** (de app kiest), **zelf kiezen** (jij kiest uit de opties, met prijs, bio-label en hoe vaak je het kocht) of **zelf halen** (markt/winkel, komt niet in het mandje). Regels worden onthouden, bv. *tomaat → zelf kiezen*. |
| **5. Gerechten combineren** | Tips per week: hetzelfde ingrediënt in meerdere gerechten, een restje kruiden of zuivel opmaken met een passend gerecht, één keer koken en twee keer eten (bolognese → lasagne, rijst → nasi), en balans (vis 1×/week, vega-avonden, niet 3× pasta). |
| **6. Baby eet mee** | Bij elk gerecht staat of de baby mee kan eten en hoe, bijvoorbeeld "portie eruit vóór zout/ketjap", met de juiste textuur voor de leeftijdsfase. Je krijgt waarschuwingen (honing < 1 jaar, hele noten, ei goed gaar, nitraatrijke groente, rijst/arseen) en ideeën voor ontbijt, lunch en tussendoor per fase. Een **allergenen-tracker** helpt om pinda en ei vóór 8 maanden te introduceren. Baby-lunches kun je in het weekmenu zetten. |
| **7. Biologisch** | Drie standen: geen voorkeur / bio als het niet te veel duurder is / altijd bio als het kan. De productkeuze weegt dit mee. De lijst toont het aandeel bio. |
| **8. Goedkoper elders** | Alleen voor **duurdere producten die jullie vaak kopen**: aanbiedingen bij **AH** (bonus), **Vomar** en **DekaMarkt**, plus structureel lagere vaste prijzen (via de open dataset [Checkjebon](https://github.com/supermarkt/checkjebon)). Prijzen worden per kilo/liter vergeleken, bio wordt met bio vergeleken, en alleen echte besparingen tellen (≥ 10% én ≥ €0,30). |

## Snel starten

```bash
git clone … && cd PicNicNow
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp .env.example .env            # vul in (zie hieronder) – of laat leeg voor demo
python -m picnicnow             # start op http://0.0.0.0:8000
```

Open `http://<ip-van-je-computer>:8000` op beide telefoons (zelfde wifi). Kies wie je bent, en klaar.
Vervolgens kun je hem via "Zet op beginscherm" als app gebruiken.

**Zonder instellingen draait het in demo-modus**, met een voorbeeldcatalogus en een verzonnen
bestelgeschiedenis. Zo kun je alles uitproberen.

### `.env` invullen

| Variabele | Waarvoor |
|---|---|
| `PICNIC_USERNAME`, `PICNIC_PASSWORD` | Je Picnic-account. Vraagt Picnic om een sms-/e-mailcode, dan verschijnt er een invulscherm. Het sessietoken wordt lokaal bewaard. |
| `ANTHROPIC_API_KEY` | Voor de gespreksassistent. Zonder sleutel werkt alles behalve het gesprek. |
| `PICNICNOW_NAMES` | bv. `Kevin,Sanne` |
| `PICNICNOW_BABY_BIRTHDATE` | `JJJJ-MM-DD`; bepaalt de babyfase. Kan ook in de app (⚙︎). |
| `PICNICNOW_ORGANIC` | `0`, `1` of `2` (zie boven) |
| `PICNICNOW_PIN` | Optionele pincode, als je de app niet open op je netwerk wilt hebben |

Alles behalve de Picnic- en API-sleutels kun je ook in de app aanpassen (⚙︎), net als
dieetwensen ("geen varkensvlees"), keukennotities ("airfryer, doordeweeks max 30 min") en een weekbudget.
De assistent houdt daar rekening mee.

Na het invullen: ⚙︎ → **Bestelgeschiedenis ophalen** (of `python -m picnicnow sync`).

## Zo gebruik je het

1. **Zondag op de bank:** open *Gesprek*, tik "🗓 Plan de week" of zeg gewoon wat je wilt
   ("maandag iets snels, woensdag mag het nieuw zijn, Sanne wil vis"). Met luistermodus aan hoef je
   niet om de beurt te typen.
2. **Menu:** zie per dag het gerecht, het niveau, of de baby mee kan eten, en combineertips. Een lege dag
   vul je via *+ avondeten kiezen*, waar je op niveau 1–5 filtert.
3. **Lijst:** *Producten kiezen* koppelt alles aan Picnic-producten. Oranje items kies je zelf, blauwe
   haal je zelf. *Naar Picnic-mandje* zet de rest in je mandje. Daarna afrekenen in de Picnic-app.
4. **Besparen:** *Aanbiedingen verversen* (bijvoorbeeld op maandag, als de nieuwe folders ingaan).
5. **Na het eten:** ✅ bij het gerecht. Daarmee leert de app wat jullie vertrouwde gerechten zijn.

Opdrachtregel: `python -m picnicnow sync` (historie ophalen) · `python -m picnicnow deals`
(aanbiedingen verversen en kansen tonen).

## Goed om te weten

- **Onofficiële koppelingen.** Picnic heeft geen publieke API. PicNicNow gebruikt
  [`python-picnic-api2`](https://pypi.org/project/python-picnic-api2/), dezelfde koppeling die de
  Home Assistant-integratie gebruikt. Albert Heijn gaat via de app-API, zoals gedocumenteerd door
  [appie-go](https://github.com/gwillem/appie-go). Vomar en DekaMarkt worden gelezen van hun
  aanbiedingenpagina's. Supermarkten kunnen die op elk moment veranderen. Werkt een bron niet meer,
  dan slaat de app hem over en meldt dat. De rest blijft gewoon werken.
- **Vomar/DekaMarkt zijn niet live getest.** In de ontwikkelomgeving waren die sites niet bereikbaar.
  De lezer probeert daarom drie methodes (JSON-LD, ingebedde app-data, HTML-kaartjes) en is getest op
  voorbeeldpagina's. Komt er bij jullie niets uit, open dan een issue met de paginabron. Voor DekaMarkt
  kun je ook de app-API gebruiken (`DEKAMARKT_API_ID/KEY/STORE_ID` in `.env`), als je die sleutels zelf
  uit de app haalt.
- **De assistent** draait op Claude (`claude-opus-5-5`, effort `medium`: snel genoeg voor een live
  gesprek). Gesprekken gaan naar de Anthropic-API. Er staan geen wachtwoorden of betaalgegevens in.
- **Spraak** gebruikt de spraakherkenning van je browser (Chrome stuurt audio daarvoor naar Google,
  Safari verwerkt het grotendeels op het toestel). Luistermodus werkt het best in Chrome op Android
  of op een laptop.
- **Babyadvies** volgt het Voedingscentrum ("De eerste hapjes", "Introductie pinda en ei"). Het is een
  hulpmiddel en vervangt het consultatiebureau niet.
- **De app bestelt nooit zelf.** Hij vult alleen je mandje.
- Alle gegevens staan lokaal in één SQLite-bestand (`picnicnow.db`).

## Techniek

```
picnicnow/
  core.py        App: instellingen, database, Picnic, lijst, huishoudprofiel
  assistant.py   Claude-gesprek + 18 tools (lijst, menu, suggesties, baby, aanbiedingen, mandje)
  server.py      FastAPI: REST + websocket voor de gedeelde live sessie
  picnic.py      Picnic-koppeling (echt + demo), 2FA
  history.py     bestelgeschiedenis: sync, vaste boodschappen, ritme
  shopping.py    lijst, productkeuze (historie → bio → prijs), verpakkingen, mandje
  meals.py       receptenbibliotheek, niveaus 1–5, suggesties, weekmenu
  combine.py     restjes, dubbel koken, baby-batch, balans
  baby.py        fases, mee-eten, waarschuwingen, allergenen
  deals/         AH, Vomar, DekaMarkt, Checkjebon + vergelijking
  data/          recepten, babyrichtlijnen, demo-catalogus
  web/           de app (HTML/CSS/JS, geen build-stap)
tests/           pytest (35 tests; Claude en supermarkten worden nagebootst)
```

```bash
pip install -e ".[dev]" && pytest
```
