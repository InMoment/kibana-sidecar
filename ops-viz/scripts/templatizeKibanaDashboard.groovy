#!/usr/bin/env groovy

import groovy.util.CliBuilder;
import java.io.File;
import groovy.json.JsonSlurper
import groovy.json.JsonOutput

def normalizeToUiFormat(o) {
    if(o["attributes"]) {
        o["_source"] = o["attributes"]
        o.remove("attributes")
    }

    if(o["id"]) {
        o["_id"] = o["id"]
        o.remove("id")
    }

    if(o["type"]) {
        o["_type"] = o["type"]
        o.remove("type")
    }
    return o;
}
def replaceIdWithInstanceNameToken(o) {
    if(!o["_id"].contains("\$(instance_name)")) {
        o["_id"] = "\$(instance_name)_" + o["_id"]
    }
    return o
}

def generateFilenameFromTitle(title) {
    return title.toLowerCase().replaceAll("[^a-z-_0-9*]", "_")
}

def generateIdFromTitle(title) {
    return generateFilenameFromTitle(title)
            .replace("__instance_name_", "\$(instance_name)")
}

def updateIds(oldIdToNewIdMap, jsonStr) {
    oldIdToNewIdMap.each { oldId, newId ->
        jsonStr = jsonStr.replace("\"" + oldId + "\"", "\"" + newId + "\"").replace(
            "\\\"" + oldId + "\\\"", "\\\"" + newId + "\\\"")
    }
    return jsonStr
}

public static void main(String[] args) {

    def cli = new CliBuilder(usage: 'templatizeKibanaDashboard.groovy [options]',
            header: 'Options:')

    cli.f(args: 1, argName: 'file', 'The Kibana Export File to templatize.\nYou can specify multiple files with multiple -f options.\nShould contain a JSON Array of objects or a single JSON object either exported via the Kibana UI or API.')
    cli.i(args: 1, argName: 'instance-name', 'The Helm Instance Name that this dashboard was exported for. e.g., tap-api-tag')
    cli.o(args: 1, argName: 'output-dir', 'The Output Directory where to output the individual Kibana template files. One file will be output per object.')
    def options = cli.parse(args)

    if (!options.f) {
        System.err.println("ERROR: Must supply filename");
        cli.usage()
        System.exit(1)
    }

    if (!options.i) {
        System.err.println("ERROR: Must supply instance-name");
        cli.usage()
        System.exit(1)
    }

    if (!options.o) {
        System.err.println("ERROR: Must supply output-dir");
        cli.usage()
        System.exit(1)
    }
    

    def filenames = options.fs;
    def instanceName = options.i;
    def outputDir = options.o;

    println("Templatizing file: $filenames using parameters:")
    println("\$(instance_name) = $instanceName")
    objects = []
    def jsonSlurper = new JsonSlurper()
    filenames.each { filename ->
        def text = new File(filename).text;


        def objectsInFile = jsonSlurper.parseText(text)
        if(!(objectsInFile instanceof List)) {
            objectsInFile = [objectsInFile]
        }
        objects.addAll(objectsInFile)
    }
    

    outputToWriteByFilename = [:]
    oldIdToNewIdMap = [:]

    objects.each { o ->
        o = normalizeToUiFormat(o)
        def jsonOutput = JsonOutput.prettyPrint(JsonOutput.toJson(o))
        jsonOutput = jsonOutput.replace(instanceName, "\$(instance_name)")
        o = jsonSlurper.parseText(jsonOutput)
        def title = o["_source"]["title"]
        def type = o["_type"]
        def id = o["_id"]
        oldIdToNewIdMap[id] = generateIdFromTitle(title)

        def outputFilename = generateFilenameFromTitle(title) + "-" + type + ".json"
        def outputFile = new File(new File(outputDir), outputFilename)

        jsonOutput = JsonOutput.prettyPrint(JsonOutput.toJson(o))
        outputToWriteByFilename[outputFile] = jsonOutput

    }

    outputToWriteByFilename.each { outputFile, outputJsonStr ->
        outputJsonStr = updateIds(oldIdToNewIdMap, outputJsonStr)
        outputFile.text = outputJsonStr
        println("Wrote output file to: ${outputFile.getAbsolutePath()}")

    }





}
