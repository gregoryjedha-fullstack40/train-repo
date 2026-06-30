# train-repo — (Ré)entraînement et monitoring

**Livrable de certification**
Titre AIA — Architecte en Intelligence Artificielle
Bloc 4 — MLOps (volet entraînement automatisé et monitoring de dérive)
Auteur : **Ahmed Mehdi SEMAR**

Dépôt complémentaire de [`bloc4_mlops`](https://github.com/semarmehdi/bloc4_mlops)
(ETL, API modèle, serveur MLflow). Ce dépôt porte deux responsabilités, automatisées
via GitHub Actions :

1. **Entraîner** un `RandomForestClassifier` (split stratifié, gestion du déséquilibre,
   tuning, métriques) et l'enregistrer dans le Model Registry MLflow sous l'alias
   `challenger`.
2. **Surveiller** la dérive des données de production (prédictions stockées sur S3)
   avec Evidently, et **déclencher un réentraînement** quand la dérive dépasse un seuil.

---

## Architecture (boucle fermée)

![Boucle monitoring / réentraînement](docs/diagrams/monitoring_loop.svg)

> Source éditable : [`docs/diagrams/monitoring_loop.drawio`](docs/diagrams/monitoring_loop.drawio)
> (ouvrir avec [draw.io](https://app.diagrams.net)).

La référence de dérive est le jeu d'entraînement ; la production est l'agrégat
**fenêtré** des CSV de prédiction écrits par l'ETL sur
`s3://demo-mlops-mehdi/data/clean/ibm_predictions/`.

---

## Structure

```
train-repo/
├── .github/workflows/
│   ├── train.yaml              # (ré)entraînement : manuel / push / PR
│   └── monitoring.yaml         # monitoring : cron + manuel, déclenche train.yaml
├── train.py                    # entraînement + évaluation + enregistrement registry
├── train_old.py                # version initiale conservée à titre de référence
├── build_current.py            # agrégation fenêtrée S3 + alignement de schéma
├── run_monitoring.py           # rapport Evidently + détection de dérive
├── requirements.txt            # dépendances entraînement
└── requirements-monitoring.txt # dépendances monitoring (evidently, boto3, ...)
```

---

## train.py

- **Split stratifié** 80/20 (attrition déséquilibrée, ~16 % de départs).
- **Gestion du déséquilibre** : `class_weight="balanced"`.
- **Tuning** : `GridSearchCV` (cv=5, scoring F1), désactivable via `TUNE=false`
  pour un entraînement rapide en CI.
- **Métriques de test** loggées dans MLflow : precision, recall, F1, ROC-AUC,
  matrice de confusion et `classification_report`.
- **Enregistrement** dans le registry sous l'alias `challenger`, avec `signature`
  et `input_example` (schéma d'entrée stable = remplaçant *drop-in* du modèle servi).

### Variables d'environnement

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `MLFLOW_TRACKING_URI` | — | URL du serveur MLflow (Space HF) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` | — | Artifact store S3 |
| `REGISTER_ALIAS` | `challenger` | Alias posé sur la version entraînée |
| `TUNE` | `true` | `false` → entraînement direct sans GridSearch |
| `N_ESTIMATORS` / `MIN_SAMPLES_SPLIT` | `300` / `2` | Utilisés seulement si `TUNE=false` |

```bash
pip install -r requirements.txt
python train.py
```

---

## Monitoring — run_monitoring.py + build_current.py

- `build_current.py` agrège les CSV de prédiction S3 sur une **fenêtre glissante**
  (défaut 24 h) et **aligne le schéma** sur les features de référence.
- `run_monitoring.py` lance un `DataDriftPreset` Evidently, sauvegarde le rapport
  HTML + un `drift_summary.json`, et expose `drift_share` / `drift_detected` à
  GitHub Actions.

> En l'absence de labels réels en production, le monitoring porte sur la **dérive
> des entrées** et la **dérive des prédictions**, pas sur l'accuracy en ligne.

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `WINDOW_HOURS` | `24` | Fenêtre de prod analysée |
| `DRIFT_THRESHOLD` | `0.5` | Seuil de part de colonnes dérivées (0-1) |
| `AWS_*` | — | Lecture des prédictions + archive des rapports |

```bash
pip install -r requirements-monitoring.txt
python run_monitoring.py
```

---

## Workflows GitHub Actions

**`train.yaml`** — déclenchement manuel (`workflow_dispatch`, avec hyperparamètres et
alias), sur push et sur pull request (chemins `train.py` / `requirements.txt`).

**`monitoring.yaml`** — déclenchement cron (quotidien) et manuel (fenêtre + seuil
réglables). Si la dérive dépasse le seuil, il lance `gh workflow run train.yaml`.

Deux points gérés explicitement :

- `gh workflow run` fonctionne avec le `GITHUB_TOKEN` par défaut, car
  `workflow_dispatch` est l'une des exceptions à la règle « un événement déclenché
  par le `GITHUB_TOKEN` ne crée pas de nouveau run ». Aucun PAT requis.
- Les crons ne se déclenchent que depuis la branche par défaut (`main`).

> **Démo.** Lancer `monitoring.yaml` en manuel avec `drift_threshold=0.1` : la dérive
> partielle suffit à franchir le seuil et `train.yaml` se déclenche dans l'onglet
> Actions.

---

## Secrets GitHub

| Nom | Utilisé par |
| --- | --- |
| `MLFLOW_TRACKING_URI` | train |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` | train + monitoring |
| `S3BUCKETNAME` | monitoring (archive HTML) |

> `MLFLOW_TRACKING_USERNAME` / `MLFLOW_TRACKING_PASSWORD` ne sont nécessaires que si le
> Space MLflow est protégé par authentification.

---

## Promotion en production et évolution

L'entraînement s'arrête volontairement à l'alias `challenger`. La promotion en
`@production` (que sert l'API modèle de `bloc4_mlops`) reste **manuelle** (déplacement
d'alias dans MLflow). Un **gate automatique champion/challenger** (comparaison sur un
holdout figé, promotion conditionnelle, rollback sinon) est identifié comme évolution,
non implémenté ici par contrainte de temps et discuté à l'oral.

---

## Auteur

**Ahmed Mehdi SEMAR** — Livrable Bloc 4 (MLOps), certification AIA — Architecte en
Intelligence Artificielle. Dépôt principal :
[bloc4_mlops](https://github.com/semarmehdi/bloc4_mlops).
