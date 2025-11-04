# 1. Start db and redis
docker-compose up -d db redis

# 2. Wait and check for health
echo "Waiting for DB to initialize..."
sleep 10
docker ps --format "{{.Names}}: {{.Status}}"
# (This time you should see vss-cloud_db_1 as healthy)

# 3. Run migrate
docker-compose run --rm --no-deps web python manage.py migrate

# 4. Finish the rest of the setup
docker-compose run --rm --no-deps web python manage.py collectstatic --noinput
docker-compose run --rm --no-deps web python manage.py createsuperuser

#docker-compose up -d
