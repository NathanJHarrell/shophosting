source .env

docker network rm aw-test-net >/dev/null 2>&1 || true

docker network create aw-test-net

docker run --rm --network aw-test-net \
  -e WORDPRESS_DB_HOST==host.docker.internal:3306 \
  -e WORDPRESS_DB_USER=$WORDPRESS_DB_USER \
  -e WORDPRESS_DB_PASSWORD=$WORDPRESS_DB_PASSWORD \
  -e WORDPRESS_DB_NAME=$WORDPRESS_DB_DATABASE \
  shophosting/wordpress:latest \
  true
