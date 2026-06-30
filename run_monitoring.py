"""
Job de monitoring de dérive (data drift) avec Evidently.

Étapes :
  1. Charge la donnée de référence (dataset d'entraînement).
  2. Agrège la prod récente depuis S3 (fenêtre glissante).
  3. Aligne les schémas et lance un DataDriftPreset.
  4. Sauvegarde le rapport HTML + un résumé JSON.
  5. Expose le résultat à GitHub Actions (GITHUB_OUTPUT) pour décider
     du réentraînement.

Variables d'environnement :
  WINDOW_HOURS     Fenêtre de prod analysée, en heures. Défaut 24.
  DRIFT_THRESHOLD  Seuil de part de colonnes dérivées (0-1). Défaut 0.5.
  REPORT_HTML      Chemin du rapport HTML. Défaut drift_report.html.
"""

import json
import os

from evidently import Dataset, DataDefinition, Report
from evidently.presets import DataDriftPreset

from build_current import align_for_drift, load_current_window, load_reference


def find_drift_share(report_dict: dict) -> tuple[float, float]:
    """Récupère (share, count) de colonnes dérivées sans dépendre de l'index."""
    metrics = report_dict.get("metrics", [])
    for metric in metrics:
        name = str(metric.get("metric_id") or metric.get("metric_name") or "")
        if "DriftedColumns" in name:
            value = metric.get("value", {})
            return float(value.get("share", 0.0)), float(value.get("count", 0.0))
    # Repli : la quickstart expose ce métrique en position 0
    value = metrics[0].get("value", {}) if metrics else {}
    return float(value.get("share", 0.0)), float(value.get("count", 0.0))


def main() -> None:
    window_hours = int(os.getenv("WINDOW_HOURS", "24"))
    threshold = float(os.getenv("DRIFT_THRESHOLD", "0.5"))
    out_html = os.getenv("REPORT_HTML", "drift_report.html")

    reference = load_reference()
    current = load_current_window(hours=window_hours)
    ref_aligned, cur_aligned = align_for_drift(reference, current)

    report = Report([DataDriftPreset()])
    result = report.run(
        current_data=Dataset.from_pandas(cur_aligned, data_definition=DataDefinition()),
        reference_data=Dataset.from_pandas(
            ref_aligned, data_definition=DataDefinition()
        ),
    )

    # Rapport visuel (artefact CI) + dict exploitable par le pipeline
    result.save_html(out_html)
    report_dict = result.dict()

    share, count = find_drift_share(report_dict)
    drift_detected = share >= threshold

    print(
        f"[RESULT] drifted_share={share:.3f} | drifted_columns={int(count)} | "
        f"threshold={threshold} | drift_detected={drift_detected}"
    )

    summary = {
        "window_hours": window_hours,
        "threshold": threshold,
        "drift_share": share,
        "drifted_columns": int(count),
        "drift_detected": drift_detected,
    }
    with open("drift_summary.json", "w") as fp:
        json.dump(summary, fp, indent=2)

    # Expose les résultats aux steps suivants de GitHub Actions
    gh_output = os.getenv("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as fp:
            fp.write(f"drift_share={share:.4f}\n")
            fp.write(f"drifted_columns={int(count)}\n")
            fp.write(f"drift_detected={'true' if drift_detected else 'false'}\n")


if __name__ == "__main__":
    main()
