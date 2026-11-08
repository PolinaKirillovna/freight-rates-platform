FROM apache/airflow:2.10.5-python3.11

USER root
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
USER airflow

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "apache-airflow==2.10.5" -r /tmp/requirements.txt

COPY --chown=airflow:root common /opt/airflow/project/common
COPY --chown=airflow:root etl /opt/airflow/project/etl
COPY --chown=airflow:root ml /opt/airflow/project/ml
COPY --chown=airflow:root sql /opt/airflow/project/sql
COPY --chown=airflow:root dags /opt/airflow/dags
COPY --chown=airflow:root docker/airflow/entrypoint.sh /opt/airflow/entrypoint.sh

ENV PYTHONPATH=/opt/airflow/project
ENTRYPOINT ["/usr/bin/dumb-init", "--", "bash", "/opt/airflow/entrypoint.sh"]
