"""
Agrégation de la donnée de production pour le monitoring Evidently.

- Récupère les CSV de prédiction sur S3 dans une fenêtre temporelle.
- Charge la donnée de référence (le dataset d'entraînement).
- Aligne les deux DataFrames sur les colonnes de features communes,
  prêtes à être passées à Evidently (DataDriftPreset).

Le timestamp est lu depuis le nom de fichier : 20260530-004111_ibm_predictions.csv
"""

import io
import re
from datetime import datetime, timedelta, timezone

import boto3
import pandas as pd

# --- Configuration prod ---
BUCKET = "demo-mlops-mehdi"
PREFIX = "data/clean/ibm_predictions/"
REFERENCE_URL = (
    "https://full-stack-assets.s3.eu-west-3.amazonaws.com/"
    "Deployment/ibm_hr_attrition.xlsx"
)
TARGET_COL = "Attrition"

# Colonnes ajoutées par l'inférence à exclure de la dérive d'entrée.
# ADAPTE cette liste aux noms réels écrits par ton étape de prédiction.
PREDICTION_COLS = {
    "prediction",
    "predicted",
    "attrition_pred",
    "proba",
    "probability",
    "score",
}
META_COLS = {"_source_file", "_event_ts"}

_TS_RE = re.compile(r"(\d{8}-\d{6})_ibm_predictions\.csv$")


def load_current_window(hours: int = 24) -> pd.DataFrame:
    """Concatène les CSV de prédiction des `hours` dernières heures."""
    s3 = boto3.client("s3")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    frames = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            match = _TS_RE.search(obj["Key"])
            if not match:
                continue
            ts = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(
                tzinfo=timezone.utc
            )
            if ts < cutoff:
                continue
            body = s3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read()
            part = pd.read_csv(io.BytesIO(body))
            part["_source_file"] = obj["Key"]
            part["_event_ts"] = ts
            frames.append(part)

    if not frames:
        raise RuntimeError(
            f"Aucun CSV de prédiction dans les {hours} dernières heures "
            f"sous s3://{BUCKET}/{PREFIX}"
        )

    current = pd.concat(frames, ignore_index=True)
    print(
        f"[INFO] Fenêtre {hours}h : {len(frames)} fichiers, "
        f"{len(current)} lignes agrégées."
    )
    return current


def load_reference() -> pd.DataFrame:
    """Charge le dataset d'entraînement comme distribution de référence."""
    return pd.read_excel(REFERENCE_URL, index_col=0)


def align_for_drift(
    reference: pd.DataFrame, current: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Réduit les deux DataFrames aux colonnes de features communes.

    On retire la cible côté référence et les colonnes de prédiction/méta
    côté prod, afin de comparer des distributions de features comparables.
    """
    ref_features = [c for c in reference.columns if c != TARGET_COL]

    excluded = PREDICTION_COLS | META_COLS | {TARGET_COL}
    cur_features = [c for c in current.columns if c.lower() not in excluded]

    common = [c for c in ref_features if c in cur_features]
    missing = [c for c in ref_features if c not in cur_features]
    if missing:
        print(f"[WARN] Features de réf absentes en prod (ignorées) : {missing}")

    print(f"[INFO] {len(common)} features communes utilisées pour la dérive.")
    return reference[common].copy(), current[common].copy()


if __name__ == "__main__":
    ref = load_reference()
    cur = load_current_window(hours=24)
    ref_aligned, cur_aligned = align_for_drift(ref, cur)
    print(ref_aligned.shape, cur_aligned.shape)
