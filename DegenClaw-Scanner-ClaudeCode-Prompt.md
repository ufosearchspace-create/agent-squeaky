# DegenClaw Competitor Scanner — Claude Code Implementation Plan

**Projekt:** Bot koji skenira DegenClaw takmičare i klasificira ih kao bot ili čovjek  
**Agent koji implementira:** Claude Code  
**Datum:** 30. mart 2026

---

## PRAVILA ZA AGENTA — OBAVEZNO POŠTOVATI

1. **Sve mora biti empirijski dokazivo.** Ne pretpostavljaj da API vraća nešto — prvo pozovi endpoint, provjeri response, pa tek onda piši kod koji ga parsira. Ako nisi siguran u strukturu podataka, napravi test poziv i logiraj response.

2. **Ne halluciniraj.** Ako ne znaš kako API radi, provjeri dokumentaciju ili napravi test call. Nemoj izmišljati endpoint nazive, response strukture ili parametre. Ako nešto ne postoji ili ne radi — reci to eksplicitno.

3. **Kvaliteta koda, ne dužina.** Čist, čitljiv kod sa jasnim imenovanjem. Bez over-engineeringa. Bez nepotrebnih abstrakcija. Jedna funkcija radi jednu stvar. Komentiraj ZAŠTO, ne ŠTA.

4. **Prije svakog koraka i pusha — pokreni kompletnu sigurnosnu provjeru.** Vlasnik ima instaliran sigurnosni audit plugin/dodatak. PRIJE SVAKOG COMMITA I PUSHA: pokreni audit, preglej rezultate, fixaj sve pronađene probleme, pa tek onda pushaj. Ovo nije opcionalno. Svaki push bez prethodnog audita je neprihvatljiv.

5. **Na kraju — kompletan finalni audit.** Kad je SVE gotovo i deployano, napravi kompletnu sigurnosnu provjeru cijelog codebasea: dependencies, env handling, SQL injection, API key exposure, rate limiting, error handling, edge cases, input validation. Dokumentiraj rezultate audita.

6. **Nikakvi hardkodirani sekreti.** Svi API ključevi, tokeni i credentials idu u environment varijable. Nikad u kodu, nikad u commitima, nikad u logovima.

---

## INFRASTRUKTURA

### Backend (Railway)
- **Runtime:** Python 3.11+
- **Tip:** Worker (bez web servera)
- **Scheduler:** APScheduler
- **Baza:** Supabase (PostgreSQL) — connection string kroz env var
- **Notifikacije:** Telegram bot

### Frontend (Vercel)
- **Framework:** Next.js (ili čisti React, agent odlučuje šta je jednostavnije)
- **Svrha:** Dashboard sa listom agenata, scoring-om, detaljima
- **Auth:** Nepotrebno — ovo je readonly dashboard za vlasnika
- **Data:** Čita direktno iz Supabase (Supabase client sa anon key, RLS disabled ili sa read-only policy)

### Baza (Supabase)
- **PostgreSQL** — koristi Supabase client library
- **Connection:** `DATABASE_URL` env var za Railway backend, Supabase JS client za Vercel frontend

---

## ENVIRONMENT VARIJABLE

### Railway backend
```
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...          # service role key za backend
DGCLAW_API_KEY=dgc_...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
HYPERLIQUID_API_URL=https://api.hyperliquid.xyz/info
```

### Vercel frontend
```
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...  # anon key za frontend readonly
```

---

## BACKEND — KOMPONENTE

### 1. Collector (`collector.py`)

Pokreće se svakih **1 sat** via APScheduler.

**Korak 1:** Dohvati leaderboard
```
GET https://degen.virtuals.io/api/leaderboard?limit=1000
Header: Authorization: Bearer {DGCLAW_API_KEY}
```

Parsira response i za svakog agenta sprema/updatea u `agents` tabelu:
- id, name, wallet_address, token_address, first_seen, last_updated
- performance snapshot (trade_count, win_count, loss_count, pnl)

**Korak 2:** Za svakog agenta — dohvati Hyperliquid trade history
```
POST https://api.hyperliquid.xyz/info
Body: {"type": "userFills", "user": "0xWalletAddress"}
```

**VAŽNO:** Wallet adresa iz DegenClaw leaderboarda možda NIJE ista kao Hyperliquid subaccount adresa. Agent mora prvo empirijski potvrditi:
- Probaj sa `acpAgent.walletAddress` iz leaderboard responsa
- Ako nema podataka, probaj sa `agentAddress`
- Ako ni to, probaj sa `owner.walletAddress`
- Logiraj koji mapping radi i koji ne

Throttle: **max 1 request po sekundi** prema Hyperliquid API.

Spremi svaki trade u `trades` tabelu. Koristi `UNIQUE(agent_id, timestamp_ms, coin, side, size)` za deduplikaciju — ne insertaj duplikate.

**Korak 3:** Za svakog agenta — dohvati forum postove
```
GET https://degen.virtuals.io/api/forums/{agentId}/threads/{threadId}/posts
Header: Authorization: Bearer {DGCLAW_API_KEY}
```

Prvo treba dohvatiti thread IDeve:
```
GET https://degen.virtuals.io/api/forums/{agentId}
```

Spremi postove u `forum_posts` tabelu.

**Error handling:** Svaki API call mora biti wrappan u try/except. Logiraj greške ali nastavi sa sljedećim agentom. Ne smije jedan failani agent srušiti cijeli collection cycle.

### 2. Analyzer (`analyzer.py`)

Pokreće se svakih **6 sati** via APScheduler.

Za svakog agenta sa **>= 10 tradeova** izračunaj 6 dimenzija:

#### D1: Timing regularity (weight: 0.30)

```python
# Iz trades tabele, sortiraj po timestamp_ms
# Izračunaj intervale između uzastopnih tradeova
intervals = [trades[i+1].timestamp_ms - trades[i].timestamp_ms for i in range(len(trades)-1)]

# Coefficient of variation
cv = np.std(intervals) / np.mean(intervals) if np.mean(intervals) > 0 else 1.0

# Score: CV < 0.3 = bot (score 0), CV > 1.5 = čovjek (score 1)
# Linearno skaliranje između
d1_score = min(max((cv - 0.3) / 1.2, 0.0), 1.0)
```

Također provjeri milisekund distribuciju:
```python
ms_parts = [t.timestamp_ms % 1000 for t in trades]
# Ako > 80% završava na .000 = bot signal (subtract 0.2 od score)
```

#### D2: Sleep pattern (weight: 0.25)

```python
# Grupiraj tradeove po satu (UTC)
hour_counts = Counter(datetime.utcfromtimestamp(t.timestamp_ms/1000).hour for t in trades)

# Shannon entropy
total = sum(hour_counts.values())
probs = [count/total for count in hour_counts.values()]
entropy = -sum(p * math.log2(p) for p in probs if p > 0)
max_entropy = math.log2(24)  # = 4.585

# Normaliziraj
normalized_entropy = entropy / max_entropy

# Score: entropy > 0.85 = bot (score 0), entropy < 0.5 = čovjek (score 1)
d2_score = min(max((0.85 - normalized_entropy) / 0.35, 0.0), 1.0)
```

Dodatno: prebroji max consecutive sate BEZ tradea.
```python
# Ako max gap >= 6 sati = human signal (add 0.15 to score)
```

#### D3: Position sizing (weight: 0.15)

```python
sizes = [float(t.size) for t in trades]

# Provjeri koliko % je "okruglo" (djeljivo sa 10, 50, 100, 250, 500)
def is_round(s):
    for divisor in [500, 250, 100, 50, 10]:
        if s % divisor == 0:
            return True
    return False

round_pct = sum(1 for s in sizes if is_round(s)) / len(sizes)

# Score: round_pct > 0.7 = čovjek (score 1), round_pct < 0.1 = bot (score 0)
d3_score = min(max((round_pct - 0.1) / 0.6, 0.0), 1.0)
```

Provjeri i ponavljanje istih veličina:
```python
unique_ratio = len(set(sizes)) / len(sizes)
# unique_ratio < 0.3 = bot signal (velik broj identičnih veličina)
```

#### D4: Reakcija na price events (weight: 0.15)

```python
# Za svaki trade, provjeri da li je bio price spike u prethodnih 5 minuta
# Price data iz Hyperliquid:
# POST {"type": "candleSnapshot", "req": {"coin": "ETH", "interval": "5m", "startTime": ..., "endTime": ...}}

# Izračunaj reaction time od spike do tradea
# Median reaction time < 60s konzistentno = bot
```

**NAPOMENA:** Ovo je najkompleksnija dimenzija. Ako se pokaže da je previše komplikovanog ili API ne vraća dovoljno podataka, postavi score na 0.5 (neutral) i logiraj zašto. Ne halluciniraj rješenje.

#### D5: Forum post analiza (weight: 0.10)

```python
posts = get_posts_for_agent(agent_id)

if len(posts) < 3:
    d5_score = 0.5  # insufficient data
else:
    # Varijabilnost dužine
    lengths = [p.content_length for p in posts]
    length_cv = np.std(lengths) / np.mean(lengths) if np.mean(lengths) > 0 else 0

    # Post-to-trade delay konzistentnost
    # (zahtijeva matching post timestamp sa nearest trade timestamp)
    
    # Score: length_cv < 0.1 = bot template (score 0), > 0.5 = human variability (score 1)
    d5_score = min(max((length_cv - 0.1) / 0.4, 0.0), 1.0)
```

#### D6: Wallet starost (weight: 0.05)

```python
# Iz trades tabele — najraniji trade timestamp kao proxy za wallet aktivnost
# Iz leaderboard response — agent registration date (first_seen u DB)

# Ako wallet ima samo DGClaw aktivnost = vjerovatnije bot (score 0.3)
# Ako wallet ima raznovrsnu aktivnost = vjerovatnije čovjek (score 0.7)

# Za sada: koristi first_seen relativno na competition start
# Ako se pojavio prvi dan = neutral, ako se pojavio kasno = neutral
# Ova dimenzija je slaba — weight je 0.05
```

#### Composite score

```python
composite = (
    0.30 * d1_score +
    0.25 * d2_score +
    0.15 * d3_score +
    0.15 * d4_score +
    0.10 * d5_score +
    0.05 * d6_score
)

if composite < 0.25:
    classification = "BOT"
elif composite < 0.40:
    classification = "LIKELY_BOT"
elif composite < 0.60:
    classification = "UNCERTAIN"
elif composite < 0.75:
    classification = "LIKELY_HUMAN"
else:
    classification = "HUMAN"
```

Spremi u `scores` tabelu sa timestampom.

### 3. Reporter (`reporter.py`)

Pokreće se **nakon svakog Analyzer runa** + **dnevni summary u 08:00 UTC**.

Šalje Telegram poruku sa:
- Ukupan broj skeniranih agenata
- Top LIKELY_HUMAN i BOT lista sa score breakdownom
- Novi agenti od zadnjeg reporta
- Promjene u classification-u
- Top 10 po PnL sa classification

### 4. Main entry point (`main.py`)

```python
from apscheduler.schedulers.blocking import BlockingScheduler

scheduler = BlockingScheduler()
scheduler.add_job(collector.run, 'interval', hours=1)
scheduler.add_job(analyzer.run, 'interval', hours=6)
scheduler.add_job(reporter.daily_summary, 'cron', hour=8, minute=0)
scheduler.start()
```

---

## FRONTEND — VERCEL DASHBOARD

### Stranice

**1. Početna (`/`)**
- Tabela svih agenata sa kolonama: Rank, Name, PnL, Trade Count, Composite Score, Classification, Last Updated
- Sortabilna po svim kolonama
- Filter po classification (BOT / LIKELY_BOT / UNCERTAIN / LIKELY_HUMAN / HUMAN)
- Colour coding: crveno za BOT, narandžasto LIKELY_BOT, sivo UNCERTAIN, zeleno LIKELY_HUMAN/HUMAN

**2. Agent detalj (`/agent/[id]`)**
- Svih 6 dimenzija sa individual score-om
- Vizualizacija:
  - D1: Histogram inter-trade intervala
  - D2: Heatmap aktivnosti po satu (24 kolone)
  - D3: Scatter plot position sizes
- Trade history lista
- Forum post lista (ako postoji)
- Score historija (kako se classification mijenjao tokom vremena)

**3. Nema auth** — ovo je privatni alat, URL je nepoznat javnosti. Ako treba zaštita, dodaj basic password check ili Vercel password protection.

### Tech stack

- Next.js (App Router)
- Supabase JS client (readonly)
- Recharts ili lightweight chart library za vizualizacije
- Tailwind za styling
- Nikakav custom backend — sve čita direktno iz Supabase

---

## SUPABASE SCHEMA

```sql
-- Agents tabela
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    wallet_address TEXT,
    agent_address TEXT,
    token_address TEXT,
    owner_wallet TEXT,
    hl_wallet TEXT,              -- confirmed Hyperliquid wallet (može biti različit)
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    trade_count INTEGER DEFAULT 0,
    win_count INTEGER DEFAULT 0,
    loss_count INTEGER DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    win_rate REAL DEFAULT 0
);

-- Trades tabela
CREATE TABLE trades (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT REFERENCES agents(id),
    timestamp_ms BIGINT NOT NULL,
    coin TEXT NOT NULL,
    side TEXT NOT NULL,
    direction TEXT,              -- Open Long, Close Long, etc.
    price REAL,
    size TEXT NOT NULL,          -- string za punu preciznost
    closed_pnl REAL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(agent_id, timestamp_ms, coin, side, size)
);

-- Indeks za brze upite po agentu i vremenu
CREATE INDEX idx_trades_agent_time ON trades(agent_id, timestamp_ms);

-- Forum postovi
CREATE TABLE forum_posts (
    id TEXT PRIMARY KEY,
    agent_id TEXT REFERENCES agents(id),
    thread_type TEXT,            -- DISCUSSION / SIGNALS
    title TEXT,
    content_length INTEGER,
    created_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ DEFAULT NOW()
);

-- Scores — historija svih scoring runova
CREATE TABLE scores (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT REFERENCES agents(id),
    scored_at TIMESTAMPTZ DEFAULT NOW(),
    d1_timing REAL,
    d2_sleep REAL,
    d3_sizing REAL,
    d4_reaction REAL,
    d5_forum REAL,
    d6_wallet REAL,
    composite REAL,
    classification TEXT,
    flags TEXT[],                -- array of human-readable flags
    trade_count_at_scoring INTEGER
);

-- Indeks za latest score po agentu
CREATE INDEX idx_scores_agent_latest ON scores(agent_id, scored_at DESC);

-- View za frontend — latest score per agent
CREATE VIEW agent_latest_scores AS
SELECT DISTINCT ON (s.agent_id)
    a.id,
    a.name,
    a.wallet_address,
    a.total_pnl,
    a.trade_count,
    a.win_rate,
    s.d1_timing,
    s.d2_sleep,
    s.d3_sizing,
    s.d4_reaction,
    s.d5_forum,
    s.d6_wallet,
    s.composite,
    s.classification,
    s.flags,
    s.scored_at,
    s.trade_count_at_scoring
FROM agents a
LEFT JOIN scores s ON a.id = s.agent_id
ORDER BY s.agent_id, s.scored_at DESC;

-- RLS policy za frontend (readonly)
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON agents FOR SELECT USING (true);

ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON trades FOR SELECT USING (true);

ALTER TABLE forum_posts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON forum_posts FOR SELECT USING (true);

ALTER TABLE scores ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON scores FOR SELECT USING (true);
```

---

## REDOSLIJED IMPLEMENTACIJE

1. **Supabase setup** — kreiraj tabele, indekse, RLS policies
2. **Collector** — dohvati leaderboard, potvrdi wallet mapping za Hyperliquid, dohvati trades
3. **Analyzer** — implementiraj D1-D6, composite scoring
4. **Reporter** — Telegram notifikacije
5. **Main** — scheduler, Railway deploy, potvrdi da collector i analyzer rade
6. **Frontend** — Vercel dashboard sa tabelom i agent detaljima
7. **Finalni audit** — sigurnost, edge cases, error handling

Svaki korak mora raditi i biti testiran prije prelaska na sljedeći.

---

## PODACI KOJE ĆE VLASNIK DOSTAVITI NAKNADNO

- Ime agenta za dashboard (naslov stranice)
- Web adresa (domena za Vercel)
- Supabase URL i ključevi
- Railway env setup potvrda
- Telegram bot token i chat ID
