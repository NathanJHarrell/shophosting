FROM shinsenter/magento:latest

USER root

# Install mysql client tools (for mysqladmin ping) - curl is already installed
RUN apt-get update \
  && apt-get install -y --no-install-recommends default-mysql-client \
  && rm -rf /var/lib/apt/lists/*

# Add wrapper script
COPY entrypoint-wrapper.sh /usr/local/bin/entrypoint-wrapper.sh
RUN chmod +x /usr/local/bin/entrypoint-wrapper.sh

USER www-data

ENTRYPOINT ["/usr/local/bin/entrypoint-wrapper.sh"]
CMD ["php-fpm"]
