# Argus — Demomanus (GUI-only)
# VG.1–VG.9 | Alla krav demonstrerade live

---

## SYSTEMBESKRIVNING (säg detta först — ~1 min)

> "Argus är ett multi-agent AI-kodningsverktyg. Det skriver inte bara kod — det letar
> aktivt efter buggar i sin egen output *innan* du ser resultatet. Allt styrs via webbgränssnittet."

**Pipelinen:**

```
Användarinput → Orchestrator
  Kodningsuppgift:  Explorer → Challenger → Coder → 4 Auditorer (parallellt)
  Endast audit:     Explorer → 4 Auditorer (parallellt)
```

**7 agenter:**

| Agent | Roll |
|---|---|
| Orchestrator | Klassificerar intention, koordinerar alla agenter, sammanfattar slutresultatet |
| Explorer | Läser kodbasen och bygger kontext åt övriga agenter |
| Challenger | Kritiserar planen *innan* kod skrivs — fångar dåliga beslut tidigt |
| Coder | Implementerar kirurgiska redigeringar |
| SecurityAuditor | Hittar injection, hårdkodade secrets, autentiseringsbrister |
| BugAuditor | Hittar logikfel, None-referenser, typfel |
| PerformanceAuditor | Hittar O(n²)-loopar, onödiga DB-anrop, blockerande operationer |
| TestAuditor | Kör pytest, flaggar otestade kodstigar |

---

## FÖRBEREDELSE

```bash
python gui.py
# Öppna http://127.0.0.1:7777
```

Håll editorn öppen bredvid — behövs för steg 1 och 2.

---

## STEG 1 — VG.8: Konfiguration + hemligheter

**Öppna i editorn:**

```
argus.yaml      ← all konfiguration: modeller, budgettak, säkerhetsregler, komprimeringsgränser
.env            ← ANTHROPIC_API_KEY=sk-ant-...  (gitignorerad)
.env.example    ← det som finns i git — ingen riktig nyckel
```

> "All konfiguration finns i `argus.yaml`. API-nyckeln läses från `.env` som aldrig committas.
> Ingen hemlighet når versionshanteringen — standard 12-factor-mönster."

Växla till webbläsaren → öppna **Inställningar** (kugghjulet uppe till höger).
Peka på att modeller och budgettak visas och kan ändras live.

**VG.8 ✓** — config-fil + env-var, noll hårdkodade värden

---

## STEG 2 — VG.7: Docker-paketering

**Öppna i editorn:**

```
Dockerfile          ← bygger imagen, installerar beroenden, startar gui.py
docker-compose.yml  ← monterar PROJECT_PATH-volymen, exponerar port 7777, läser .env automatiskt
```

Peka på `ENTRYPOINT` i Dockerfile:

```dockerfile
ENTRYPOINT ["python", "/app/gui.py"]
CMD ["--host", "0.0.0.0", "--no-browser"]
```

> "Hela systemet levereras som en Docker-container. En icke-teknisk person kan köra det med
> ett enda kommando — ingen lokal Python-installation krävs."

```bash
PROJECT_PATH=/sökväg/till/projekt docker compose up
# → http://localhost:7777
```

**VG.7 ✓** — reproducerbar, dokumenterad körväg, inget manuellt steg

---

## STEG 3 — VG.3: Realtidskostnad + budgettak

Peka på **måttstaveln** till höger i GUI:et:

- Tokenbudgetstaplar (hårt tak / mjukt tak)
- Live-kostnad i USD
- Per-agent-uppdelning

Öppna **Inställningar** → Budget. Sätt `dollar_hard_cap` till `0.05`.

Kör i kommandorutan:

```
audit demo/buggy_app
```

**Peka på:** Agenten stoppas mitt i körningen. I aktivitetsloggen visas ett rött märke:

```
🛑 Budget: hard cap reached — agent stopped (agent_name)
```

Ett rött toast-meddelande blinkar uppe till höger.

> "Taket är ett hårt stopp — inte bara en varning. Agenten kan inte fortsätta."

Återställ: sätt `dollar_hard_cap` tillbaka till `2.00` i Inställningar och kör `audit demo/buggy_app` igen för STEG 5.

**VG.3 ✓** — live-kostnad + varningströskel + hårt stopp demonstrerat

---

## STEG 4 — VG.4: Skydd mot farliga kommandon

**BLOCKED — skriv i kommandorutan:**

```
rm -rf /
```

Peka på: svaret visar `BLOCKED` — bash-processen startas aldrig.

**REVIEW — skriv:**

```
rm somefile.txt
```

Peka på: svaret visar `REVIEW` och kräver godkännande innan exekvering.

> "Destruktiva kommandon blockeras på allow/deny-lista-nivå *innan* de når bash.
> Det räcker inte att be modellen uppföra sig — regeln är hård i koden.
> Öppna `argus.yaml` under `safety` för att se listan."

**VG.4 ✓** — aktivt block/gate demonstrerat, täcker VG.5:s bash-körnig

---

## STEG 5 — VG.1 + VG.5 + VG.9: Parallella agenter + bash + autonomi

Kör i kommandorutan:

```
audit demo/buggy_app
```

**Medan det kör — peka på aktivitetsloggen:**

---

### VG.1 — Parallell exekvering + integration

Fyra rader dyker upp nästan *samtidigt*:

```
▶ started  security_auditor
▶ started  bug_auditor
▶ started  performance_auditor
▶ started  test_auditor
```

> "Alla fyra startar inom samma sekund — inte i sekvens. De arbetar parallellt på
> samma kodbas."

När alla är klara visas Orchestrators sammanfattning — ett integrerat resultat som
väger ihop fynd från alla fyra agenter.

> "Orchestrator är den femte agenten som konsumerar deras output och producerar
> det slutgiltiga svaret. Det är det som gör det till ett multi-agent-system — inte
> bara fyra loopar som körs i tur och ordning."

---

### VG.5 — Bash-exekvering

Scrolla i aktivitetsloggen och hitta TestAuditorns verktygsanrop:

```
⚙ bash   pytest demo/buggy_app/test_app.py
```

> "Pytest kördes på riktigt via bash. Resultatet — misslyckade tester — syns i fynden."

---

### VG.9 — Agentautonomi (ReAct-loop)

Peka på sekvensen för en agent:

```
▶ started
⚙ tool_call  read_file
✓ tool_result
"text" (agentens resonemang)
⚙ tool_call  read_file  (annan fil)
✓ tool_result
✔ finished
```

> "Modellen bestämmer själv varje iteration om den ska göra ett till verktygsanrop
> eller returnera sitt svar. Det är inte ett fast skript — agenten resonerar och väljer."

---

**Förväntade fynd:**

```
[CRITICAL] SQL-injektion — /users-endpoint       (SecurityAuditor)
[HIGH]     Hårdkodad API-nyckel — rad 18          (SecurityAuditor)
[HIGH]     O(n²) nästlad loop — /stats-endpoint   (PerformanceAuditor)
[MEDIUM]   Ohanterad None — /process-endpoint     (BugAuditor)
[LOW]      Misslyckat test — test_process_data     (TestAuditor)
```

**VG.1 ✓ | VG.5 ✓ | VG.9 ✓**

---

## STEG 6 — VG.6: Partiell filredigering

Kör i kommandorutan:

```
fix 1
```

_(Fixar det första kritiska fyndet från auditen — SQL-injektion eller hårdkodad nyckel)_

Peka på aktivitetsloggen:

- Verktygsanropet `⚙ edit_file` visas med filnamn
- Klicka på raden för att expandera — sök-ersätt-blocket syns
- Bara de berörda raderna ändras

> "Redigeringsverktyget gör sök-ersätt, inte hel-filsöverskrivning. Det misslyckas om
> måltexten inte hittas — agenten kan inte råka skriva på fel ställe."

**VG.6 ✓** — partiell redigering demonstrerad

---

## STEG 7 — VG.2: Kontexthantering

Kör i kommandorutan:

```
stats
```

Peka på per-agent tokenantal i svaret och i måttstaveln.

Öppna `argus.yaml` i editorn, peka på:

```yaml
context:
  max_history_tokens: 50000
  compaction_threshold: 500
  compaction_model: claude-haiku-4-5-20251001
  max_context_injection_pct: 0.30
```

> "Tre mekanismer skyddar kontextfönstret:
>
> 1. **Komprimering** — verktygsoutput över 500 tokens sammanfattas automatiskt av Haiku
> 2. **Glidande historiefönster** — äldre meddelanden trimmas, filredigeringar prioriteras
> 3. **Injektionstak** — kontext som skickas mellan agenter max 30 % av budgeten"

Peka på de **lila märkena** i aktivitetsloggen från STEG 5-körningen:

```
⚡ Tool output compacted (tier 1)
```

Kör sedan `stats` igen och peka på den **andra tabellen**:

```
### ⚡ Compaction calls (Haiku — proof it executed)
| Compacted | Tokens used | Cost | Calls |
```

> "Det här är Haikus räkning — ett riktigt API-anrop med verklig kostnad.
> Det går inte att fejka. Haiku läste verktygsoutputen och skrev en sammanfattning."

Klicka på **⚡ Compact**-knappen i toppraden för manuell triggning.

> "Komprimering sker automatiskt vid varje stort verktygsanrop, men kan också triggas manuellt."

**VG.2 ✓** — konkret mekanism demonstrerad med verklig Haiku-kostnad som bevis

---

## STEG 8 — Full kodningsuppgift (hela pipelinen live)

Kör i kommandorutan:

```
add input validation to the /process endpoint in demo/buggy_app/app.py
```

Peka på aktivitetsloggen medan det körs:

1. `explorer` — läser filer, bygger kontext
2. `challenger` — kritiserar planen *innan* kod skrivs
3. `coder` — implementerar med `edit_file`
4. Alla 4 auditorer startar automatiskt på det ändrade — Auto-Audit

> "Challenger är det som skiljer Argus från ett enkelt kodverktyg. Den ifrågasätter
> planen innan en enda rad skrivs — fångar arkitekturmisstag, inte bara
> implementationsfel."

---

## DEL 2 — STYRKOR, SVAGHETER, ANVÄNDNINGSFALL (~4 min)

---

### Styrkor

**1. Agenten granskar sin egen kod**
De flesta AI-kodverktyg skriver kod och lämnar det åt dig att hitta felen.
Argus kör automatiskt fyra specialiserade granskare på varje kodändring — du
ser resultatet *inklusive* fynden, inte bara koden.

**2. Parallell specialisering**
Istället för en generalist-agent som gör allt kör Argus fyra granskare
simultant med varsitt fokusområde. Det ger bredare täckning på kortare tid.

**3. Challenger som arkitektoniskt filter**
Challenger-agenten är ett extra lager innan implementation. Den fångar
"fel plan" — inte bara "dålig kod". Det är ett designval som minskar kostsamma
omskrivningar.

**4. Hårda gränser för kostnad och säkerhet**
Budgettaket är ett hårt stopp, inte en rekommendation. Blocklist för bash-kommandon
är kod, inte promptinstruktioner. Systemet kan inte "övertygäas" att bryta dem.

**5. Full synlighet i realtid**
Varje verktygsanrop, varje iteration, varje token — synligt i aktivitetsloggen
medan det händer. Inte en svart låda.

**6. Portabelt och konfigurerbart**
Docker + YAML-konfiguration + .env-hemligheter. Fungerar på alla maskiner,
alla modeller kan bytas per agent, alla gränser kan justeras utan kodändringar.

---

### Svagheter

**1. Kvaliteten är bunden till modellvalet**
Haiku är snabbt och billigt men missar kantfall i komplexa flerfilsrefaktoreringar.
Sonnet ger bättre resultat men kostar mer. Det finns ingen "rätt" modell — det är
alltid en avvägning.

**2. Inget persistent minne mellan sessioner**
Varje session börjar från noll. Systemet vet inte vad som granskades igår,
vilka mönster som är vanliga i just det här projektet, eller vilket beslut som
fattades förra veckan. Det är ett grundläggande problem med nuvarande LLM-design.

**3. Skalning mot stora kodbaser**
Explorer måste läsa filer för att förstå projektet. I en kodbas med hundratals
filer är det långsamt och dyrt. Det saknas indexering eller caching — varje session
är kall.

**4. REVIEW-gates avbryter flödet**
När bash-kommandon kräver godkännande pausas hela körningen tills användaren
svarar. I ett automatiserat CI-flöde är det ett problem.

**5. Granskarna hittar ibland samma saker**
Security- och Bug-auditorerna överlappar ibland. Det ger dubbla fynd för
samma problem, vilket kan vara förvirrande i rapporten.

**6. AI kan ha fel med hög konfidens**
Det är det fundamentala problemet med LLM-baserade verktyg. Argus kan missa
buggar, eller flagga kod som korrekt när den inte är det. Det ersätter inte
en erfaren recensent — det är ett filter, inte ett facit.

---

### Bästa användningsfall

1. **Säkerhetsgranskning före merge** — `audit src/` på en PR-branch ger en rankad fyndlista på sekunder
2. **Implementera med inbyggt säkerhetsnät** — kodningsuppgift + auto-audit fångar regressioner direkt
3. **Snabb orientering i okänd kod** — Explorer + Challenger ger en strukturerad bild av riskområden
4. **Kostnadseffektiv AI-assistans** — hårda tak gör det säkert att köra upprepade gånger utan surprises

---

## VG-KRITERIERNAS TÄCKNING

| VG | Kriterium | Var det demonstreras | Bevis |
|---|---|---|---|
| VG.1 | Parallell agentexekvering + integration | Steg 5 | 4 agenter startar simultant, Orchestrator konsumerar deras output |
| VG.2 | Kontexthantering | Steg 7 | Compact-knapp, lila märke, yaml-konfiguration |
| VG.3 | Realtidskostnad + budgettak | Steg 3 | Rött märke + toast när hard cap nås |
| VG.4 | Skydd mot farliga kommandon | Steg 4 | BLOCKED-svar live i GUI |
| VG.5 | Bash-exekvering | Steg 5 | `⚙ bash pytest` i aktivitetsloggen |
| VG.6 | Partiell filredigering | Steg 6 | `edit_file` sök-ersätt expanderat i loggen |
| VG.7 | Docker-paketering | Steg 2 | Dockerfile + docker-compose i editorn |
| VG.8 | Konfiguration + hemligheter | Steg 1 | argus.yaml + .env i editorn |
| VG.9 | Agentautonomi (ReAct) | Steg 5 | tool_call → text → tool_call → end_turn synligt |

**Alla 9 VG-kriterier demonstrerade live. ✓**

---

## TIDSGUIDE

| Avsnitt | Tid |
|---|---|
| Systembeskrivning | 1 min |
| Steg 1–8 | 15–18 min |
| Styrkor / svagheter / användningsfall | 4 min |
| Frågebuffert | 3 min |
| **Totalt** | **~25 min** |

---

## SNABBÅTERSTÄLLNING

```bash
# Återställ budget: Inställningar → dollar_hard_cap = 2.00

# Återställ demo-appen (kräver att den buggiga versionen är committad som HEAD):
git checkout demo/buggy_app/app.py

# Om HEAD är den fixade versionen — kopiera tillbaka manuellt:
# (demo-appen har alltid BUG 1–5 i den körklara versionen)

# Starta om containern med återställd app:
docker compose down && docker compose up --build
```
