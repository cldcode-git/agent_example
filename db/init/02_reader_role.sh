#!/bin/bash
# Agent'ların bağlanacağı SALT-OKUNUR kullanıcıyı oluşturur.
# Agent'lar yalnızca SELECT yapabilir; INSERT/UPDATE/DELETE/DROP veritabanı seviyesinde reddedilir.
# Bu, kodumuzdaki SQL kontrolüne ek ikinci bir güvenlik katmanıdır.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v reader="$POSTGRES_READER_USER" \
     -v reader_pw="$POSTGRES_READER_PASSWORD" \
     -v dbname="$POSTGRES_DB" <<'EOSQL'
CREATE ROLE :"reader" LOGIN PASSWORD :'reader_pw';
GRANT CONNECT ON DATABASE :"dbname" TO :"reader";
GRANT USAGE ON SCHEMA public TO :"reader";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"reader";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"reader";
-- Ek önlemler: her oturum salt-okunur ve uzun sorgular 60 sn'de kesilir
ALTER ROLE :"reader" SET default_transaction_read_only = on;
ALTER ROLE :"reader" SET statement_timeout = '60s';
EOSQL
