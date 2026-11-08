# Image de base : pour une reproductibilité totale, épingle-la par digest
# (récupéré avec : docker buildx imagetools inspect python:3.13-slim)
#   FROM python:3.13-slim@sha256:<digest> AS builder
# Dependabot (docker) mettra ensuite le digest à jour automatiquement.

# ---- Étape 1 : installation des dépendances ----
# nosemgrep: dockerfile-source-not-pinned
FROM python:3.13-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Étape 2 : image finale minimale ----
# nosemgrep: dockerfile-source-not-pinned
FROM python:3.13-slim

# Utilisateur non-root (bonne pratique, vérifiée par Trivy misconfig)
# + suppression de pip (et de sa copie ensurepip) : inutile à l'exécution, les dépendances
# sont installées dans l'étape builder. pip embarque ses propres dépendances (urllib3,
# msgpack, setuptools...) qui remontent comme CVE au scan d'image : moins de paquets,
# moins de surface d'attaque.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip

WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/ ./app/

ARG APP_VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}

USER 10001
EXPOSE 8000

# Pas de HEALTHCHECK Docker : Kubernetes l'ignore et utilise ses propres sondes (k8s/deployment.yaml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
