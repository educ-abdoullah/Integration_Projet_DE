"""Demarre le S3 local, teste et execute la pipeline du niveau 2."""

import argparse
import sys

from run_level1 import PROJECT_ROOT, run_commands


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pause", action="store_true", help="Attendre Entree entre les etapes pour une presentation.")
    args = parser.parse_args()
    artifacts_mount = str(PROJECT_ROOT / "artifacts") + ":/app/artifacts"
    run_commands((
        ["build", "pipeline"],
        ["--profile", "s3", "up", "-d", "--wait", "postgres", "s3"],
        ["--profile", "s3", "run", "--rm", "tests-s3"],
        ["--profile", "s3", "run", "--rm", "pipeline", "--generate"],
        ["run", "--rm", "--entrypoint", "python", "-v", artifacts_mount, "pipeline",
         "-m", "pipeline_data.proof", "--save-state", "/app/artifacts/niveau2-avant-relance.json"],
        ["--profile", "s3", "run", "--rm", "pipeline"],
        ["run", "--rm", "--entrypoint", "python", "-v", artifacts_mount, "pipeline",
         "-m", "pipeline_data.proof", "--compare-state", "/app/artifacts/niveau2-avant-relance.json",
         "--summary-only"],
    ), "niveau2.log", pause=args.pause)
    print("Niveau 2 termine. Journal : artifacts/niveau2.log")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
