"""Demonstration Kubernetes locale avec kind ; aucun cluster existant n'est modifie."""

import argparse
from pathlib import Path
import secrets
import subprocess
import sys

from deploy_kubernetes import Kubernetes, ROOT, deploy
from verify_kubernetes import verify


NODE_IMAGE = "kindest/node:v1.35.8@sha256:07b2536e30b803ed61d1677a79df6115f798ce64c80f9e22f6ed45afd09323c0"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", default="kind")
    parser.add_argument("--cluster", default="projetde-level3")
    parser.add_argument("--pause", action="store_true", help="Attendre Entree entre les etapes de demonstration.")
    args = parser.parse_args()
    artifact_dir = ROOT / "artifacts" / "kubernetes"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    kubeconfig = str(artifact_dir / (args.cluster + ".kubeconfig"))
    image = "projetde-app:level3-" + secrets.token_hex(4)
    secret_file = artifact_dir / (args.cluster + ".env")
    clusters = subprocess.check_output([args.kind, "get", "clusters"], text=True).splitlines()
    if args.cluster in clusters:
        raise SystemExit("Le cluster existe deja. Choisir un autre nom avec --cluster ; rien n'est efface.")
    with secret_file.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(key + "=" + secrets.token_hex(24) for key in
                              ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY")) + "\n")
    commands = (
        ["docker", "build", "-t", image, str(ROOT)],
        [args.kind, "create", "cluster", "--name", args.cluster, "--image", NODE_IMAGE,
         "--kubeconfig", kubeconfig, "--wait", "180s"],
        [args.kind, "load", "docker-image", image, "--name", args.cluster],
    )
    for command in commands:
        if args.pause:
            input("\nAppuyer sur Entree pour lancer l'etape suivante...")
        print("> " + " ".join(command), flush=True)
        subprocess.run(command, check=True)
    kube = Kubernetes("kind-" + args.cluster, kubeconfig)
    if args.pause:
        input("\nAppuyer sur Entree pour deployer PostgreSQL, S3 et le CronJob...")
    # Uniquement pour accelerer la preuve locale ; production : toutes les 5 min.
    deploy(kube, image, secret_file, schedule="* * * * *")
    verify(kube, image, pause=args.pause)
    print("Cluster conserve pour inspection. Kubeconfig : " + kubeconfig, flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
