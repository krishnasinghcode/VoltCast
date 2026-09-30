docker compose up -d kafka postgres mongo redis
docker compose build ingestion
docker compose up -d data-gen

curl.exe -X POST http://localhost:8001/api/v1/simulation/e3b18edb-6d2b-4d85-9ead-70916c800c41/start
curl.exe http://localhost:8001/api/v1/simulation/e3b18edb-6d2b-4d85-9ead-70916c800c41


docker exec -it krishna-postgres-1 psql -U cvi_app -d cvi -c "SELECT count(*) FROM agg_daily;"
docker compose restart ingestion
docker compose logs ingestion --tail 20
docker exec -it krishna-postgres-1 psql -U cvi_app -d cvi -c "SELECT count(*) FROM agg_daily;"