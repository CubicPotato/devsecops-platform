# Plateforme DevSecOps sécurisée

Une chaîne CI/CD **GitHub Actions** qui teste, analyse et déploie automatiquement une API Python conteneurisée sur **Kubernetes (k3s auto-hébergé)**, en **GitOps** avec Argo CD. Si une faille grave est détectée, le déploiement est **bloqué**.

## Architecture du pipeline

```
 git push
    │
    ├── Tests unitaires (pytest)
    ├── Secrets ........ Gitleaks (tout l'historique git)
    ├── SAST ........... Semgrep (Python, OWASP Top 10, Dockerfile, GitHub Actions)
    └── SCA / IaC ...... Trivy fs (CVE des dépendances, misconfig Dockerfile + manifests K8s)
            │
            ▼  (les 4 jobs doivent réussir = security gate)
    Build image Docker ─► Trivy image (CVE de l'OS + paquets) ─► SBOM CycloneDX
            │
            ▼  (uniquement sur main)
    Push vers GHCR (tag = SHA du commit)
            │
            ▼
    Commit du nouveau tag dans k8s/deployment.yaml
            │
            ▼  (le cluster tire les changements, GitHub n'a aucun accès au cluster)
    Argo CD (sur le serveur k3s) synchronise le cluster avec Git
```

Tous les résultats sont publiés au format SARIF dans l'onglet **Security → Code scanning** du dépôt.

## Mesures de sécurité appliquées

| Domaine | Mesure |
|---|---|
| Pipeline | Permissions minimales par job (`permissions: {}` par défaut), actions épinglées par **SHA** (et non par tag), Dependabot pour les mettre à jour |
| Secrets | Aucun secret longue durée : `GITHUB_TOKEN` éphémère pour GHCR et le commit de déploiement, **aucun accès au cluster stocké dans GitHub** ; Gitleaks sur tout l'historique |
| Code | Semgrep (SAST), validation des entrées via Pydantic |
| Dépendances | Trivy fs + Dependabot, versions épinglées |
| Image | Multi-stage, base `slim`, utilisateur non-root, mises à jour OS, scan Trivy, SBOM |
| Kubernetes | Pod Security « restricted », `readOnlyRootFilesystem`, capabilities supprimées, pas de token de service account, limites CPU/RAM, NetworkPolicy (appliquée par k3s) |
| Déploiement | **GitOps** avec Argo CD : modèle *pull*, aucun port entrant à ouvrir, `selfHeal` annule toute modification manuelle du cluster ; environnement GitHub `production` (validation manuelle possible) |

## Structure

```
app/                     API FastAPI
tests/                   tests pytest
Dockerfile               image durcie
k8s/                     manifests Kubernetes (synchronisés par Argo CD)
argocd/                  application Argo CD
.github/workflows/       pipeline DevSecOps
.github/dependabot.yml   mises à jour automatiques
```

## Lancer en local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
uvicorn app.main:app --reload        # http://127.0.0.1:8000/docs

docker build -t demo-api .
docker run -p 8000:8000 demo-api
```

Lancer les scans en local (facultatif) :

```bash
semgrep scan --config p/python --config p/owasp-top-ten --config p/dockerfile
trivy fs --scanners vuln,misconfig,secret .
trivy image demo-api
```

## Mise en place du serveur (une seule fois)

Prérequis : un serveur **Debian** accessible en SSH, avec au moins 2 vCPU et 4 Go de RAM.

### 1. Installer k3s

Sur le serveur :

```bash
# Traefik est désactivé : le service demo-api utilise directement le port 80
curl -sfL https://get.k3s.io | sh -s - --disable traefik

# Utiliser kubectl sans sudo
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$USER": ~/.kube/config
chmod 600 ~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
export KUBECONFIG=~/.kube/config

kubectl get nodes   # le nœud doit être "Ready"
```

> Si un pare-feu `ufw` est actif : `sudo ufw allow 80/tcp`, puis autorise le réseau interne de k3s avec `sudo ufw allow from 10.42.0.0/16` et `sudo ufw allow from 10.43.0.0/16`.

### 2. Configurer le dépôt GitHub

1. **Settings → Environments → New environment** : `production`. Tu peux y ajouter *Required reviewers* pour valider chaque déploiement à la main.
2. **Settings → Code security** : active *Dependabot alerts* et *Code scanning*. Sur un dépôt **privé**, l'onglet Security demande GitHub Advanced Security, et Argo CD aurait besoin d'identifiants pour lire le dépôt : le plus simple est de rendre le dépôt **public**.
3. Si tu protèges la branche `main`, le job de déploiement doit pouvoir y pousser son commit (sinon il échoue).

### 3. Premier passage du pipeline

Pousse sur `main`. Le pipeline construit et analyse l'image, la pousse sur GHCR, puis commite son tag dans `k8s/deployment.yaml` (pense à faire `git pull` ensuite).

L'image GHCR est **privée** par défaut : le cluster ne pourra pas la télécharger (`ImagePullBackOff`). Va dans **ton profil → Packages → le package → Package settings → Change visibility → Public**.

### 4. Installer Argo CD

Sur le serveur :

```bash
# Version figée (la dernière publiée au moment de l'installation)
ARGOCD_VERSION=$(curl -s https://api.github.com/repos/argoproj/argo-cd/releases/latest | grep -oP '"tag_name": "\K[^"]+')
echo "$ARGOCD_VERSION"

kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
kubectl -n argocd rollout status deployment/argocd-server

# Argo CD suit désormais le dossier k8s/ de la branche main
kubectl apply -f https://raw.githubusercontent.com/CubicPotato/devsecops-platform/main/argocd/application.yaml
```

Vérifier :

```bash
kubectl -n argocd get applications        # demo-api : Synced / Healthy
kubectl -n devsecops-demo get pods,svc
curl http://<IP_DU_SERVEUR>/health
```

L'application est accessible depuis ton réseau local sur `http://<IP_DU_SERVEUR>/`.

### Interface Argo CD (pratique pour la démo)

Depuis ton PC, ouvre un tunnel SSH puis lance le port-forward dans cette session :

```bash
ssh -L 8080:localhost:8080 <utilisateur>@<IP_DU_SERVEUR>

# dans la session SSH
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Ouvre `https://localhost:8080` (certificat auto-signé), utilisateur `admin`, mot de passe affiché ci-dessus. Change-le ensuite dans *User Info*.

## Démo pour la soutenance : le pipeline bloque une faille

Crée une branche `demo/vuln` et ajoute par exemple dans `app/main.py` :

```python
import subprocess

@app.get("/ping")
def ping(host: str):
    return subprocess.run(f"ping -c 1 {host}", shell=True, capture_output=True).stdout
```

puis ouvre une Pull Request : **Semgrep** signale l'injection de commande et le pipeline échoue. Tu peux faire de même avec une vieille version d'une bibliothèque dans `requirements.txt` (détectée par **Trivy**) ou une fausse clé AWS dans un fichier (détectée par **Gitleaks**). Corrige, pousse : le pipeline repasse au vert.

## Pistes d'amélioration

- Signature de l'image avec **Cosign** et vérification à l'admission (Kyverno)
- **DAST** avec OWASP ZAP sur l'application déployée
- Secrets applicatifs chiffrés dans Git avec **Sealed Secrets** (ou External Secrets)
- Accès depuis Internet sans ouvrir de port, via **Cloudflare Tunnel**
- Ingress HTTPS (cert-manager + Let's Encrypt)
