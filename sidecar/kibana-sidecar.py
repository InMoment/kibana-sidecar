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
        raise Exception("No url provided")
    elif method == "GET" or method is None:
        res = r.get("%s" % url, auth=auth, params=queryParams, timeout=30, headers=headers)
        logger.info("%s request sent to %s. Response: %d %s" % (method, url, res.status_code, res.reason))
        return res
    elif method == "POST":
        res = r.post("%s" % url, auth=auth, params=queryParams, json=payload, timeout=30, headers=headers)
        logger.info("%s request sent to %s. Response: %d %s" % (method, url, res.status_code, res.reason))
        return res
    elif method == "PUT":
        res = r.put("%s" % url, auth=auth, params=queryParams, json=payload, timeout=30, headers=headers)
        logger.info("%s request sent to %s. Response: %d %s" % (method, url, res.status_code, res.reason))
        return res
    else:
        # If method is not provided use GET as default
        raise Exception("Unsupported method: " + method)

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
            if 'attributes' in o and 'title' in o["attributes"]:
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
        logger.error("Failed to save all objects because: ", exc_info=True)

def updateWatcherObjects(configMapName, elasticSearchBaseUrl, elasticMajorVersion, kibanaUsername, kibanaPassword, watcherObjects):

    logger.info(f"Creating/Updating in Watcher: {elasticSearchBaseUrl}: Watcher Object(s) with data: \n{json.dumps(watcherObjects)} ...")

    watcherBaseUrl = None
    if elasticMajorVersion == "6":
        watcherBaseUrl = f"{elasticSearchBaseUrl}/_xpack/watcher/watch"
    elif elasticMajorVersion == "7":
        watcherBaseUrl = f"{elasticSearchBaseUrl}/_watcher/watch"
    else:
        logger.error(f"Unsupported Elastic Search Major Version: {elasticMajorVersion}")
        raise Exception(f"Unsupported Elastic Search Major Version: {elasticMajorVersion}")

    for watcher in watcherObjects:

        try:
            if not 'id' in watcher:
                raise Exception(f"Watcher didn't contain an 'id' property: {json.dumps(watcher)}")
            watchId = watcher["id"]
            active = "true"
            if 'active' in watcher:
                active = watcher['active']

            # These parameters shouldn't actually be in the POST to Watcher API.
            if 'id' in watcher:
                del watcher['id']
            if 'active' in watcher:
                del watcher['active']

            logger.debug(f"POSTing data:\n{json.dumps(watcher)}")

            res = request(f"{watcherBaseUrl}/{watchId}", kibanaUsername, kibanaPassword, "PUT",
                          {"active": active}, watcher, {"kbn-xsrf": "kibana-sidecar"})
            if res.status_code != 200 and res.status_code != 201:
                logger.error(
                    f"Failed to create Watcher object: {watcher} because request returned status: {res.status_code} and body: {res.text}")
            else:
                responseBody = res.json()
                logger.debug(f"Response from Kibana: {json.dumps(responseBody)}")

                logger.info(f"Watcher with ID: {watchId} saved successfully.")
        except Exception as e:
            logger.error(f"Failed to save Watcher with ID: {watchId} because: ", exc_info=True)



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

def watchForChanges(label, kibanaBaseUrl, elasticSearchBaseUrl, kibanaUsername, kibanaPassword, currentNamespace, elasticMajorVersion):
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
                    (kibanaObjects, watcherObjects) = separateKibanaFromWatcherObjects(objectsToUpload)
                    if generateIdFromTitle:
                        kibanaObjects = renameAllIds(oldIdToNewIdMap, kibanaObjects)

                    kibanaObjects = reorderObjectsToUpload(kibanaObjects)

                    upsertKibanaObject(f"{metadata.namespace}/{metadata.name}", kibanaBaseUrl, kibanaUsername,
                                       kibanaPassword, kibanaObjects)

                    watcherObjects = prepareWatcherObjectsForUpload(watcherObjects, generateIdFromTitle)
                    if len(watcherObjects) > 0:
                        updateWatcherObjects(f"{metadata.namespace}/{metadata.name}", elasticSearchBaseUrl, elasticMajorVersion, kibanaUsername,
                                       kibanaPassword, watcherObjects)
                    else:
                        logger.info("No Watcher objects to process")



            except Exception as e:
                logger.error(f"Failed to process ConfigMap: {metadata.namespace}/{metadata.name} because: ", exc_info=True)

def separateKibanaFromWatcherObjects(objectsArr):
    kibanaObjects = []
    watcherObjects = []

    for o in objectsArr:
        if 'type' in o:
            kibanaObjects.append(o)
        elif ('input' in o or 'trigger' in o or 'actions' in o):
            watcherObjects.append(o)
        else:
            logger.warn(f"Couldn't determine type of object for object. Ignoring it. Object Contents: {json.dumps(o)}")
    return (kibanaObjects, watcherObjects)

def getDefaultWatcherActions():
    defaultActionsFilePath = os.getenv("DEFAULT_WATCHER_ACTIONS_FILEPATH")
    logger.info(f"Reading Default Watcher Actions from filepath: {defaultActionsFilePath}")
    if defaultActionsFilePath:
        with open(defaultActionsFilePath, 'r') as f:
            defaultActions = json.load(f)
            return defaultActions
    else:
        return {}

def prepareWatcherObjectsForUpload(watcherObjects, generateIdFromTitle):
    # Support generating ID from Watcher name
    if generateIdFromTitle:
        for o in watcherObjects:
            if 'name' in o["metadata"]:
                name = o["metadata"]["name"]
                newId = generateObjectIdFromTitle(name)
                logger.debug(f"Generated ID: {newId} from name: {name}")
                o["id"] = newId

    # Add Default Actions
    defaultActions = getDefaultWatcherActions()
    if len(defaultActions) > 0:
        for o in watcherObjects:
            logger.info(f"Adding {len(defaultActions)} default actions to Watcher with ID: {o['id']}")
            if not 'actions' in o:
                o['actions'] = {}

            for key, value in defaultActions.items():
                logger.debug(f"Adding default action with key: {key} to Watcher with ID: {o['id']}")
                o['actions'][key] = value

    return watcherObjects


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

    elasticSearchBaseUrl = os.getenv('ELASTICSEARCH_BASE_URL')
    if elasticSearchBaseUrl is None:
        logger.error("Should have added ELASTICSEARCH_BASE_URL as environment variable! Exit")
        return -1
    if elasticSearchBaseUrl.endswith("/"):
        elasticSearchBaseUrl = elasticSearchBaseUrl[0:-1]

    logger.info(f"Using Kibana Base URL: {kibanaBaseUrl}")
    logger.info(f"Using ElasticSearch Base URL: {elasticSearchBaseUrl}")
    logger.info(f"Will load ConfigMaps with label: {label}")



    kibanaUsername = os.getenv('KIBANA_USERNAME')
    kibanaPassword = os.getenv('KIBANA_PASSWORD')

    elasticMajorVersion = None

    logger.info("Figuring out what version of Elastic Search we are dealing with ...")
    res = request(elasticSearchBaseUrl + "/", kibanaUsername, kibanaPassword, "GET", None, None)
    if res.status_code != 200:
        logger.error(
            f"Failed to identify Elastic Search version because request returned status: {res.status_code} and body: {res.text}")
    else:
        responseBody = res.json()
        elasticVersion = responseBody['version']['number']
        elasticMajorVersion = elasticVersion.split(".")[0]
        logger.info(f"Found Elastic Search Version: {elasticVersion} with Major Version: {elasticMajorVersion}")

    if elasticMajorVersion is None:
        logger.error("Could not determine Elastic Search Major Version. Aborting ...")
        return

    if elasticMajorVersion != "6" and elasticMajorVersion != "7":
        logger.error("kibana-sidecar currently only supports Elastic 6 and Elastic 7. Aborting ...")
        return

    config.load_incluster_config()
    logger.info("Config for cluster api loaded...")
    currentNamespace = open("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read()

    while True:
        try:
            watchForChanges(label, kibanaBaseUrl, elasticSearchBaseUrl, kibanaUsername, kibanaPassword, currentNamespace, elasticMajorVersion)
        except Exception as e:
            logger.error("Caught error while attempting to watch for changes. Re-establishing watch...", exc_info=True)


if __name__ == '__main__':
    main()
