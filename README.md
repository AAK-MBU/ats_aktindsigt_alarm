# ats_aktindsigt_alarm

ATS-proces der overvåger aktindsigts intake-flow på Automation Server (ATS):

```
OS2Forms ──► os2forms-polling-service ──► ATS-intake-kø ──► ATS-intake-proces ──► aktindsigt-portalen
```

Den finder to slags afvigelser og lægger ét alarm-item pr. afvigelse på sin egen workqueue. Derefter behandles hvert item:
- **Workitems der er gået i stå i intake-køen:** processen genkører dem og sender besked om det.
- **OS2Forms-svar der ikke er nået frem til portalen:** processen sender besked med det led, hvert svar mangler i.

Processen bygger på [ats-process-framework](https://github.com/AAK-MBU/ats-process-framework).

## Flow

```
--queue    overvågede køer ──► workitems gået i stå    ──► stale_<kø>_<id>_<n>
           OS2Forms-svar   ──► ikke i kø / ikke i portal ──► missing_<webform>_<tidspunkt>
                                                            │
                                                            ▼  alarm-køen (processens egen)
--process  stale_item:     in progress/failed → status "new" + mail
                           new, eller over retry-loftet → kun mail
           missing_forms:  mail med de manglende svar og hvor de stoppede
```

| Trin | Flag | Rolle |
|---|---|---|
| Populate | `--queue` | `processes/populate.collect_alarm_items`: bygger alarm-items. References der allerede findes i alarm-køen, springes over. |
| Process | `--process` | `processes/handle_item.handle_item`: genkører og mailer pr. item |
| Finalize | `--finalize` | no-op |

Processen er tænkt kørt med `--queue --process` på et fast skema.

### Workitems der er gået i stå
Et workitem i en af køerne i `MONITORED_QUEUES` regnes som gået i stå, når to ting gælder:
- Dets status er `new`, `in progress` eller `failed`.
- `updated_at` er mere end `MAX_AGE` gammel. Default er 60 min.

Alarm-køen overvåger ikke sig selv. Alarm-køens egne items er historikken, så **hvert stop meldes én gang**:
- Hvis det seneste alarm-item for workitem'et stadig er `new` eller `in progress`, oprettes der ikke et nyt.
- Hvis workitem'et ikke er opdateret, siden det seneste alarm-item blev behandlet, er det samme stop, og der oprettes ikke et nyt.
- Ellers oprettes `stale_<kø>_<id>_<n+1>`.

Ved behandling af et item gælder:
- `in progress` og `failed` sættes til `new` via `PUT /workitems/{id}/status`. ATS låser samtidig elementet op. Det sker, så længe `n <= MAX_RETRIES_PER_ITEM`. Derefter sendes kun en mail om manuel håndtering.
- `new` giver kun en mail om, at køen ikke bliver drevet.

### OS2Forms-svar der ikke er leveret
Hver `FormMapping` i `FORM_MAP` kobler en webform til en række led (`FormDestination`) i flowets rækkefølge:

| Led | Svaret tæller som landet, når |
|---|---|
| `ats_queue` | dets `uuid` eller `sid` (efter `match_on`) står som delstreng i en item-reference i køen, uanset status |
| `aktindsigt` | dets `uuid` står i portalens `GET /api/intake/modtagne` for webformen |

For hver webform hentes alle svar fra OS2Forms. Svar oprettet før `SIDEN` ignoreres, og svar yngre end `MAX_AGE` springes over. Hvert manglende svar meldes **én gang**, ved det første led det ikke er nået til, sammen med det seneste led det nåede (fx `ATS-køen <navn>: status completed`). Et svar, der mangler i køen, meldes altså ikke også som manglende i portalen.

Hver webform med manglende svar giver ét item `missing_<webform_id>_<tidspunkt>`, så der kommer en mail ved hver kørsel, så længe svarene mangler.

## Kør

Kræver Python 3.13 og [uv](https://docs.astral.sh/uv/). `DBCONNECTIONSTRINGPROD` bruges gennem pyodbc og kræver en ODBC-driver til SQL Server.

```sh
uv sync
cp .env.example .env                     # udfyld værdierne
uv run python main.py --queue --process
```

## Konfiguration

### `ats_framework/processes/alarm_config.py`

| Navn | Betydning |
|---|---|
| `PROCESS_NAME` | Processens navn i beskeden på genkørte workitems |
| `MAX_AGE` | Hvor længe et workitem må stå uændret, og hvor gammelt et svar skal være, før det skal være leveret |
| `STALE_STATUSES` / `RETRYABLE_STATUSES` | Statusser der kan gå i stå, og dem der genkøres |
| `MAX_RETRIES_PER_ITEM` | Maks antal alarmer pr. workitem, der fører til genkørsel |
| `INTAKE_QUEUE` | ATS-køen, polling-servicen lægger svarene på |
| `MONITORED_QUEUES` | Workqueue-navne der overvåges. Slås op med `GET /workqueues/by_name/{navn}` |
| `ATS_TIMEZONE` | Tidszonen ATS' tidsstempler uden tidszone, og `SIDEN` uden tidszone, tolkes i |
| `OS2FORMS_BASE_URL`, `OS2FORMS_CREDENTIAL` | OS2Forms-instans og navnet på credentialen med api-key |
| `AKTINDSIGT_BASE_URL`, `AKTINDSIGT_CREDENTIAL` | Portalens backend og navnet på credentialen med intake-API-nøglen |
| `SIDEN` | Skæringsdato (ISO-8601). Svar oprettet før den ignoreres |
| `FORM_MAP` | `FormMapping(webform_id, destinations)` pr. overvåget webform |
| `SUBJECT_PREFIX`, `SUBJECTS` | Emnelinjer i alarm-mails |
| `RECIPIENTS_CONSTANT`, `SENDER_CONSTANT`, `SMTP_SERVER_CONSTANT`, `SMTP_PORT_CONSTANT` | Navne på konstanter i rpa.Constants |

Værdier med præfikset `UDFYLDES_` er pladsholdere. `validate_config()` afviser dem og en ugyldig `SIDEN`, når køen populeres. Portalens værdier kræves kun, når en mapping har portalen som led.

### Miljøvariabler

| Variabel | Påkrævet | Betydning |
|---|---|---|
| `ATS_URL`, `ATS_TOKEN` | ja | ATS-API'et |
| `ATS_WORKQUEUE_OVERRIDE` | lokalt | Id på alarm-køen. I drift vælges den af ATS-sessionen. |
| `DBCONNECTIONSTRINGPROD` | ja | ODBC-forbindelse til RPA-databasen (konstanter og credentials) |
| `OPENORCHESTRATORKEY` | ja | Nøglen, credentials i `rpa.Credentials` er Fernet-krypteret med (læses af `mbu_rpa_core`). Skal være den samme, som credentialerne blev gemt med |
| `AARHUS_ROOT_CERT_PEM` | nej | PEM med ATS' CA. Tilføjes til de offentlige rodcertifikater. |
| `LOCAL_DEVELOPMENT` | nej | `true` sender alle alarm-mails til `TestEmail` |
| `TestEmail` | når `LOCAL_DEVELOPMENT=true` | Modtager af omdirigerede mails |

### RPA-databasen
- **Credentials:**
  - `os2_api` (OS2Forms api-key)
  - den credential `AKTINDSIGT_CREDENTIAL` peger på (aktindsigts intake-API-nøgle i password)
- **Konstanter til alarm-mails:**
  - `rpa_team_email`: modtagere, som JSON-liste eller kommasepareret
  - `e-mail_noreply`
  - `smtp_adm_server`
  - `smtp_port`
- **Konstanter til frameworkets fejlmail:** `Error Email`, `Email Friend`, `smtp_server`.

## Datakilder

| Kilde | Adgang |
|---|---|
| ATS `GET /workqueues/by_name/{navn}`, `GET /workqueues/{id}/items` | læser overvågede køer og alarm-køen |
| ATS `PUT /workitems/{id}/status` | genkører workitems |
| OS2Forms `GET /webform_rest/{webform_id}/submissions` + hvert svars URL | læser svar (`entity.uuid`, `entity.sid`, `entity.created`) |
| Aktindsigt `GET /api/intake/modtagne?webform_id=…&siden=…` med `X-API-Key` | læser modtagne svar (`uuid`, `sagId`) |
| `[rpa].[Constants]`, `[rpa].[Credentials]` via `mbu_rpa_core.RPAConnection` | mail-opsætning og api-nøgler |

## Projektstruktur

```
main.py                        entrypoint: --queue / --process / --finalize
ats_framework/core/            frameworkets løkker; delegerer til processes/
ats_framework/helpers/         ATS-kald, aktindsigt-opslag, RPA-opslag, mail, TLS
ats_framework/processes/       alarm_config, populate, stale_items, missing_forms, handle_item
tests/                         pytest
```

## Test

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Testene kører isoleret. ATS-, OS2Forms-, aktindsigt-, database- og SMTP-kald monkeypatches. CI kører ruff. PR'er til `main` gates af **version-gate** (`.github/workflows/version-gate.yml`): er `version` i `pyproject.toml` ikke højere end på `main`, fejler checket og beder i en PR-kommentar om én label, `major`, `minor` eller `bugfix`. CI skriver så den nye version i `pyproject.toml` og pusher den til PR'ens branch.

## Kendte begrænsninger

- Svaret fra OS2Forms' enkelt-svar-endpoint læses defensivt: både `[{"value": ...}]` og skalarer accepteres, og `created` kan være et Unix-tidsstempel eller ISO. Dets form er ikke verificeret mod produktion.
- Alle item-referencer i de overvågede køer hentes ved hver kørsel. Tiden skalerer derfor med køernes størrelse.
- Portalens `/api/intake/modtagne` spørges også for webforms, hvor alle svar mangler allerede i køen.
