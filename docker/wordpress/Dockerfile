FROM wordpress:latest

# Install mysql client tools (for mysqladmin ping) + curl (for wp-cli download)
RUN apt-get update \
  && apt-get install -y --no-install-recommends default-mysql-client ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*

# Install WP-CLI
RUN curl -fsSL https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar -o /usr/local/bin/wp \
  && chmod +x /usr/local/bin/wp \
  && wp --info

# Add wrapper
COPY entrypoint-wrapper.sh /usr/local/bin/entrypoint-wrapper.sh
RUN chmod +x /usr/local/bin/entrypoint-wrapper.sh

ENTRYPOINT ["/usr/local/bin/entrypoint-wrapper.sh"]
CMD ["apache2-foreground"]

