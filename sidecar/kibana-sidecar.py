from kubernetes import client, config, watch
import os
import logging
from logstash_formatter import LogstashFormatterV1
import sys
import requests
import json
from requests.packages.urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
import collections
import re

class LogStashFormatterCustom(LogstashFormatterV1):
    def format(self, record):
        record.level = record.levelname
        delattr(record, 'levelname')
        pid = record.process
        delattr(record, 'process')
        record.process = {"pid": pid, "name": record.processName}
        delattr(record, 'processName')
        return LogstashFormatterV1.format(self, record)


logger = logging.getLogger()
LOGLEVEL = os.environ.get('LOGLEVEL', 'WARNING').upper()
logger.setLevel(level=LOGLEVEL)
stdoutHandler = logging.StreamHandler(stream=sys.stdout)
formatter = LogStashFormatterCustom()

stdoutHandler.setFormatter(formatter)
logger.addHandler(stdoutHandler)

def request(url, username, password, method, queryParams, payload, headers={}):
    r = requests.Session()
    retries = Retry(total = 5,
            connect = 5,
            backoff_factor = 0.2,
            status_forcelist = [ 500, 502, 503, 504 ])
    r.mount('http://', HTTPAdapter(max_retries=retries))
    r.mount('https://', HTTPAdapter(max_retries=retries))
    auth = None
    if username and password:
        auth = HTTPBasicAuth(username, password)
    if url is None:
        logger.info("No url provided. Doing nothing.")
        # If method is not provided use GET as default
    elif method == "GET" or method is None:
        res = r.get("%s" % url, auth=auth, params=queryParams, timeout=30, headers=headers)
        logger.info("%s request sent to %s. Response: %d %s" % (method, url, res.status_code, res.reason))
        return res
    elif method == "POST":
        res = r.post("%s" % url, auth=auth, params=queryParams, json=payload, timeout=30, headers=headers)
        logger.info("%s request sent to %s. Response: %d %s" % (method, url, res.status_code, res.reason))
        return res

# Kibana API Saved Object Format is different from the format that you get if you export objects from the UI.
# So we need to rename a few properties.
# https://www.elastic.co/guide/en/kibana/current/saved-objects-api-bulk-create.html
# UI export has `_id`, `_type` and `_source` instead of `id`, `type` and `attributes`
def transformKibanaObjectToApiFormat(obj):
    if '_source' in obj:
        obj["attributes"] = obj["_source"]
        del obj["_source"]

    if '_id' in obj:
        obj["id"] = obj["_id"]
        del obj["_id"]

    if '_type' in obj:
        obj["type"] = obj["_type"]
        del obj["_type"]

    return obj

def generateObjectIdFromTitle(title):
    lowerTitle = title.lower()
    id = re.sub("[^a-z-_0-9*]", "_", lowerTitle)
    return id

def prepareRecordsInConfigMapForUpload(jsonStrArrOrObj, generateIdFromTitle, oldIdToNewIdMap):
    parsedData = json.loads(jsonStrArrOrObj)
    if not isinstance(parsedData, collections.Sequence):
        parsedData = [parsedData]

    transformedData = [transformKibanaObjectToApiFormat(o) for o in parsedData]

    if generateIdFromTitle:
        for o in transformedData:
            if 'title' in o["attributes"]:
                title = o["attributes"]["title"]
                newId = generateObjectIdFromTitle(title)
                oldIdToNewIdMap[o["id"]] = newId
                logger.debug(f"Generated ID: {newId} from title: {title}")
                o["id"] = newId

    return transformedData

def renameAllIds(oldIdToNewIdMap, dataArr):
    dataJsonStr = json.dumps(dataArr)
    # Update references from old IDs to new IDs
    for oldId, newId in oldIdToNewIdMap.items():
        logger.debug(f"Replacing instances of oldId: {oldId} with newId: {newId}")
        dataJsonStr = dataJsonStr.replace("\"" + oldId + "\"", "\"" + newId + "\"").replace(
            "\\\"" + oldId + "\\\"", "\\\"" + newId + "\\\"")
    renamedData = json.loads(dataJsonStr)
    return renamedData


def upsertKibanaObject(configMapName, kibanaBaseUrl, kibanaUsername, kibanaPassword, data):


    logger.info(f"Creating/Updating in Kibana: {kibanaBaseUrl}: Kibana Object(s) with data: \n{json.dumps(data)} ...")

    logger.debug(f"POSTing data:\n{json.dumps(data)}")
    # Bulk Create (with overwrite) objects in Kibana
    try:
        res = request(kibanaBaseUrl + "/api/saved_objects/_bulk_create", kibanaUsername, kibanaPassword, "POST", { "overwrite": "true"}, data, {"kbn-xsrf": "kibana-sidecar"})
        if res.status_code != 200:
            logger.error(f"Failed to create objects: {data} because request returned status: {res.status_code} and body: {res.text}")
        else:
            responseBody = res.json()
            logger.debug(f"Response from Kibana: {json.dumps(responseBody)}")
            allSuccessful = True
            for o in responseBody["saved_objects"]:
                id = o["id"]
                if 'error' in o:
                    logger.error(f"Failed to save object with id: {id} because: {json.dumps(o['error'])}")
                    allSuccessful = False
                else:
                    logger.debug(f"Successfully saved object with id: {id}")
            if allSuccessful:
                logger.info(f"All objects from ConfigMap: {configMapName} were saved successfully")
    except Exception as e:
        logger.error("Failed to save all objects because: ", exc_info=e)


# We want to upload objects in the following order:
# index-patterns, searches, visualizations, dashboards
# this is because visualizations can use searches, dashboards can use visualizations and searches
# Everything can use index-patterns
def reorderObjectsToUpload(objectsArr):
    indexPatterns = []
    searches = []
    visualizations = []
    dashboards = []
    other = []

    for o in objectsArr:
        type = o["type"]
        if type == "index-pattern":
            indexPatterns.append(o)
        elif type == "search":
            searches.append(o)
        elif type == "visualization":
            visualizations.append(o)
        elif type == "dashboard":
            dashboards.append(o)
        else:
            other.append(o)
    all = []
    all.extend(indexPatterns)
    all.extend(searches)
    all.extend(visualizations)
    all.extend(dashboards)
    all.extend(other)

    return all



def deleteKibanaObject(configMapName, kibanaBaseUrl, kibanaUsername, kibanaPassword, filename, data, generateIdFromTitle):
    logger.info(f"Deleting from Kibana: {kibanaBaseUrl}: Kibana Object(s) with data: \n{data} ...")
    # TODO Handle generating ID from Title
    # TODO: Delete object from Kibana

def watchForChanges(label, kibanaBaseUrl, kibanaUsername, kibanaPassword, currentNamespace):
    v1 = client.CoreV1Api()
    w = watch.Watch()
    stream = None
    namespace = os.getenv("NAMESPACE")
    if namespace is None:
        logger.info(f"Watching ConfigMaps in current namespace: {currentNamespace}.")
        stream = w.stream(v1.list_namespaced_config_map, namespace=currentNamespace)
    elif namespace == "ALL":
        logger.info(f"Watching ConfigMaps in ALL namespaces.")
        stream = w.stream(v1.list_config_map_for_all_namespaces)
    else:
        logger.info(f"Watching ConfigMaps in namespace: {namespace}.")
        stream = w.stream(v1.list_namespaced_config_map, namespace=namespace)
    for event in stream:
        metadata = event['object'].metadata
        if metadata.labels is None:
            continue
        logger.debug(f'Inspecting configmap {metadata.namespace}/{metadata.name}')
        if label in event['object'].metadata.labels.keys():
            logger.info(f"Configmap with label found for configmap: {metadata.namespace}/{metadata.name}")
            dataMap=event['object'].data
            if dataMap is None:
                logger.info("Configmap does not have data.")
                continue
            try:
                eventType = event['type']
                generateIdFromTitle = ("generate_id_from_title" in event['object'].metadata.labels.keys() and event['object'].metadata.labels["generate_id_from_title"] == "true")
                oldIdToNewIdMap = {}
                objectsToUpload = []
                for filename in dataMap.keys():
                    logger.info("File in configmap %s %s" % (filename, eventType))
                    if (eventType == "ADDED") or (eventType == "MODIFIED"):
                        data = dataMap[filename]
                        objectsToUpload.extend(prepareRecordsInConfigMapForUpload(data, generateIdFromTitle, oldIdToNewIdMap))

                    else:
                        data = dataMap[filename]
                        deleteKibanaObject(f"{metadata.namespace}/{metadata.name}", kibanaBaseUrl, kibanaUsername, kibanaPassword, filename, data, generateIdFromTitle)

                if len(objectsToUpload) > 0:
                    if generateIdFromTitle:
                        objectsToUpload = renameAllIds(oldIdToNewIdMap, objectsToUpload)

                    objectsToUpload = reorderObjectsToUpload(objectsToUpload)

                    upsertKibanaObject(f"{metadata.namespace}/{metadata.name}", kibanaBaseUrl, kibanaUsername,
                                       kibanaPassword, objectsToUpload)



            except Exception as e:
                logger.error(f"Failed to process ConfigMap: {metadata.namespace}/{metadata.name} because: ", exc_info=e)

def main():
    logger.info("Starting config map collector")
    label = os.getenv('LABEL')
    if label is None:
        logger.error("Must configure LABEL as environment variable! Exit")
        return -1
    kibanaBaseUrl = os.getenv('KIBANA_BASE_URL')
    if kibanaBaseUrl is None:
        logger.error("Should have added KIBANA_BASE_URL as environment variable! Exit")
        return -1
    if kibanaBaseUrl.endswith("/"):
        kibanaBaseUrl = kibanaBaseUrl[0:-1]
    logger.info(f"Using Kibana Base URL: {kibanaBaseUrl}")
    logger.info(f"Will load ConfigMaps with label: {label}")
    kibanaUsername = os.getenv('KIBANA_USERNAME')
    kibanaPassword = os.getenv('KIBANA_PASSWORD')

    config.load_incluster_config()
    logger.info("Config for cluster api loaded...")
    currentNamespace = open("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read()
    watchForChanges(label, kibanaBaseUrl, kibanaUsername, kibanaPassword, currentNamespace)


if __name__ == '__main__':
    main()
