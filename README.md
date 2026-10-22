# Pipeline de transactions financières

Projet d'intégration continue et de déploiement d'une pipeline data, organisé en trois niveaux.

Le jeu de données contient trois CSV de sept transactions fixes chacun : France vers Inde, banque source BNP Paribas, montants en EUR. Les traitements calculent les sommes envoyées par IBAN et par banque, les sommes reçues par compte et un indicateur pour les transactions strictement supérieures à 5 000 €.

## Prérequis

- Python 3.10 ou supérieur pour lancer les scripts d'orchestration.
- Docker Desktop démarré avec les conteneurs Linux et Docker Compose.
- Pour le niveau 3 : kubectl et kind.

Les commandes se lancent depuis la racine du projet. Les dépendances applicatives sont installées dans l'image Docker.

## Niveau 1 — CSV locaux et PostgreSQL

**Stack :** Python, PostgreSQL 17, Psycopg, Docker Compose, unittest et GitHub Actions.

Les CSV sont générés localement puis chargés et validés. Les fonctions de traitement sont séparées du chargement. Les transactions et les résultats sont insérés dans PostgreSQL dans une transaction atomique. Une empreinte SHA-256 permet de reconnaître les fichiers déjà traités ; les erreurs temporaires d'insertion font l'objet de nouvelles tentatives.

```cmd
python scripts/run_level1.py
```

Le script construit l'image, démarre PostgreSQL, exécute les tests, génère et traite les CSV, relance le traitement pour contrôler l'absence de doublons et exécute les simulations de panne et de reprise.

## Niveau 2 — Stockage S3 local

**Stack :** stack du niveau 1, SeaweedFS 4.48 et Boto3.

Les CSV sont générés en mémoire et déposés dans un bucket S3 local. La pipeline récupère les fichiers depuis S3, avec des tentatives supplémentaires en cas d'erreur temporaire de téléchargement, puis insère les résultats dans PostgreSQL.

```cmd
python scripts/run_level2.py
```

Le script démarre PostgreSQL et SeaweedFS, exécute les 31 tests des niveaux 1 et 2, dépose et traite les CSV, affiche les objets S3 et les tables SQL, puis compare les cinq tables avant et après une relance.

Les scripts des niveaux 1 et 2 créent `.env` depuis `.env.example` si ce fichier manque. Les journaux sont enregistrés dans `artifacts/niveau1.log` et `artifacts/niveau2.log`.

## Niveau 3 — Kubernetes et déploiement continu

**Stack :** Python, Docker, Kubernetes, kind, kubectl, CronJob, PostgreSQL, SeaweedFS, GitHub Actions et GitHub Container Registry (GHCR).

PostgreSQL et SeaweedFS sont déployés avec des StatefulSets et des volumes persistants. Un CronJob lance régulièrement le traitement des nouveaux CSV présents dans S3.



```cmd
python scripts/run_level3.py --kind artifacts/tools/kind.exe --cluster projetde-demo
```

Le nom donné à `--cluster` doit être inutilisé. Le script construit l'image, crée le cluster local, déploie les services et dépose les CSV. Il attend un déclenchement automatique du CronJob, affiche les ressources Kubernetes et les tables PostgreSQL, puis vérifie l'absence de doublons après relance. Le cluster est conservé à la fin.

La fréquence est d'une minute pour la démonstration locale et de cinq minutes pour le déploiement normal.

Le workflow `.github/workflows/cd.yml` exécute la CI avant le déploiement. Il propose une démonstration sur un cluster kind temporaire dans GitHub Actions ou un déploiement sur un cluster persistant configuré, avec publication de l'image dans GHCR. Le lancement local ne déclenche pas ce workflow. Le déploiement local a été vérifié ; une exécution distante nécessite un dépôt GitHub et les accès correspondants.

## Base de données

| Table | Contenu |
| --- | --- |
| `transactions` | Détail des transactions et indicateur de dépassement du seuil. |
| `fichiers_traites` | Nom, empreinte, date de traitement et nombre de transactions par fichier. |
| `totaux_envoyes_par_iban` | Sommes envoyées par IBAN origine et devise. |
| `totaux_envoyes_par_banque` | Sommes envoyées par banque source et devise. |
| `totaux_recus_par_compte` | Sommes reçues par pays, compte destinataire et devise. |

Pour le jeu de démonstration complet : **21 transactions**, **64 697,76 EUR** et **six transactions signalées**. Une relance conserve les mêmes données.

## Organisation

| Dossier ou fichier | Rôle |
| --- | --- |
| `pipeline_data/` | Chargement, traitements, accès S3, insertion PostgreSQL, retry et contrôles. |
| `scripts/` | Génération, orchestration des niveaux, simulations de panne et déploiement. |
| `sql/` | Schéma des cinq tables PostgreSQL. |
| `tests/` | Tests unitaires et tests d'intégration PostgreSQL et S3. |
| `deploy/kubernetes/` | Manifests Kubernetes. |
| `.github/workflows/` | Pipelines CI et CD. |
| `Dockerfile`, `compose.yaml` | Image applicative et services locaux. |
| `.env.example` | Modèle de configuration locale. |
| `artifacts/` | Journaux et fichiers générés, ignorés par Git. |
| `docs/` | Documentation locale par étape, ignorée par Git. |
