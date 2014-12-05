#!/usr/bin/env python
import json
import logging
import optparse
import os
import shutil
import zipfile

from maven_artifact import MavenArtifact


def parse_opts():
    """ Reads input options. """

    usage = "Usage: %prog -b BUILTGAVSLIST -c CACHEDIR -t ROOTS -m BOMS -r REPOSITORY -o OUTPUT_DIRECTORY"
    description = ("Analyze a Maven repository built from AProx depgraphs.")

    cliOptParser = optparse.OptionParser(usage=usage, description=description)
    cliOptParser.add_option(
        '-b', '--builtgavs',
        help='File containing list of built GAVs in this release generated from the MakeMEAD config.'
    )
    cliOptParser.add_option(
        '-c', '--cachedir',
        help='Path to cache directory containing json cache files.'
    )
    #cliOptParser.add_option(
    #    '-a', '--aproxurl',
    #    help='AProx URL to read artifact relationships'
    #)
    cliOptParser.add_option(
        '-t', '--roots',
        help='Comma-separated list of root GAVs.'
    )
    cliOptParser.add_option(
        '-m', '--boms',
        help='Comma-separated list of BOM GAVs.'
    )
    cliOptParser.add_option(
        '-r', '--repository',
        help='Name of directory or zip file that contains the repository. If the value ends with zip it unpacks it and '
             'expects the repository in path "*/maven-repository/".'
    )
    cliOptParser.add_option(
        '-o', '--output',
        help='Name of directory where the report should be generated.'
    )
    cliOptParser.add_option(
        '-l', '--loglevel',
        default='info',
        help='Set the level of log output. Can be set to debug, info, warning, error, or critical.'
    )
    cliOptParser.add_option(
        '-L', '--logfile',
        help='Set the file in which the log output should be written.'
    )

    options = cliOptParser.parse_args()[0]

    return options


def main():
    options = parse_opts()

    # Set the log level
    set_log_level(options.loglevel, options.logfile)

    # unzip the repo if needed
    if options.repository.endswith(".zip"):
        temp_repo_dir = "temp-maven-repo"
        unzip(options.repository, temp_repo_dir)
        filenames = os.listdir(temp_repo_dir)
        if len(filenames) == 1:
            repo_dir = os.path.join(temp_repo_dir, filenames[0], "maven-repository")
            if not os.path.exists(repo_dir):
                raise "Unknown repository layout in zip %s, maven-repository dir not found in %s." % (options.repository,
                                                                                                     filenames[0])
        else:
            raise "Unknown repository layout in zip %s, found more than 1 entries inside: %s" % (options.repository,
                                                                                                 str(filenames))
    else:
        repo_dir = options.repository

    # crawl the repo and find all poms, read the GAVs from them and place them in a dictionary
    groupids = dict()
    for directory, subdirs, filenames in os.walk(repo_dir):
        for filename in filenames:
            if filename.endswith(".pom"):
                ma = MavenArtifact.createFromPomPath(os.path.join(directory, filename)[len(repo_dir) + 1:])
                versions = groupids.setdefault(ma.groupId, dict()).setdefault(ma.artifactId, dict())
                if ma.version in versions:
                    raise "Duplicate processing of %s" % str(ma)
                versions[ma.version] = ma

    # go through the cache files if available to identify which artifact was contained in the result and create record
    # of that in the dictionary
    for cache_filename in os.listdir(options.cachedir):
        if cache_filename.endswith(".cache"):
            cacheroot = None
            for root in options.roots.split(","):
                if root in cache_filename:
                    cacheroot = root
                    break
            if cacheroot:
                with open(os.path.join(options.cachedir, cache_filename)) as cache_file:
                    data = json.load(cache_file)
                    logging.debug("Loaded json cache file %s", cache_file)
                for groupid in groupids.keys():
                    for artifactid in groupids[groupid].keys():
                        for version in groupids[groupid][artifactid].keys():
                            ma = groupids[groupid][artifactid][version]
                            logging.debug("Checking if %s included in %s", ma.getGAV(), cache_filename)
                            if ma.getGAV() in data:
                                logging.debug("%s was found in %s", ma.getGAV(), cache_filename)
                                ma.roots.append(cacheroot)
            else:
                logging.warn("None of %s was found as root for cache file %s", options.roots, cache_filename)

    # record path from each of the roots to each of the poms and create record of that in the dictionary
    # FIXME
    generate_report(options.output, options.roots, options.boms, groupids)


def generate_report(output, artifactSources, artifactList, report_name):
    """
    Generates report. The report consists of a summary page, groupId pages, artifactId pages and leaf artifact pages.
    Summary page contains list of roots, list of BOMs, list of multi-version artifacts, links to all artifacts and list
    of artifacts that do not match the BOM version. Each artifact has a separate page containing paths from roots to
    the artifact with path explanation.
    """
    multiversion_gas = dict()
    if os.path.exists(output):
        logging.warn("Target report path %s exists. Deleting...", output)
        shutil.rmtree(output)
    os.makedirs(os.path.join(output, "pages"))

    roots = []
    boms = set()
    for artifact_source in artifactSources:
        if artifact_source["type"] == "dependency-graph":
            roots.extend(artifact_source['top-level-gavs'])
            boms = boms.union(artifact_source['injected-boms'])
    boms = sorted(list(boms))

    groupids = dict()
    for ga in artifactList:
        (groupid, artifactid) = ga.split(":")
        priorityList = artifactList[ga]
        for priority in priorityList:
            versions = priorityList[priority]
            if versions:
                groupids.setdefault(groupid, dict()).setdefault(artifactid, dict()).update(versions)
                if len(groupids[groupid][artifactid]) > 1:
                    multiversion_gas.setdefault(groupid, dict())[artifactid] = groupids[groupid][artifactid]

    for groupid in groupids.keys():
        artifactids = groupids[groupid]
        for artifactid in artifactids.keys():
            versions = artifactids[artifactid]
            for version in versions.keys():
                art_spec = versions[version]
    
                ma = MavenArtifact.createFromGAV("%s:%s:%s" % (groupid, artifactid, version))
                generate_artifact_page(ma, roots, art_spec.paths, output)
            generate_artifactid_page(groupid, artifactid, versions, output)
        generate_groupid_page(groupid, artifactids, multiversion_gas, output)
    generate_summary(roots, boms, groupids, multiversion_gas, output, report_name)
    generate_css(output)


def generate_artifact_page(ma, roots, paths, output):
    html = ("<html><head><title>Artifact {gav}</title>" + \
            "<link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"></head><body>" + \
            "<div class=\"header\"><a href=\"../index.html\">Back to repository summary</a></div>" + \
            "<div class=\"artifact\"><h1>{gav}</h1>" + \
            "<p class=\"breadcrumbs\"><a href=\"groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a>" + \
            "&nbsp;:&nbsp;<a href=\"artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a>" + \
            "&nbsp;:&nbsp;{version}</p>" + \
            "<h2>Paths</h2><ul>").format(gav=ma.getGAV().replace(":", " : "), groupid=ma.groupId, artifactid=ma.artifactId, version=ma.version)
    examples = ""
    if ma.getGAV() in roots:
        li = "<li>"
        li += "<a href=\"artifact_version_{gav_filename}.html\" title=\"{gav}\">{aid}</a>".format(
              gav=ma.getGAV().replace(":", " : "), aid=ma.artifactId, gav_filename=ma.getGAV().replace(":", "$"))
        li += " <span class=\"relation\">is root</span>"
        if ma.is_example():
            examples += li
        else:
            html += li

    for path in sorted(paths):
        rma = path[0].declaring
        li = "<li>"
        for rel in path:
            dec = rel.declaring
            if dec:
                rel_type = rel.rel
                li += "<a href=\"artifact_version_{gav_filename}.html\" title=\"{gav}\">{daid}</a>".format(
                      gav=dec.getGAV().replace(":", " : "), daid=dec.artifactId,
                      gav_filename=dec.getGAV().replace(":", "$"))
                li += " <span class=\"relation\">"
                if rel_type is None:
                    li += "unknown relation"
                elif rel_type == "DEPENDENCY":
                    if rel.scope == "embedded":
                        li += "embeds"
                    else:
                        li += "depends on (scope %s)" % rel.scope
                elif rel_type == "PARENT":
                    li += "has parent"
                elif rel_type == "BOM":
                    li += "imports BOM"
                else:
                    li += "unknown relation (%s)" % rel_type
                li += "</span> "
            else:
                li += "... <span class=\"relation\">unknown relation</span> "
        leaf = path[-1].target
        gav = leaf.getGAV()
        li += "<a href=\"artifact_version_{gav_filename}.html\" title=\"{gav}\">{aid}</a></li>".format(
              gav=gav.replace(":", " : "), gav_filename=gav.replace(":", "$"), aid=leaf.artifactId)
        if rma.is_example():
            examples += li
        else:
            html += li
    html += examples.replace("<li>", "<li class=\"example\">")
    html += "</ul></div></body></html>"
    with open(os.path.join(output, "pages", "artifact_version_%s.html" % ma.getGAV().replace(":", "$")), "w") as htmlfile:
        htmlfile.write(html)


def generate_artifactid_page(groupid, artifactid, artifacts, output):
    html = ("<html><head><title>ArtifactId {groupid}:{artifactid}</title>" + \
            "<link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"></head><body>" + \
            "<div class=\"header\"><a href=\"../index.html\">Back to repository summary</a></div>" + \
            "<div class=\"artifact\"><h1>{groupid}:{artifactid}</h1>" + \
            "<p class=\"breadcrumbs\"><a href=\"groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a>" + \
            "&nbsp;:&nbsp;<a href=\"artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a></p>" + \
            "<h2>Versions</h2><ul>").format(groupid=groupid, artifactid=artifactid)
    for version in sorted(artifacts.keys()):
        gav = "%s:%s:%s" % (groupid, artifactid, version)
        html += "<li><a href=\"artifact_version_{gav_filename}.html\">{version}</a></li>".format(
                version=version, gav_filename=gav.replace(":", "$"))
    html += "</ul></div></body></html>"
    with open(os.path.join(output, "pages",
                           "artifactid_{groupid}${artifactid}.html".format(groupid=groupid, artifactid=artifactid)
                           ), "w") as htmlfile:
        htmlfile.write(html)


def generate_groupid_page(groupid, artifactids, multiversion_gas, output):
    html = ("<html><head><title>GroupId {groupid}</title>" + \
            "<link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"></head><body>" + \
            "<div class=\"header\"><a href=\"../index.html\">Back to repository summary</a></div>" + \
            "<div class=\"artifact\"><h1>{groupid}</h1>" + \
            "<p class=\"breadcrumbs\"><a href=\"groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a></p>" + \
            "<h2>Artifacts</h2><ul>").format(groupid=groupid)
    for artifactid in sorted(artifactids.keys()):
        html += ("<li><a href=\"artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a></li><ul>" + \
                 "").format(groupid=groupid, artifactid=artifactid)
        artifacts = artifactids[artifactid]
        for version in sorted(artifacts.keys()):
            gav = "%s:%s:%s" % (groupid, artifactid, version)
            html += ("<li><a href=\"artifact_version_{gav_filename}.html\">{ver}</a></li>" + \
                     "").format(ver=version, gav_filename=gav.replace(":", "$"))
        html += "</ul>"
    html += "</ul></div></body></html>"
    with open(os.path.join(output, "pages", "groupid_%s.html" % groupid), "w") as htmlfile:
        htmlfile.write(html)


def generate_summary(roots, boms, groupids, multiversion_gas, output, report_name):
    html = ("<html><head><title>Repository {report_name}</title>" + \
            "<link rel=\"stylesheet\" type=\"text/css\" href=\"pages/style.css\"></head><body>" + \
            "<div class=\"artifact\"><h1>{report_name}</h1>" + \
            "").format(report_name=report_name)
    html += "<h2>Repo roots</h2><ul>"
    examples = ""
    for root in sorted(roots):
        ma = MavenArtifact.createFromGAV(root)
        gid = ma.groupId
        aid = ma.artifactId
        ver = ma.version
        if gid in groupids.keys() and aid in groupids[gid].keys() and ver in groupids[gid][aid]:
            if ma.is_example():
                examples += "<li class=\"error\"><a href=\"pages/artifact_version_{gid}${aid}${ver}.html\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</a></li>".format(
                            gid=gid, aid=aid, ver=ver)
            else:
                html += "<li><a href=\"pages/artifact_version_{gid}${aid}${ver}.html\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</a></li>".format(
                        gid=gid, aid=aid, ver=ver)
        else:
            if ma.is_example():
                examples += "<li class=\"example\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</li>".format(gid=gid, aid=aid, ver=ver)
            else:
                html += "<li class=\"error\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</li>".format(
                        gid=gid, aid=aid, ver=ver)
    html += examples + "</ul><h2>BOMs</h2><ul>"
    for bom in sorted(boms):
        ma = MavenArtifact.createFromGAV(bom)
        gid = ma.groupId
        aid = ma.artifactId
        ver = ma.version
        if gid in groupids.keys() and aid in groupids[gid].keys() and ver in groupids[gid][aid]:
            html += "<li><a href=\"pages/artifact_version_{gid}${aid}${ver}.html\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</a></li>".format(gid=gid, aid=aid, ver=ver)
        else:
            html += "<li><span class=\"error\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</span></li>".format(
                    gid=gid, aid=aid, ver=ver)
    html += "</ul><h2>Multi-versioned artifacts</h2><ul>"
    for groupid in sorted(multiversion_gas.keys()):
        html += ("<li><a href=\"pages/groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a></li><ul>" + \
                 "").format(groupid=groupid)
        artifactids = multiversion_gas[groupid]
        for artifactid in sorted(artifactids.keys()):
            html += ("<li><a href=\"pages/artifactid_{artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a></li><ul>" + \
                     "").format(artifactid=artifactid)
            artifacts = artifactids[artifactid]
            for version in sorted(artifacts.keys()):
                gav = "%s:%s:%s" % (groupid, artifactid, version)
                html += "<li><a href=\"pages/artifact_version_{gav_filename}.html\">{gav}</a></li>".format(
                        gav=gav, gav_filename=gav.replace(":", "$"))
            html += "</ul>"
        html += "</ul>"
    html += "</ul><h2>All artifacts</h2><ul>"
    for groupid in sorted(groupids.keys()):
        html += "<li><a href=\"pages/groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a></li><ul>".format(
                groupid=groupid)
        artifactids = groupids[groupid]
        for artifactid in sorted(artifactids.keys()):
            html += ("<li><a href=\"pages/artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a></li><ul>" + \
                     "").format(groupid=groupid, artifactid=artifactid)
            artifacts = artifactids[artifactid]
            for version in sorted(artifacts.keys()):
                gav = "%s:%s:%s" % (groupid, artifactid, version)
                html += "<li><a href=\"pages/artifact_version_{gav_filename}.html\">{version}</a></li>".format(
                        version=version, gav_filename=gav.replace(":", "$"))
            html += "</ul>"
        html += "</ul>"
    html += "</ul></div></body></html>"
    with open(os.path.join(output, "index.html"), "w") as htmlfile:
        htmlfile.write(html)


def generate_css(output):
    css = ".error, .error a { color: red }\n.example, .example a { color: grey }\n.relation { color: grey; font-size: 0.8em }"
    with open(os.path.join(output, "pages", "style.css"), "w") as cssfile:
        cssfile.write(css)


def unzip(repository_zip, target_dir):
    if os.path.exists(target_dir):
        logging.warn("Target zip extract path %s exists. Deleting...", target_dir)
        shutil.rmtree(target_dir)
    zfile = zipfile.ZipFile(repository_zip)
    for name in zfile.namelist():
        _dirname = os.path.split(name)[0]
        dirname = os.path.join(target_dir, _dirname)
        logging.debug("Extracting %s into %s", name, dirname)
        zfile.extract(name, target_dir)


def set_log_level(level, logfile=None):
    """Sets the desired log level."""
    log_level = getattr(logging, level.upper(), None)
    unknown_level = False
    if not isinstance(log_level, int):
        unknown_level = True
        log_level = logging.INFO
    if logfile:
        logging.basicConfig(format='%(levelname)s (%(threadName)s): %(message)s', level=log_level, filename=logfile,
                            filemode='w')
    else:
        logging.basicConfig(format='%(levelname)s (%(threadName)s): %(message)s', level=log_level)

    if unknown_level:
        logging.warning('Unrecognized log level: %s. Log level set to info', level)


if __name__ == '__main__':
    main()

