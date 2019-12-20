
# Overview

This is a docker container intended to run inside a Kubernetes cluster to collect config maps with a specified label and upload the values as `Saved Objects` to the Kibana API.

This is so that you can automate deploying Kibana searches, visualizations, index-patterns, dashboards and watchers.

The contained python script is working with the Kubernetes API `1.10` or later and Kibana API `6.4.x` or later `6.x` versions and Kibana `7.0` or later `7.x` versions

# Why?

Currently (April 2018) there is no simple way to configure Kibana artifacts via Kubernetes artifacts and keep them updated at runtime.

By doing this, you can automate uploading of your Kibana artifacts in the same manner that you automate deploying your Kubernetes apps (e.g., Helm, or whatever else you use).

This was inspired by https://github.com/kiwigrid/k8s-sidecar/

# How?

Run the container created by this repo in a single pod (as a `Deployment` with `1` replica). Specify which label should be monitored and where your Kibana endpoint is.

# Prerequisites

You must be using Kibana version `6.4.x` or later `6.x` versions and Kibana `7.0` or later `7.x` versions
  - We use the `_bulk_create` API which was added in `6.4` and Watcher APIs

# Features

- Extract files from config maps
- Filter based on label
- Update/Delete objects in Kibana on change of configmap
- Supports Kibana Basic Authentication

# Docker Repo

Docker Images of every `master` commit are on https://hub.docker.com/r/inmoment/kibana-sidecar/tags

The Docker Image is tagged with both `latest` and a tag specific to the git commit hash and git commit timestamp.

If you want to always pull the latest, you can use:

`docker pull inmoment/kibana-sidecar:latest`

Or to use a specific version, e.g., `20190606.192629-9d03e15`:

`docker pull inmoment/kibana-sidecar:20190606.192629-9d03e15`

It is generally recommended that you NOT use `latest` in any production system because you could have things changing on you without your knowledge.


# Usage

Example for a simple deployment can be found in `example.yaml`. Depending on the cluster setup you have to grant yourself admin rights first: `kubectl create clusterrolebinding cluster-admin-binding   --clusterrole cluster-admin   --user $(gcloud config get-value account)`

## Generating Kibana Object IDs from Titles

Letting Kibana generate the object IDs makes URLs that aren't so friendly.

You can configure the sidecar to generate object IDs from the `title` of the objects in the config map by adding the label: `generate_id_from_title: "true"` to your ConfigMap.

If that is specified, then for all objects in your config map, the `id` will be overwritten with the `title` with the following normalizations applied:

1) It will be lowercased
2) All characters which are not: `a-z-_0-9*` will be replaced with `_`

By default, this is not enabled. You have to explicitly enable it by adding the label. However, once the label is added to a `ConfigMap` that setting will be used for all objects in that `ConfigMap`.


## Configuration Environment Variables

- `LABEL` 
  - description: Label that should be used for filtering
  - required: true
  - type: string

- `KIBANA_BASE_URL`
  - description: The Base URL for your Kibana installation. Everything up to but not including `/api` which would be required to hit the API.
  - required: true
  - type: string
  
- `ELASTICSEARCH_BASE_URL`
  - description: The Base URL for your Elastic Search cluster. Used for Watcher configuration.
  - required: true
  - type: string

- `KIBANA_USERNAME`
  - description: The username to use with the Kibana APIs and ElasticSearch APIs
  - required: false
  - type: URI

- `KIBANA_PASSWORD`
  - description: The password to use with the Kibana API and ElasticSearch APIs
  - required: false
  - type: string

- `NAMESPACE`
  - description: If specified, the sidecar will search for config-maps inside this namespace. Otherwise the namespace in which the sidecar is running will be used. It's also possible to specify `ALL` to search in all namespaces.
  - required: false
  - type: string

- `LOGLEVEL`
  - description: If specified, the level of logs that will be output.
  - required: false
  - type: string
  - default: `WARNING`
  - valid values: `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`,
  
- `DEFAULT_WATCHER_ACTIONS_FILEPATH`
  - description: If specified, will be the filepath inside the container where a JSON file containing a JSON object of Default Watcher Actions exists. These actions will be applied to ALL Watcher objects that are deployed.
  - required: false
  - type: string
  - default: ''
  - NOTE: You should map a Secret or a ConfigMap value into a file in the container, then set this environment variable to that path.
  - You can specify the `volumes` and `volumeMounts` keys in the Helm Chart to mount in your `Default Watcher Actions` file.
  - The file should contain a JSON Object of Watcher Actions.


## Helm Chart

The `kibana-sidecar` can be deployed using the Helm Chart included in this repo in the `charts/kibana-sidecar` directory.

The environment variables above can be configured via the Helm values.

In addition, resources, image tag, and other variables can be configured.

# How to deploy your Kibana Objects

The Kibana Objects must be defined in `ConfigMap`s which contain the specified label that the `kibana-sidecar` is watching.

Each entry in the `ConfigMap` will be attempted to be loaded into Kibana.

Each entry in the `ConfigMap` should contain one Kibana object in JSON format.

The `kibana-sidecar` will take all entries in the `ConfigMap` and order them in the following order: `savedsearch`, `visualization`, `dashboard` due to the dependencies that these objects may have.

An example `ConfigMap` that produces a Saved Search for `kibana-sidecar` log events is included in the Helm Chart in this repository.



# Building the Docker Image

Run:
- `docker build . -t kibana-sidecar:latest`
 
# Continuous Integration

We are using Travis: https://travis-ci.com/InMoment/kibana-sidecar

PRs and `master` builds get built automatically.

# Deploying Elastic Watchers

You can use `kibana-sidecar` to deploy [Elastic Watchers](https://www.elastic.co/guide/en/x-pack/current/watcher-getting-started.html)

The format of the JSON object that you should use in your ConfigMap to configure an Elastic Watcher is based on the [`PUT` API](https://www.elastic.co/guide/en/elasticsearch/reference/6.7/watcher-api-put-watch.html)

It should look like what the `PUT` API expects with the following exceptions:

1) If you are not using the label: `generate_id_from_title: "true"`, you must specify an `id` property at the root of the object.
2) If you want to make the Watcher inactive when it is deployed, you can set the `active` property at the root of the object to `false`.

e.g.,
```
{
  "id": "my_watcher_id",
  "active": false,
  "metadata": {
    ...
  },
  "input": {
    ...
  },
  "condition": {
    ...
  },
  "actions": {
    ...
  }

```

## Authorization

Your Kibana user specified by `KIBANA_USERNAME` must have `manage_watcher` cluster privileges. See [Elastic docs](https://www.elastic.co/guide/en/elasticsearch/reference/6.7/watcher-api-put-watch.html#_authorization_98)

## Default Elastic Watcher Actions

Specifying the Actions in every watcher can be annoying, especially if you have different Action configurations for different environments.

This feature allows you to configure the `kibana-sidecar` to have a set of 1 or more Default Watcher Actions.

It will then add all of these actions to every Watcher it deploys.

To use this feature, do the following:

1) Configure a `ConfigMap` or `Secret` to have the content of your Default Actions. This should be a JSON Object with keys for the action name and values for the action definition.
  e.g.,
```
{
    "opsgenie": {
        "webhook": {
            "scheme": "https",
            "method": "POST",
            "host": "api.opsgenie.com",
            "port": 443,
            "path": "/v1/json/eswatcher",
            "headers": {
                "Content-Type": "application/json"
            },
            "params": {
                "apiKey": "sdf-20aefdf-4233s-8d0ddf2-8c2eebd1b7f"
            },
            "body": "{{#toJson}}ctx{{/toJson}}"
        }
    }
}

```

2) Mount that `ConfigMap` or `Secret` as a `Volume` using the Helm Chart's `volumes` and `volumeMounts` keys

3) Configure the environment variable `DEFAULT_WATCHER_ACTIONS_FILEPATH` to be the absolute filepath where the JSON file is mounted.


# Operational Visibility (ops-viz)

## Kibana

We are including the Kibana Dashboards in the Helm Chart so that they can get automatically uploaded by the `kibana-sidecar` into the appropriate Kibana for the target environment.

The scope of the Kibana Objects are for a single "helm release" of the kibana-sidecar.

The Kibana objects are stored in separate files in the `charts/kibana-sidecar/ops-viz/kibana` directory.

If making modifications, export all kibana-sidecar-related objects, then run the `templatizeKibanaDashboard.groovy` script on the exported file with the output dir specified as `charts/kibana-sidecar/ops-viz/kibana`

Then commit any changed or new files after inspecting them.

Therefore, the Kibana objects will either be specific to a given Helm Release or there may be objects which are non-specific to a Helm Release.

The `ops-viz/scripts/templatizeKibanaDashboard.groovy` script will replace any instances of the Helm Release name with a token so that it can be rendered during Helm template rendering.

If your Helm release was named: `kibana-sidecar` and these are the Kibana objects you exported from the Kibana UI (or API)

You run the script:
  `ops-viz/scripts/templatizeKibanaDashboard.groovy -f export_from_kibana.json -i kibana-sidecar -o charts/kibana-sidecar/ops-viz/kibana`

Then inspect them and make sure that the `$(instance_name)` variable appears in the places you expected them to.

Then add any new files or any updated files to Git.

Then whenever the Helm chart is deployed, these Kibana objects will be written into a `ConfigMap`. 

The `kibana-sidecar` will then find this `ConfigMap` and attempt to upload the objects to the target Kibana for the environment.

If you don't see your dashboards get loaded, check the logs of the `kibana-sidecar` component in Kibana.

Or perhaps the `kibana-sidecar` isn't running in your target environment.

If I want to render the Kibana objects and extract the JSON so I can manually import it into Kibana:
- `relName=kibana-sidecar; helm template -n $relName charts/kibana-sidecar -x templates/kibana-config-map.yaml | yq -r "[.data[] | fromjson] " > ${relName}_kibana-objects.json`
- If I want to use non-default values, add `-f <valuesFile>`
- NOTE: this requires you to have [`yq`](https://github.com/kislyuk/yq) and [`jq`](https://stedolan.github.io/jq) on your path.

