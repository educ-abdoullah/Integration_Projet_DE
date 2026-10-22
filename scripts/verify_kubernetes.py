"""Preuve sur un cluster de demonstration neuf : depot S3 puis traitement planifie."""

import argparse
from decimal import Decimal
import json
import time

from deploy_kubernetes import Kubernetes


def database_state(kube: Kubernetes) -> dict:
    raw = kube.run("-n", "projetde", "exec", "postgres-0", "--", "psql", "-U", "pipeline",
                   "-d", "transactions", "-tA", "-c",
                   "SELECT json_build_object('nb_transactions', COUNT(*), 'total', "
                   "COALESCE(SUM(montant), 0), 'nb_flags', COUNT(*) FILTER (WHERE est_suspecte)) "
                   "FROM transactions;")
    return json.loads(raw, parse_float=Decimal)


def checkpoint(pause: bool, message: str) -> None:
    print("\n=== " + message + " ===", flush=True)
    if pause:
        input("Appuyer sur Entree pour continuer...")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError("Controle non satisfait : " + message)
    print("[OK] " + message, flush=True)


def show_resources(kube: Kubernetes) -> None:
    print(kube.run("get", "nodes"), flush=True)
    print(kube.run("-n", "projetde", "get", "statefulsets,pods,services,pvc,cronjobs,jobs"), flush=True)
    sets = json.loads(kube.run("-n", "projetde", "get", "statefulsets", "-o", "json"))["items"]
    require({item["metadata"]["name"] for item in sets} == {"postgres", "s3"}
            and all(item.get("status", {}).get("readyReplicas", 0) == 1 for item in sets),
            "PostgreSQL et S3 ont chacun une replique prete dans Kubernetes")
    volumes = json.loads(kube.run("-n", "projetde", "get", "pvc", "-o", "json"))["items"]
    require({item["metadata"]["name"] for item in volumes} == {"data-postgres-0", "data-s3-0"}
            and all(item.get("status", {}).get("phase") == "Bound" for item in volumes),
            "Les deux volumes persistants PostgreSQL et S3 sont Bound")
    cron = json.loads(kube.run("-n", "projetde", "get", "cronjob", "pipeline", "-o", "json"))
    require(cron["spec"].get("suspend") is False and cron["spec"]["concurrencyPolicy"] == "Forbid",
            "Le CronJob est actif et interdit le chevauchement de ses executions planifiees")
    print("Calendrier du CronJob : " + cron["spec"]["schedule"], flush=True)


def database_snapshot(kube: Kubernetes) -> dict:
    # Toutes les colonnes, y compris identifiants et date_trt, sont comparees.
    orders = {
        "transactions": "transaction_id",
        "fichiers_traites": "empreinte",
        "totaux_envoyes_par_iban": "iban_origine, devise",
        "totaux_envoyes_par_banque": "banque_source, devise",
        "totaux_recus_par_compte": "pays_destinataire, compte_destinataire, devise",
    }
    fields = ", ".join(
        f"'{name}', (SELECT COALESCE(json_agg(t), '[]'::json) "
        f"FROM (SELECT * FROM {name} ORDER BY {order}) t)"
        for name, order in orders.items()
    )
    raw = kube.run("-n", "projetde", "exec", "postgres-0", "--", "psql", "-U", "pipeline",
                   "-d", "transactions", "-tA", "-c", "SELECT json_build_object(" + fields + ");")
    return json.loads(raw, parse_float=Decimal)


def verify(kube: Kubernetes, image: str, pause: bool = False) -> None:
    checkpoint(pause, "RESSOURCES KUBERNETES ET VOLUMES")
    show_resources(kube)
    if database_state(kube)["nb_transactions"] != 0:
        raise ValueError("Cette preuve demande une base de demonstration vide ; aucune donnee ne sera supprimee")
    checkpoint(pause, "DEPOT DES CSV DANS S3 PUIS ATTENTE DU CRONJOB")
    # Ce job depose uniquement : il n'appelle ni runner ni process_blob.
    upload = (
        "import os; from scripts.generate_csv import generate_csv_bytes; "
        "from pipeline_data.storage import s3_client,ensure_bucket,upload_csv; "
        "c=s3_client(); b=os.environ['S3_BUCKET']; ensure_bucket(c,b); "
        "[upload_csv(c,b,'incoming/'+n,v) for n,v in generate_csv_bytes().items()]; "
        "print('DEPOT UNIQUEMENT : 3 CSV dans S3, aucun traitement lance par ce job')"
    )
    kube.job(image, ["python", "-c", upload], "depot-demo")
    print("Attente d'une execution automatique du CronJob...", flush=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = database_state(kube)
        if state == {"nb_transactions": 21, "total": Decimal("64697.76"), "nb_flags": 6}:
            break
        time.sleep(5)
    else:
        raise RuntimeError("Le traitement planifie n'a pas produit les 21 transactions attendues")
    jobs = json.loads(kube.run("-n", "projetde", "get", "jobs", "-o", "json"))["items"]
    scheduled = [job["metadata"]["name"] for job in jobs
                 if job["metadata"].get("annotations", {}).get(
                     "batch.kubernetes.io/cronjob-scheduled-timestamp"
                 ) and any(owner.get("kind") == "CronJob" and owner.get("name") == "pipeline"
                           for owner in job["metadata"].get("ownerReferences", []))]
    require(bool(scheduled), "Un Job a ete cree automatiquement par le CronJob (horodatage du controleur)")
    print("Jobs planifies : " + ", ".join(scheduled), flush=True)
    for name in scheduled:
        print(kube.run("-n", "projetde", "logs", "job/" + name, "--tail=30"), flush=True)
    checkpoint(pause, "AFFICHAGE DES CSV S3 ET DES CINQ TABLES POSTGRESQL")
    kube.job(image, ["python", "-m", "pipeline_data.proof"], "preuves")
    before = database_snapshot(kube)
    checkpoint(pause, "RELANCE ET VERIFICATION DE L'ABSENCE DE DOUBLONS")
    kube.job(image, label="relance")
    require(database_snapshot(kube) == before,
            "ANTI-DOUBLON : les 5 tables sont identiques avant et apres relance")
    require(database_state(kube) == state, "Resultats inchanges : 21 transactions, 64697.76 EUR, 6 flags")
    print(kube.run("-n", "projetde", "get", "cronjobs,jobs"), flush=True)
    print("PREUVE VALIDE : planification automatique, 21 transactions, 64697.76 EUR, "
          "6 flags, relance sans doublon", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig")
    parser.add_argument("--image", required=True)
    parser.add_argument("--pause", action="store_true")
    args = parser.parse_args()
    verify(Kubernetes(args.context, args.kubeconfig), args.image, pause=args.pause)


if __name__ == "__main__":
    main()
