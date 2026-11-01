# Bug Report (Current Branch: v3.1)

Dato: 2026-02-24  
Scope: `main.py`, `sync/*`, `unifi/*`, tests  
Miljø: Docker-baseret validering

## Executive Summary

- Kritiske blockers fundet: **0**
- Høj prioritet (P1) åbne: **0**
- Medium prioritet (P2) åbne: **2**
- Lav prioritet (P3) åbne: **flere tekniske gældsitems**

## Verificerede rettelser

### BR-001: Sensitive data i logs

- Status: **Fixed**
- Severity: **P1 (Security)**
- Problem: Tokens/passwords kunne skrives til logs ved fejl og debug output.
- Løsning:
  - Central redaction formatter indført.
  - Dækker Authorization, API keys, token/password query params, og `https://user:pass@host`.
- Filer:
  - `sync/log_sanitizer.py`
  - `main.py` (formatter wiring)
  - `tests/test_log_sanitizer.py`
- Verifikation:
  - Test suite indeholder redaction-cases og er grøn i Docker.

### BR-002: Ping subprocess input-hardening

- Status: **Fixed**
- Severity: **P1 (Security/Runtime)**
- Problem: `ping_ip()` accepterede uvalideret input til subprocess-kommando.
- Løsning:
  - IP valideres via `ipaddress.ip_address`.
  - `count`/`timeout` clamped til sikre grænser.
  - `check=False` sat eksplicit.
- Filer:
  - `sync/ipam.py`
  - `tests/test_ipam_ping.py`
- Verifikation:
  - Nye tests for invalid IP og argument-clamping.

### BR-003: Tavse exceptions i kritiske flows

- Status: **Fixed (målrettet)**
- Severity: **P2 (Stability/Observability)**
- Problem: `except ...: pass` skjulte fejl i template sync og session-file checks.
- Løsning:
  - Erstattet med debug/warning logging i relevante kritiske paths.
- Filer:
  - `main.py`
  - `unifi/unifi.py`

### BR-004: Regression fra API re-export cleanup

- Status: **Fixed**
- Severity: **P2 (Compatibility)**
- Problem: Oprydning af imports i `main.py` brød test/import-kontrakter.
- Løsning:
  - Backward-compatible symbol re-exports bevaret i `main.py`.

## Åbne risici (ikke blockers til i morgen, men bør planlægges)

### BR-005: Bred exception-håndtering i stor del af main-flow

- Status: **Open**
- Severity: **P2**
- Beskrivelse:
  - Flere brede `except Exception` bruges stadig i runtime flow.
  - Nogle er bevidste fail-soft mekanismer, men gør root-cause analyse sværere.
- Anbefalet handling:
  - Faseopdeling: erstat brede catches med domænespecifikke exceptions i top 10 hot paths først.

### BR-006: Monolitisk `main.py` (arkitektur-risiko)

- Status: **Open**
- Severity: **P2**
- Beskrivelse:
  - Stor fil med mange ansvar (sync orchestration, API access, IPAM, cleanup, templates).
  - Øger regression-risiko og gør testbarhed dårligere.
- Anbefalet handling:
  - Fortsæt igangværende split i moduler (`sync/*`, `unifi/*`) med små, testdrevne trin.

## Reproduktion / validering

Kør i Docker:

```bash
docker run --rm -v "$PWD":/app -w /app unifi2netbox:v3.1-check sh -lc "pip install -q ruff && ruff check ."
docker run --rm -v "$PWD":/app -w /app unifi2netbox:v3.1-check sh -lc "pip install -q pytest && pytest -q"
docker run --rm -v "$PWD":/app -w /app unifi2netbox:v3.1-check sh -lc "pip install -q bandit && bandit -q -r main.py sync unifi"
```

Forventet resultat:

- `ruff`: all checks passed
- `pytest`: alle tests grønne
- `bandit`: ingen findings i app-kode scope ovenfor

