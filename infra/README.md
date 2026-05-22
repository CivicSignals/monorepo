# infra/

Deployment + local-stack definitions. We dogfood the self-host distribution:
the Phase 0 production layout is the same docker-compose stack developers run
locally (doc 06 §10).

| Path                       | Purpose                                                        |
|----------------------------|----------------------------------------------------------------|
| `docker-compose.dev.yml`   | Local dev stack (`make dev`). See `docs/dev-environment.md`.   |
| `docker-compose.yml`       | Single-host production / self-host quickstart. TODO O2.        |
| `nginx/nginx.conf`         | Reverse proxy: `/api` → api, rest → web. TLS added in A4/A5.   |
| `deploy/deploy.sh`         | SSH pull-and-restart deploy, run from CI. TODO A4.            |
| `provision/provision.sh`   | One-shot fresh-VPS host prep (Docker, firewall, swap, backup cron). TODO A4/A5. |

Phase 1 (AWS: ECS/EKS, RDS, ElastiCache, S3) and the Helm chart (TODO O3) are
tracked separately and deferred until MRR justifies the move.
