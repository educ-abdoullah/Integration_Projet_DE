BEGIN;

CREATE TABLE fichiers_traites (
    fichier_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nom_fichier TEXT NOT NULL CHECK (btrim(nom_fichier) <> ''),
    empreinte VARCHAR(64) NOT NULL UNIQUE
        CHECK (empreinte ~ '^[0-9a-f]{64}$'),
    date_trt TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    nb_transactions INTEGER NOT NULL CHECK (nb_transactions > 0)
);

CREATE TABLE transactions (
    transaction_id UUID PRIMARY KEY,
    date_transaction TIMESTAMPTZ NOT NULL,
    iban_origine VARCHAR(34) NOT NULL CHECK (btrim(iban_origine) <> ''),
    pays_source CHAR(2) NOT NULL CHECK (pays_source ~ '^[A-Z]{2}$'),
    banque_source TEXT NOT NULL CHECK (btrim(banque_source) <> ''),
    iban_destinataire VARCHAR(34)
        CHECK (iban_destinataire IS NULL OR btrim(iban_destinataire) <> ''),
    compte_destinataire TEXT NOT NULL CHECK (btrim(compte_destinataire) <> ''),
    pays_destinataire CHAR(2) NOT NULL CHECK (pays_destinataire ~ '^[A-Z]{2}$'),
    montant NUMERIC(14,2) NOT NULL CHECK (montant > 0),
    devise CHAR(3) NOT NULL CHECK (devise ~ '^[A-Z]{3}$'),
    est_suspecte BOOLEAN NOT NULL,
    fichier_id BIGINT NOT NULL REFERENCES fichiers_traites (fichier_id),
    CONSTRAINT seuil_transaction_suspecte
        CHECK (est_suspecte = (montant > 5000))
);

CREATE INDEX transactions_fichier_id_idx ON transactions (fichier_id);

CREATE TABLE totaux_envoyes_par_iban (
    iban_origine VARCHAR(34) NOT NULL,
    devise CHAR(3) NOT NULL,
    montant_total NUMERIC(20,2) NOT NULL CHECK (montant_total >= 0),
    PRIMARY KEY (iban_origine, devise)
);

CREATE TABLE totaux_envoyes_par_banque (
    banque_source TEXT NOT NULL,
    devise CHAR(3) NOT NULL,
    montant_total NUMERIC(20,2) NOT NULL CHECK (montant_total >= 0),
    PRIMARY KEY (banque_source, devise)
);

CREATE TABLE totaux_recus_par_compte (
    pays_destinataire CHAR(2) NOT NULL,
    compte_destinataire TEXT NOT NULL,
    devise CHAR(3) NOT NULL,
    montant_total NUMERIC(20,2) NOT NULL CHECK (montant_total >= 0),
    PRIMARY KEY (pays_destinataire, compte_destinataire, devise)
);

COMMIT;
