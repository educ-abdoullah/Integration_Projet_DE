"""Deploie les trois composants sur un contexte Kubernetes explicitement choisi."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "deploy" / "kubernetes"


class Kubernetes:
    def __init__(self, context: str, kubeconfig: str | None = None):
        self.prefix = ["kubectl", "--context", context]
        if kubeconfig:
            self.prefix += ["--kubeconfig", kubeconfig]

    def run(self, *arguments: str, content: str | None = None) -> str:
        result = subprocess.run(
            [*self.prefix, *arguments], input=content, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
        )
        if result.returncode:
            # content peut contenir un Secret et n'est pas journalise.
            raise RuntimeError(result.stderr.strip() or "Commande kubectl en echec")
        return result.stdout.strip()

    def apply(self, document: dict) -> None:
        print(self.run("apply", "-f", "-", content=json.dumps(document)), flush=True)

    def job(self, image: str, command: list[str] | None = None, label: str = "verification") -> str:
        name = label + "-" + uuid4().hex[:10]
        job = json.loads(self.run(
            "-n", "projetde", "create", "job", name, "--from=cronjob/pipeline",
            "--dry-run=client", "-o", "json",
        ))
        container = job["spec"]["template"]["spec"]["containers"][0]
        container["image"] = image
        if command is not None:
            container["command"] = command
        self.apply(job)
        try:
            print(self.run("-n", "projetde", "wait", "--for=condition=complete",
                           "job/" + name, "--timeout=300s"), flush=True)
        finally:
            print(self.run("-n", "projetde", "logs", "job/" + name,
                           "--all-containers=true", "--tail=-1"), flush=True)
        return name


def deploy(kube: Kubernetes, image: str, secrets_file: Path | None,
           schedule: str = "*/5 * * * *", pull_secret: str | None = None) -> None:
    print(kube.run("apply", "-f", str(MANIFESTS / "namespace.yaml")), flush=True)
    if secrets_file:
        values = {}
        for line in secrets_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        required = {"POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"}
        if set(values) != required or any(not value or "REMPLACER" in value for value in values.values()):
            raise ValueError("Le fichier de secrets doit definir les trois valeurs reelles attendues")
        kube.apply({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                    "metadata": {"name": "pipeline-secrets", "namespace": "projetde"},
                    "stringData": values})
    else:
        kube.run("-n", "projetde", "get", "secret", "pipeline-secrets", "-o", "name")

    schema = json.loads(kube.run(
        "-n", "projetde", "create", "configmap", "pipeline-schema",
        "--from-file=001_schema.sql=" + str(ROOT / "sql/001_schema.sql"),
        "--dry-run=client", "-o", "json",
    ))
    kube.apply(schema)
    for name in ("config.yaml", "postgres.yaml", "s3.yaml"):
        print(kube.run("apply", "-f", str(MANIFESTS / name)), flush=True)
    for service in ("postgres", "s3"):
        print(kube.run("-n", "projetde", "rollout", "status", "statefulset/" + service,
                       "--timeout=300s"), flush=True)

    cron = json.loads(kube.run("create", "--dry-run=client", "-o", "json",
                               "-f", str(MANIFESTS / "cronjob.yaml")))
    cron["spec"]["schedule"] = schedule
    pod = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    pod["containers"][0]["image"] = image
    if pull_secret:
        pod["imagePullSecrets"] = [{"name": pull_secret}]
    kube.apply(cron)
    # Une vraie execution sans generation doit reussir avant d'activer le timer.
    kube.job(image)
    print(kube.run("-n", "projetde", "patch", "cronjob", "pipeline", "--type=merge",
                   "-p", '{"spec":{"suspend":false}}'), flush=True)
    print("DEPLOIEMENT VALIDE : PostgreSQL, S3 et CronJob actifs", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Contexte cible explicite, jamais choisi implicitement.")
    parser.add_argument("--kubeconfig")
    parser.add_argument("--image", required=True, help="Image chargee dans kind ou publiee dans un registre.")
    parser.add_argument("--secrets-file", type=Path)
    parser.add_argument("--schedule", default="*/5 * * * *")
    parser.add_argument("--image-pull-secret")
    args = parser.parse_args()
    deploy(Kubernetes(args.context, args.kubeconfig), args.image, args.secrets_file,
           args.schedule, args.image_pull_secret)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
