#!/usr/bin/env bash

TAG="$1"

echo "$DOCKER_PASSWORD" | docker login -u "$DOCKER_USERNAME" --password-stdin

echo "Pushing with tag: latest ..."
docker push inmoment/kibana-sidecar:latest

if [ "$TAG" != "" ]; then
  echo "Pushing with tag: $TAG ..."
  docker push inmoment/kibana-sidecar:$TAG
fi
