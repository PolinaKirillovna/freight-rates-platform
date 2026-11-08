FROM apache/superset:4.1.2

USER root
RUN pip install --no-cache-dir "psycopg2-binary==2.9.10" "clickhouse-connect==0.8.18"
COPY docker/superset/superset_config.py /app/pythonpath/superset_config.py
COPY docker/superset/bootstrap.py /app/bootstrap/bootstrap.py
COPY docker/superset/entrypoint.sh /app/bootstrap/entrypoint.sh
RUN chmod +x /app/bootstrap/entrypoint.sh
USER superset

ENTRYPOINT ["/app/bootstrap/entrypoint.sh"]
