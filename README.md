# Plateforme DevSecOps sécurisée

Une chaîne CI/CD **GitHub Actions** qui teste, analyse et déploie automatiquement une API Python conteneurisée sur **Kubernetes (Azure AKS)**. Si une faille grave est détectée, le déploiement est **bloqué**.

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
    Déploiement AKS (connexion Azure par OIDC, sans mot de passe)
```

Tous les résultats sont publiés au format SARIF dans l'onglet **Security → Code scanning** du dépôt.

## Mesures de sécurité appliquées

| Domaine | Mesure |
|---|---|
| Pipeline | Permissions minimales par job (`permissions: {}` par défaut), actions épinglées par **SHA** (et non par tag), Dependabot pour les mettre à jour |
| Secrets | Aucun secret longue durée : `GITHUB_TOKEN` éphémère pour GHCR, **OIDC** pour Azure ; Gitleaks sur tout l'historique |
| Code | Semgrep (SAST), validation des entrées via Pydantic |
| Dépendances | Trivy fs + Dependabot, versions épinglées |
| Image | Multi-stage, base `slim`, utilisateur non-root, mises à jour OS, scan Trivy, SBOM |
| Kubernetes | Pod Security « restricted », `readOnlyRootFilesystem`, capabilities supprimées, pas de token de service account, limites CPU/RAM, NetworkPolicy |
| Déploiement | Environnement GitHub `production` (validation manuelle possible) |

## Structure

```
app/                     API FastAPI
tests/                   tests pytest
Dockerfile               image durcie
k8s/                     manifests Kubernetes
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

## Mise en place sur Azure (une seule fois)

Prérequis : [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) installé, puis `az login`.

### 1. Créer le cluster AKS

```bash
RG=rg-devsecops
CLUSTER=aks-devsecops
LOCATION=francecentral

az group create -n $RG -l $LOCATION

az aks create -g $RG -n $CLUSTER \
  --tier free \
  --node-count 1 \
  --node-vm-size Standard_B2s \
  --network-plugin azure \
  --network-policy azure \
  --generate-ssh-keys
```

> Si la taille `Standard_B2s` n'est pas disponible dans ta région ou ton abonnement, liste les tailles possibles avec `az vm list-skus -l $LOCATION -o table`.

### 2. Créer l'identité pour GitHub (OIDC, sans mot de passe)

Remplace `TON_COMPTE/TON_DEPOT` par ton dépôt GitHub.

```bash
REPO=TON_COMPTE/TON_DEPOT

APP_ID=$(az ad app create --display-name gh-devsecops-deployer --query appId -o tsv)
az ad sp create --id $APP_ID

# GitHub pourra s'authentifier uniquement depuis l'environnement "production" de ce dépôt
az ad app federated-credential create --id $APP_ID --parameters "{
  \"name\": \"github-production\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:${REPO}:environment:production\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"

# Droit minimal : récupérer les accès à CE cluster uniquement
AKS_ID=$(az aks show -g $RG -n $CLUSTER --query id -o tsv)
az role assignment create --assignee $APP_ID \
  --role "Azure Kubernetes Service Cluster User Role" \
  --scope $AKS_ID

# Valeurs à copier dans GitHub
echo "AZURE_CLIENT_ID=$APP_ID"
echo "AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID=$(az account show --query id -o tsv)"
```

### 3. Configurer le dépôt GitHub

1. **Settings → Environments → New environment** : `production`. Tu peux y ajouter *Required reviewers* pour valider chaque déploiement à la main.
2. **Settings → Secrets and variables → Actions → Variables** : ajoute `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AKS_RESOURCE_GROUP` (= `rg-devsecops`) et `AKS_CLUSTER_NAME` (= `aks-devsecops`).
   Ce sont des *variables* et non des *secrets* : avec OIDC, ces identifiants ne permettent rien sans le jeton signé par GitHub.
3. **Settings → Code security** : active *Dependabot alerts* et *Code scanning*. Sur un dépôt **privé**, l'onglet Security demande GitHub Advanced Security ; le plus simple est de rendre le dépôt **public**.

### 4. Premier déploiement

Pousse sur `main`. Après le premier push, l'image GHCR est **privée** : AKS ne pourra pas la télécharger (`ImagePullBackOff`). Va dans **ton profil → Packages → le package → Package settings → Change visibility → Public**, puis relance le workflow.

Récupérer l'IP publique :

```bash
az aks get-credentials -g $RG -n $CLUSTER
kubectl -n devsecops-demo get svc demo-api
curl http://<EXTERNAL-IP>/health
```

### Limiter les coûts

```bash
az aks stop  -g $RG -n $CLUSTER   # arrête les nœuds quand tu ne travailles pas
az aks start -g $RG -n $CLUSTER
az group delete -n $RG            # tout supprimer à la fin du projet
```

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
- Secrets applicatifs dans **Azure Key Vault** (Secrets Store CSI Driver)
- Ingress HTTPS (cert-manager + Let's Encrypt)
