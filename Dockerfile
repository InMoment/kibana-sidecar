FROM python:3.6-slim-stretch
RUN pip install kubernetes==10.0.0 logstash_formatter>=0.5.17
COPY sidecar/kibana-sidecar.py /app/
ENV PYTHONUNBUFFERED=1
WORKDIR /app/
CMD [ "python", "-u", "/app/kibana-sidecar.py" ]
