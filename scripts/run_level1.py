"""Enchaine tout le niveau 1 avec Docker, sans installation Python externe."""

from pathlib import Path
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_commands(commands: tuple, log_name: str, pause: bool = False) -> None:
    if shutil.which("docker") is None:
        raise SystemExit("Docker Desktop est necessaire et doit etre demarre.")
    if not (PROJECT_ROOT / ".env").exists():
        shutil.copyfile(PROJECT_ROOT / ".env.example", PROJECT_ROOT / ".env")
    artifacts = PROJECT_ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with (artifacts / log_name).open("w", encoding="utf-8") as log:
        for arguments in commands:
            if pause:
                input("\nAppuyer sur Entree pour lancer l'etape suivante...")
            command = ["docker", "compose", *arguments]
            label = "\n> " + " ".join(command) + "\n"
            print(label, end="", flush=True)
            log.write(label)
            with subprocess.Popen(
                command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            ) as process:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                code = process.wait()
            if code:
                raise SystemExit(code)


def main() -> None:
    commands = (
        ["build", "pipeline"],
        ["up", "-d", "--wait", "postgres"],
        ["--profile", "test", "run", "--rm", "tests"],
        ["run", "--rm", "pipeline", "--source", "local", "--generate"],
        ["run", "--rm", "pipeline", "--source", "local", "--generate"],
        ["--profile", "demo", "run", "--rm", "demonstrations"],
    )
    run_commands(commands, "niveau1.log")
    print("Niveau 1 valide.")
    print("Rapport : artifacts/demonstrations.json ; journal : artifacts/niveau1.log")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
