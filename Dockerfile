FROM ubuntu:focal
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-pip \
    && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir kubernetes==12.0.1 logstash_formatter>=0.5.17
COPY sidecar/kibana-sidecar.py /app/
ENV PYTHONUNBUFFERED=1
WORKDIR /app/
CMD [ "python3", "-u", "/app/kibana-sidecar.py" ]
