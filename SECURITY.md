# Security Policy

## Supported Versions
Questo progetto è mantenuto “best effort”. Mantieni aggiornate le dipendenze e Python.

## Reporting a Vulnerability
Se trovi una vulnerabilità:
1. Non aprire issue pubbliche con dettagli exploitabili.
2. Contatta il maintainer/referente del repository e fornisci:
   - descrizione del problema
   - impatto
   - riproducibilità
   - eventuale patch proposta

## Hardening consigliato
- Conserva il token bot solo come Secret/env var (mai in chiaro nel repo).
- Limita i permessi del bot nel gruppo al minimo necessario.
- Su GitHub Actions, valuta runner self-hosted o storage persistente per resilienza.
