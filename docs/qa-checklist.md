# QA Checklist (Production Readiness)

Denne checkliste er lavet til release-gating før produktion.

## 1. Build og miljø

- [ ] `docker build` gennemføres uden fejl.
- [ ] `docker compose config` validerer konfigurationen.
- [ ] `.env` er komplet og uden hardcoded test-værdier.
- [ ] `UNIFI_VERIFY_SSL=true` i produktion (medmindre der er en dokumenteret undtagelse).
- [ ] `NETBOX_VERIFY_SSL=true` i produktion (medmindre der er en dokumenteret undtagelse).

## 2. Test i Docker (obligatorisk)

- [ ] Lint: `ruff check .` er grøn.
- [ ] Unit/integration tests: `pytest -q` er grøn.
- [ ] Security scan (app-kode): `bandit -q -r main.py sync unifi` er grøn.
- [ ] Testresultater er dokumenteret i release-notes/PR.

## 3. Sikkerhed

- [ ] Logredaction er aktiv for tokens, passwords, API keys og URL credentials.
- [ ] Ingen credentials i commit-historik, docs eller `.env.example`.
- [ ] NetBox token har mindst nødvendige rettigheder.
- [ ] UniFi auth-mode er valideret (Integration API eller legacy fallback).
- [ ] Session-fil permissions håndhæves (`0600`) ved persistering.

## 4. Funktionel validering

- [ ] Device sync (create/update) virker på mindst ét test-site.
- [ ] Interface sync virker, inkl. create/update/delete flows.
- [ ] DHCP/IPAM flow er valideret med realistiske prefixes.
- [ ] VRF/tenant mapping er valideret for den aktuelle produktion.
- [ ] Cleanup-job er testet i sikkert scope (ikke-destruktiv verifikation først).

## 5. Observability og drift

- [ ] `logs/` mount er aktiv.
- [ ] `data/` mount er aktiv hvis specs-cache skal persisteres.
- [ ] Fejl kan spores via loglinjer uden eksponering af hemmeligheder.
- [ ] Threading-env (`MAX_*_THREADS`) er sat til stabile værdier for miljøet.
- [ ] `SYNC_INTERVAL` er sat så jobs ikke overlapper.

## 6. Release-gate (go/no-go)

- [ ] Ingen åbne P0/P1 bugs.
- [ ] Kendte P2/P3 issues er accepteret og dokumenteret.
- [ ] Rollback-plan er dokumenteret.
- [ ] Driftsansvarlig har godkendt release.

