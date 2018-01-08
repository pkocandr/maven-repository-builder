#!/usr/bin/env python

import reporter
import logging
import optparse

import maven_repo_util
from configuration import Configuration
from artifact_list_builder import ArtifactListBuilder
from filter import Filter
from maven_artifact import MavenArtifact


def main():
    description = "Generate artifact list from sources defined in the given configuration file"
    cliOptParser = optparse.OptionParser(usage="Usage: %prog -c CONFIG", description=description)
    cliOptParser.add_option(
        '-c', '--config', dest='config',
        help='Configuration file to use for generation of an artifact list for the repository builder'
    )
    cliOptParser.add_option(
        '-a', '--classifiers', default='sources',
        help='Comma-separated list of additional classifiers to download. It is possible to use "__all__" to '
             'request all available classifiers. There can be a type specified with each classifiers separated '
             'by colon, e.g. sources:jar.'
    )
    cliOptParser.add_option(
        '-t', '--threadnum',
        type="int",
        default=5,
        help='Number of download threads per server when downloading artifacts. Default is 5, max is 20.'
    )
    cliOptParser.add_option(
        '-x', '--excludedtypes',
        default='zip:ear:war:tar:gz:tar.gz:bz2:tar.bz2:7z:tar.7z',
        help='Colon-separated list of filetypes to exclude. Defaults to '
             'zip:ear:war:tar:gz:tar.gz:bz2:tar.bz2:7z:tar.7z.'
    )
    cliOptParser.add_option(
        '--nocache',
        dest="cache",
        default=True,
        action='store_false',
        help='Don\'t use any cache (dependency graph cache etc.).'
    )
    cliOptParser.add_option(
        '-w', '--whitelist',
        help='Name of a file containing GATCV patterns allowing usage of stars or regular expressions when enclosed '
             'in "r/pattern/". It can force inclusion of artifacts with excluded types.'
    )
    cliOptParser.add_option(
        "-R", '--reportdir',
        dest="reportdir",
        default=None,
        help='Dir where to generate the repository analysis report. If not specified no report will be generated.'
    )
    cliOptParser.add_option(
        "-N", '--reportname',
        dest="reportname",
        default="Maven Repository",
        help='Name of the repository to be used in the analysis report. It is used only when reportdir value is specified.'
    )
    cliOptParser.add_option(
        '-l', '--loglevel',
        default='info',
        help='Set the level of log output. Can be set to debug, info, warning, error, or critical'
    )
    cliOptParser.add_option(
        '-L', '--logfile',
        help='Set the file in which the log output should be written.'
    )
    (options, args) = cliOptParser.parse_args()
    if options.threadnum < 1:
        logging.warn("Thread number cannot be lower than 1. Using 1.")
        options.threadnum = 1
    elif options.threadnum > 20:
        logging.warn("Thread number cannot be higher than 20. Using 20.")
        options.threadnum = 20

    # Set the log level
    maven_repo_util.setLogLevel(options.loglevel, options.logfile)

    artifactList = _generateArtifactList(options, args)

    _printArtifactList(artifactList)


def generateArtifactList(options, args):
    """
    Generates artifact "list" from sources defined in the given configuration in options. The result
    is dictionary with following structure:

    <repo url> (string)
      L list of MavenArtifact
    """

    artifactList = _generateArtifactList(options, args)
    #build sane structure - url to MavenArtifact list
    urlToMAList = {}
    for ga in artifactList:
        priorityList = artifactList[ga]
        for priority in priorityList:
            versionList = priorityList[priority]
            for version in versionList:
                artSpec = versionList[version]
                url = artSpec.url
                for artType in artSpec.artTypes.keys():
                    for classifier in artSpec.artTypes[artType].classifiers:
                        if classifier:
                            gatcv = "%s:%s:%s:%s" % (ga, artType, classifier, version)
                        else:
                            gatcv = "%s:%s:%s" % (ga, artType, version)
                        artifact = MavenArtifact.createFromGAV(gatcv)
                        urlToMAList.setdefault(url, []).append(artifact)
    return urlToMAList


def _generateArtifactList(options, args):

    config = Configuration()
    if options.config or not args:
        # load configuration
        logging.info("Loading configuration...")
        config.load(options)
    else:
        # create configuration
        logging.info("Creating configuration...")
        config.create(options, args)

    # build list
    logging.info("Building artifact list...")
    listBuilder = ArtifactListBuilder(config)
    artifactList = listBuilder.buildList()

    logging.debug("Generated list contents:")
    _logAL(artifactList)

    #filter list
    logging.info("Filtering artifact list...")
    listFilter = Filter(config)
    artifactList = listFilter.filter(artifactList, options.threadnum)

    logging.debug("Filtered list contents:")
    _logAL(artifactList)

    logging.info("Artifact list generation done")

    if options.reportdir:
        logging.info("Generating repository analysis report")
        if hasattr(options, "reportname"):
            reporter.generate_report(options.reportdir, config, artifactList, options.reportname)
        else:
            reporter.generate_report(options.reportdir, config, artifactList, None)
        logging.info("Report has been generated")

    return artifactList


def _logAL(artifactList):
    for ga in artifactList:
        priorityList = artifactList[ga]
        for priority in priorityList:
            versionList = priorityList[priority]
            for version in versionList:
                artSpec = versionList[version]
                for artType in artSpec.artTypes.keys():
                    for classifier in artSpec.artTypes[artType].classifiers:
                        if classifier == "":
                            logging.debug("  %s:%s:%s", ga, artType, version)
                        else:
                            logging.debug("  %s:%s:%s:%s", ga, artType, classifier, version)


def _printArtifactList(artifactList, printFormat="{url}\t{gatcv}"):
    """
    Prints each artifact from given artifact list with its url on each line. The default format
    of each line is "{url}\t{gatcv}". Available variables are {gatcv}, {groupId}, {artifactId}, {version}, 
    {type}, {classifier}, {priority}.

    :param artifactList: artifact structure to print
    :param printFormat: print format to use (not mandatory)
    """
    for ga in artifactList:
        for priority in artifactList[ga]:
            for version in artifactList[ga][priority]:
                for artType in artifactList[ga][priority][version].artTypes:
                    for classifier in artifactList[ga][priority][version].artTypes[artType].classifiers:
                        if classifier:
                            gatcv = "%s:%s:%s:%s" % (ga, artType, classifier, version)
                        else:
                            gatcv = "%s:%s:%s" % (ga, artType, version)
                        (groupId, artifactId) = ga.split(":")
                        values = {"gatcv": gatcv, "groupId": groupId, "artifactId": artifactId, "version": version, 
                                  "type": artType, "classifier": classifier, "priority": priority, 
                                  "url": artifactList[ga][priority][version].url}
                        print printFormat.format(**values)


if __name__ == '__main__':
    main()
