# Train & Monitor — IBM Attrition

Repo de **(ré)entraînement** et de **monitoring de dérive** du modèle d'attrition,
complémentaire de [`demo-MLops`](https://github.com/semarmehdi/demo-MLops) (qui
contient l'ETL, l'API modèle et le serveur MLflow).

Ce repo porte deux responsabilités, automatisées via **GitHub Actions** :

1. **Entraîner** un `RandomForestClassifier` et l'enregistrer dans le **Model
   Registry MLflow** sous l'alias `challenger`.
2. **Surveiller** la dérive des données de production (prédictions sur S3) avec
   **Evidently**, et **déclencher un réentraînement** quand la dérive dépasse un seuil.

---

## Architecture (boucle fermée)

```
                         (cron / manuel)
                               │
                               ▼
        ┌──────────────────────────────────────┐
        │  monitoring.yaml → run_monitoring.py  │
        │  Evidently : réf (train) vs prod (S3) │
        │  → rapport HTML + drift_share         │
        └───────────────┬──────────────────────┘
                        │ drift_share ≥ seuil ?
              ┌─────────┴─────────┐
           non│                   │oui  (gh workflow run train.yaml)
              ▼                   ▼
     rapport archivé      ┌──────────────────────────┐
     (artefact + S3)      │  train.yaml → train.py   │
                          │  split + tuning + métriques│
                          │  → MLflow alias=challenger │
                          └───────────┬──────────────┘
                                      │ (promotion @production = MANUELLE)
                                      ▼
                     API modèle (demo-MLops) sert @production
```

> La référence de dérive est le **dataset d'entraînement** ; la production est
> l'agrégat **fenêtré** des CSV de prédiction écrits par l'ETL sur
> `s3://demo-mlops-mehdi/data/clean/ibm_predictions/`.

---

## Structure

```
train-repo/
├── .github/workflows/
│   ├── train.yaml              # (ré)entraînement : manuel / push / PR
│   └── monitoring.yaml         # monitoring : cron + manuel, déclenche train.yaml
├── train.py                    # entraînement + éval + enregistrement registry
├── build_current.py            # agrégation fenêtrée S3 + alignement de schéma
├── run_monitoring.py           # rapport Evidently + détection de dérive
├── requirements.txt            # deps entraînement (mlflow, sklearn, ...)
└── requirements-monitoring.txt # deps monitoring (evidently, boto3, ...)
```

---

## `train.py` — ce qu'il fait

- **Split stratifié** 80/20 (l'attrition est déséquilibrée, ~16 % de départs).
- **Gestion du déséquilibre** : `class_weight="balanced"`.
- **Tuning** : `GridSearchCV` (cv=5, scoring **F1**) — désactivable via `TUNE=false`
  pour un entraînement rapide en CI.
- **Métriques sur le test** (jamais vu) : precision, recall, F1, ROC-AUC,
  matrice de confusion + `classification_report`, **loggées dans MLflow**.
- **Enregistrement** dans le Model Registry sous l'alias `challenger`, avec
  `signature` et `input_example` (schéma d'entrée stable = remplaçant *drop-in*
  du modèle servi).

> **Choix de l'algorithme.** RandomForest : robuste au mélange catégoriel/numérique
> du dataset RH, peu sensible au scaling, fournit des probabilités calibrables et
> une importance de variables exploitable — bon compromis performance/explicabilité
> pour un POC d'attrition.

### Variables d'environnement

| Variable              | Défaut       | Rôle                                                |
| --------------------- | ------------ | --------------------------------------------------- |
| `MLFLOW_TRACKING_URI` | —            | URL du serveur MLflow (Space HF)                    |
| `AWS_ACCESS_KEY_ID`   | —            | Accès S3 (artifact store MLflow)                    |
| `AWS_SECRET_ACCESS_KEY` | —          | Idem                                                |
| `AWS_DEFAULT_REGION`  | —            | Idem (ex. `eu-west-3`)                              |
| `REGISTER_ALIAS`      | `challenger` | Alias posé sur la version entraînée                 |
| `TUNE`                | `true`       | `false` → entraînement direct (sans GridSearch)     |
| `N_ESTIMATORS`        | `300`        | Utilisé seulement si `TUNE=false`                   |
| `MIN_SAMPLES_SPLIT`   | `2`          | Utilisé seulement si `TUNE=false`                   |

### Lancer en local

```bash
conda create -n train python=3.11 -y && conda activate train
pip install -r requirements.txt
# .env avec MLFLOW_TRACKING_URI + AWS_*
python train.py
```

Vérifie ensuite dans l'UI MLflow : onglet **Experiments** (run + métriques de test)
et **Models** (nouvelle version `ibm_attrition_detector` avec l'alias `challenger`).

---

## Monitoring — `run_monitoring.py` + `build_current.py`

- `build_current.py` agrège les CSV de prédiction S3 sur une **fenêtre glissante**
  (défaut 24 h) et **aligne le schéma** sur les features de référence (retire la
  cible côté réf, la prédiction côté prod).
- `run_monitoring.py` lance un `DataDriftPreset` Evidently, sauvegarde le **rapport
  HTML** + un `drift_summary.json`, et expose `drift_share` / `drift_detected` à
  GitHub Actions.

> **Ce qu'on mesure (et pas).** Sans labels réels en prod, on suit la **dérive des
> entrées** et la **dérive des prédictions**, pas l'accuracy en ligne (qui
> nécessiterait les départs réels constatés a posteriori).

### Variables d'environnement

| Variable          | Défaut | Rôle                                          |
| ----------------- | ------ | --------------------------------------------- |
| `WINDOW_HOURS`    | `24`   | Fenêtre de prod analysée                      |
| `DRIFT_THRESHOLD` | `0.5`  | Seuil de part de colonnes dérivées (0-1)      |
| `AWS_*`           | —      | Lecture des prédictions + archive des rapports |

> Le bucket de lecture est défini en constante dans `build_current.py`
> (`demo-mlops-mehdi`). L'archive S3 du rapport utilise le secret `S3BUCKETNAME`.

---

## Workflows GitHub Actions

### `train.yaml`
Se déclenche en **manuel** (`workflow_dispatch`, avec hyperparamètres et alias),
sur **push** et sur **pull request** (chemins `train.py` / `requirements.txt`).

### `monitoring.yaml`
Se déclenche en **cron** (quotidien) et en **manuel** (fenêtre + seuil réglables).
Si la dérive dépasse le seuil, il lance `gh workflow run train.yaml`.

**Deux pièges déjà gérés :**
- `gh workflow run` fonctionne avec le `GITHUB_TOKEN` par défaut car
  `workflow_dispatch` est l'une des **exceptions** à la règle « un événement
  déclenché par le `GITHUB_TOKEN` ne crée pas de nouveau run ». Pas de PAT requis.
- Les **crons ne partent que depuis la branche par défaut** (`main`) : le fichier
  doit être mergé sur `main` pour que la planification soit active.

> **Démo.** Pour montrer la boucle en live, lance `monitoring.yaml` en manuel avec
> `drift_threshold=0.1` : la dérive partielle suffit à franchir le seuil et tu vois
> `train.yaml` se déclencher dans l'onglet **Actions**.

---

## Secrets & Variables GitHub

**Repo → Settings → Secrets and variables → Actions** (niveau *Repository*).

| Nom                          | Type   | Utilisé par               |
| ---------------------------- | ------ | ------------------------- |
| `MLFLOW_TRACKING_URI`        | Secret | train                     |
| `AWS_ACCESS_KEY_ID`          | Secret | train + monitoring        |
| `AWS_SECRET_ACCESS_KEY`      | Secret | train + monitoring        |
| `AWS_DEFAULT_REGION`         | Secret | train + monitoring        |
| `S3BUCKETNAME`               | Secret | monitoring (archive HTML) |

> `MLFLOW_TRACKING_USERNAME` / `MLFLOW_TRACKING_PASSWORD` ne sont nécessaires que si
> ton Space MLflow est protégé par une authentification. S'il est ouvert (cas par
> défaut du setup `demo-MLops`), `MLFLOW_TRACKING_URI` + `AWS_*` suffisent.

---

## Promotion en production & évolution

L'entraînement s'arrête volontairement à l'alias `challenger`. La **promotion en
`@production`** (que sert l'API modèle) reste **manuelle** : déplacement de l'alias
dans l'UI MLflow, ou

```python
from mlflow import MlflowClient
MlflowClient().set_registered_model_alias(
    name="ibm_attrition_detector", alias="production", version=<n>
)
```

Un **gate automatique champion/challenger** (comparaison du nouveau modèle au modèle
en production sur un holdout figé, promotion conditionnelle, rollback sinon) est
identifié comme évolution — non implémenté ici par contrainte de temps, discuté à
l'oral.

---

## TL;DR

```bash
# Entraîner et enregistrer un challenger
pip install -r requirements.txt
python train.py

# Vérifier la dérive et (éventuellement) déclencher un réentraînement
pip install -r requirements-monitoring.txt
python run_monitoring.py
```
