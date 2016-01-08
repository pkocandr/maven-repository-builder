import logging
import os
import re
import shutil
import zipfile

from maven_artifact import MavenArtifact
from maven_repo_util import slashAtTheEnd


def generate_report(output, config, artifact_list, report_name):
    """
    Generates report. The report consists of a summary page, groupId pages, artifactId pages and leaf artifact pages.
    Summary page contains list of roots, list of BOMs, list of multi-version artifacts, links to all artifacts and list
    of artifacts that do not match the BOM version. Each artifact has a separate page containing paths from roots to
    the artifact with path explanation.
    """
    multiversion_gas = dict()
    malformed_versions = dict()
    if os.path.exists(output):
        logging.warn("Target report path %s exists. Deleting...", output)
        shutil.rmtree(output)
    os.makedirs(os.path.join(output, "pages"))

    roots = []
    for artifact_source in config.artifactSources:
        if artifact_source["type"] == "dependency-graph":
            roots.extend(artifact_source['top-level-gavs'])

    groupids = dict()
    version_pattern = re.compile("^.*[.-]redhat-[^.]+$")
    for ga in artifact_list:
        (groupid, artifactid) = ga.split(":")
        priority_list = artifact_list[ga]
        for priority in priority_list:
            versions = priority_list[priority]
            if versions:
                groupids.setdefault(groupid, dict()).setdefault(artifactid, dict()).update(versions)
                if len(groupids[groupid][artifactid]) > 1:
                    multiversion_gas.setdefault(groupid, dict())[artifactid] = groupids[groupid][artifactid]
                for version in versions:
                    if not version_pattern.match(version):
                        malformed_versions.setdefault(groupid, dict()).setdefault(artifactid, dict())[version] = groupids[groupid][artifactid][version]

    optional_artifacts = dict()
    for groupid in groupids.keys():
        artifactids = groupids[groupid]
        for artifactid in artifactids.keys():
            versions = artifactids[artifactid]
            for version in versions.keys():
                art_spec = versions[version]

                ma = MavenArtifact.createFromGAV("%s:%s:%s" % (groupid, artifactid, version))
                generate_artifact_page(ma, roots, art_spec.paths, art_spec.url, output, groupids, optional_artifacts)
            generate_artifactid_page(groupid, artifactid, versions, output)
        generate_groupid_page(groupid, artifactids, output)
    generate_summary(config, groupids, multiversion_gas, malformed_versions, output, report_name, optional_artifacts)
    generate_css(output)


def generate_artifact_page(ma, roots, paths, repo_url, output, groupids, optional_artifacts):
    norm_repo_url = slashAtTheEnd(repo_url)
    html = ("<html><head><title>Artifact {gav}</title>" + \
            "<link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"></head><body>" + \
            "<div class=\"header\"><a href=\"../index.html\">Back to repository summary</a></div>" + \
            "<div class=\"artifact\"><h1>{gav}</h1>" + \
            "<p class=\"breadcrumbs\"><a href=\"groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a>" + \
            "&nbsp;:&nbsp;<a href=\"artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a>" + \
            "&nbsp;:&nbsp;{version}</p>" + \
            "<h2>Paths</h2><ul id=\"paths\">").format(gav=ma.getGAV().replace(":", " : "), groupid=ma.groupId, artifactid=ma.artifactId, version=ma.version)
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

    all_paths_optional = True
    directly_optional = False
    for path in sorted(paths):
        optional_path = False
        rma = path[0].declaring
        li = "<li>"
        is_inherited_or_mixin = False
        for rel in path:
            if hasattr(rel, "inherited") and rel.inherited or hasattr(rel, "mixin") and rel.mixin:
                is_inherited_or_mixin = True
                break
            dec = rel.declaring
            if dec:
                rel_type = rel.rel_type
                if dec.groupId in groupids and dec.artifactId in groupids[dec.groupId] and dec.version in groupids[dec.groupId][dec.artifactId]:
                    add_params = " class=\"optional\"" if optional_path else ""
                    li += "<a href=\"artifact_version_{gav_filename}.html\" title=\"{gav}\"{add_params}>{daid}</a>".format(
                          gav=dec.getGAV().replace(":", " : "), daid=dec.artifactId,
                          gav_filename=dec.getGAV().replace(":", "$"), add_params=add_params)
                else:
                    add_params = " class=\"excluded%s\"" % " optional" if optional_path else ""
                    li += ("<a href=\"{repo_url}{pom_path}\" title=\"{gav} (excluded, the link tries to reference the pom.xml in the same" \
                        + " repo as this artifact)\"{add_params}>{daid}</a>").format(gav=dec.getGAV().replace(":", " : "), daid=dec.artifactId,
                                                                                     repo_url=norm_repo_url, pom_path=dec.getPomFilepath(),
                                                                                     add_params=add_params)
                li += " <span class=\"relation\">"
                if rel_type is None:
                    li += "unknown relation"
                elif rel_type == "DEPENDENCY":
                    if "embedded" in rel.extra:
                        if "optional" in rel.extra:
                            li += "embeds ?optionally?"
                        else:
                            li += "embeds"
                    else:
                        li += "depends on (scope %s)" % rel.extra
                    if "optional" in rel.extra:
                        optional_path = True
                elif rel_type == "PARENT":
                    li += "has parent"
                elif rel_type == "PLUGIN":
                    li += "uses plugin"
                elif rel_type == "PLUGIN_DEP":
                    li += "uses plugin %s with added dependency" % rel.extra
                elif rel_type == "BOM":
                    li += "imports BOM"
                else:
                    li += "unknown relation (%s)" % rel_type
                li += "</span> "
            else:
                li += "... <span class=\"relation\">unknown relation</span> "
        if not is_inherited_or_mixin:
            leaf = path[-1].target
            gav = leaf.getGAV()
            add_params = " class=\"optional\"" if optional_path else ""
            li += "<a href=\"artifact_version_{gav_filename}.html\" title=\"{gav}\"{add_params}>{aid}</a></li>".format(
                gav=gav.replace(":", " : "), gav_filename=gav.replace(":", "$"), aid=leaf.artifactId, add_params=add_params)
            if rma.is_example():
                examples += li
            else:
                html += li
            all_paths_optional &= optional_path
            directly_optional = (path[-1].rel_type == "DEPENDENCY" and "optional" in path[-1].extra)
    html += examples.replace("<li>", "<li class=\"example\">")
    html += "</ul></div><div id=\"pom\"><iframe src=\"{repo_url}{pom_path}\"/></div></body></html>".format(repo_url=norm_repo_url,
                                                                                                      pom_path=ma.getPomFilepath())
    with open(os.path.join(output, "pages", "artifact_version_%s.html" % ma.getGAV().replace(":", "$")), "w") as htmlfile:
        htmlfile.write(html)
    if len(paths) > 0 and all_paths_optional:
        optional_artifacts[ma] = directly_optional


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


def generate_groupid_page(groupid, artifactids, output):
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


def generate_summary(config, groupids, multiversion_gas, malformed_versions, output, report_name, optional_artifacts):
    html = ("<html><head><title>Repository {report_name}</title>" + \
            "<link rel=\"stylesheet\" type=\"text/css\" href=\"http://code.jquery.com/ui/1.11.4/themes/smoothness/jquery-ui.css\">" + \
            "<script src=\"http://code.jquery.com/jquery-1.10.2.js\"></script>" + \
            "<script src=\"http://code.jquery.com/ui/1.11.4/jquery-ui.js\"></script>" + \
            "<link rel=\"stylesheet\" type=\"text/css\" href=\"pages/style.css\">" + \
            "<script>$(function() {script});</script></head><body>" + \
            "<div class=\"artifact\"><h1>{report_name}</h1><div id=\"tabs\">" + \
            "<ul>" + \
            "<li><a href=\"#tab-definition\">Artifact sources</a></li>" + \
            "<li><a href=\"#tab-multi-versioned-artifacts\">Multi-versioned artifacts</a></li>" + \
            "<li><a href=\"#tab-malformed-versions\">Malformed versions</a></li>" + \
            "<li><a href=\"#tab-optional-artifacts\">Optional artifacts</a></li>" + \
            "<li><a href=\"#tab-all-artifacts\">All artifacts</a></li>" + \
            "</ul>" + \
            "<div id=\"tab-definition\">" + \
            "<h2>Artifact sources</h2>").format(report_name=report_name, script="{ $(\"#tabs\").tabs(); }")

    examples = ""
    i = 1
    for artifact_source in config.artifactSources:
        html += "<div class=\"artifact-source\">"
        if artifact_source["type"] == "dependency-graph":
            html += "<h3>Dependency graph #%i</h3><h4>Roots</h4><ul>" % i
            i += 1
            for root in sorted(artifact_source['top-level-gavs']):
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
            html += examples + "</ul><h4>BOMs</h4><ul>"
            if len(artifact_source['injected-boms']):
                for bom in artifact_source['injected-boms']:
                    ma = MavenArtifact.createFromGAV(bom)
                    gid = ma.groupId
                    aid = ma.artifactId
                    ver = ma.version
                    if gid in groupids.keys() and aid in groupids[gid].keys() and ver in groupids[gid][aid]:
                        html += "<li><a href=\"pages/artifact_version_{gid}${aid}${ver}.html\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</a></li>".format(gid=gid, aid=aid, ver=ver)
                    else:
                        html += "<li><span class=\"error\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</span></li>".format(
                                gid=gid, aid=aid, ver=ver)
            else:
                html += "<li><em>none</em></li>"
            if len(artifact_source['excluded-subgraphs']):
                html += "</ul><h4>Excluded subgraphs</h4><ul>"
                for exclusion in artifact_source['excluded-subgraphs']:
                    ma = MavenArtifact.createFromGAV(exclusion)
                    gid = ma.groupId
                    aid = ma.artifactId
                    ver = ma.version
                    if gid in groupids.keys() and aid in groupids[gid].keys() and ver in groupids[gid][aid]:
                        html += "<li><a class=\"error\" href=\"pages/artifact_version_{gid}${aid}${ver}.html\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</a></li>".format(gid=gid, aid=aid, ver=ver)
                    else:
                        html += "<li class=\"excluded\">{gid}&nbsp;:&nbsp;{aid}&nbsp;:&nbsp;{ver}</li>".format(
                                gid=gid, aid=aid, ver=ver)
            if artifact_source['preset'] in ["sob-build", "scope-with-embedded", "requires", "managed-sob-build"]:
                contents = "runtime dependencies"
            elif artifact_source['preset'] in ["sob", "build-env", "build-requires", "br", "managed-sob"]:
                contents = "build-time dependencies"
            else:
                contents = artifact_source['preset']
            html += "</ul><h4>Contents</h4><ul><li>%s</li></ul>" % contents
        else:
            html += artifact_source["type"]
        html += "</div>"
    html += "<div class=\"artifact-source\"><h3>Global</h3><h4>Excluded GA(TC)V patterns</h4><ul>"
    if len(config.excludedGAVs):
        for excluded_pattern in config.excludedGAVs:
            html += "<li class=\"excluded\">%s</li>" % excluded_pattern
    else:
        html += "<li><em>none</em></li>"
    html += "</ul><h4>Excluded repositories</h4><ul>"
    if len(config.excludedRepositories):
        for excluded_repo in config.excludedRepositories:
            html += "<li class=\"excluded\"><a href=\"%(url)s\">%(url)s</li>" % {"url": excluded_repo}
    else:
        html += "<li><em>none</em></li>"
    html += "</ul></div>"

    html += "</div>\n<div id=\"tab-multi-versioned-artifacts\"><h2>Multi-versioned artifacts</h2><ul>"
    for groupid in sorted(multiversion_gas.keys()):
        artifactids = multiversion_gas[groupid]
        for artifactid in sorted(artifactids.keys()):
            html += ("<li><a href=\"pages/groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a>&nbsp;:&nbsp;" + \
                     "<a href=\"pages/artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a>" + \
                     "</li><ul>").format(artifactid=artifactid, groupid=groupid)
            artifacts = artifactids[artifactid]
            for version in sorted(artifacts.keys()):
                gav = "%s:%s:%s" % (groupid, artifactid, version)
                html += "<li><a href=\"pages/artifact_version_{gav_filename}.html\">{gav}</a></li>".format(
                        gav=gav, gav_filename=gav.replace(":", "$"))
            html += "</ul>"

    html += "</ul></div>\n<div id=\"tab-malformed-versions\"><h2>Malformed versions</h2><ul>"
    for groupid in sorted(malformed_versions.keys()):
        artifactids = malformed_versions[groupid]
        for artifactid in sorted(artifactids.keys()):
            artifacts = artifactids[artifactid]
            for version in sorted(artifacts.keys()):
                gav = "%s:%s:%s" % (groupid, artifactid, version)
                html += ("<li><a href=\"pages/groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a>&nbsp;:&nbsp;" + \
                         "<a href=\"pages/artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a>&nbsp;:&nbsp;" + \
                         "<a href=\"pages/artifact_version_{gav_filename}.html\">{gav}</a></li>").format(groupid=ma.groupId,
                                                                                                         artifactid=ma.artifactId,
                                                                                                         gav=gav,
                                                                                                         gav_filename=gav.replace(":", "$"))

    html += "</ul></div>\n<div id=\"tab-optional-artifacts\"><h2>Optional artifacts</h2><h3>Direct optionals</h3><ul>"
    for ma in sorted(optional_artifacts.keys()):
        if optional_artifacts[ma]:
            gav = "%s&nbsp;:&nbsp;%s&nbsp;:&nbsp;%s" % (ma.groupId, ma.artifactId, ma.version)
            gav_filename = "%s$%s$%s" % (ma.groupId, ma.artifactId, ma.version)
            html += ("<li><a href=\"pages/groupid_{groupid}.html\" title=\"GroupId {groupid}\" class=\"optional\">{groupid}</a>&nbsp;:&nbsp;" + \
                     "<a href=\"pages/artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\" class=\"optional\">{artifactid}</a> : " + \
                     "<a href=\"pages/artifact_version_{gav_filename}.html\" class=\"optional\">{version}</a></li>").format(groupid=ma.groupId,
                                                                                                         artifactid=ma.artifactId,
                                                                                                         version=ma.version,
                                                                                                         gav_filename=gav_filename)
    html += "</ul><h3>Transitive optionals</h3><ul>"
    for ma in sorted(optional_artifacts.keys()):
        if not optional_artifacts[ma]:
            gav_filename = "%s$%s$%s" % (ma.groupId, ma.artifactId, ma.version)
            html += ("<li><a href=\"pages/groupid_{groupid}.html\" title=\"GroupId {groupid}\" class=\"optional\">{groupid}</a> : " + \
                     "<a href=\"pages/artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\" class=\"optional\">{artifactid}</a> : " + \
                     "<a href=\"pages/artifact_version_{gav_filename}.html\" class=\"optional\">{version}</a></li>").format(groupid=ma.groupId,
                                                                                                         artifactid=ma.artifactId,
                                                                                                         version=ma.version,
                                                                                                         gav_filename=gav_filename)
    html += "</ul></div>\n<div id=\"tab-all-artifacts\"><h2>All artifacts</h2><ul>"
    for groupid in sorted(groupids.keys()):
        artifactids = groupids[groupid]
        for artifactid in sorted(artifactids.keys()):
            artifacts = artifactids[artifactid]
            for version in sorted(artifacts.keys()):
                gav = "%s:%s:%s" % (groupid, artifactid, version)
                html += ("<li><a href=\"pages/groupid_{groupid}.html\" title=\"GroupId {groupid}\">{groupid}</a>&nbsp;:&nbsp;" + \
                         "<a href=\"pages/artifactid_{groupid}${artifactid}.html\" title=\"ArtifactId {artifactid}\">{artifactid}</a> : " + \
                         "<a href=\"pages/artifact_version_{gav_filename}.html\">{version}</a></li>").format(groupid=groupid, artifactid=artifactid, version=version, gav_filename=gav.replace(":", "$"))
    html += "</ul></div></div></div></body></html>"
    with open(os.path.join(output, "index.html"), "w") as htmlfile:
        htmlfile.write(html)


def generate_css(output):
    css = "body { background-color: white }\n" \
        + "a { color: blue }\n" \
        + ".artifact-source { border: solid 0.15em #bbb; margin: 1em 0; padding: 0.5em; }\n" \
        + ".artifact-source h3 { margin: 0.5em 0 0; }\n" \
        + ".error, .error a { color: red }\n" \
        + ".excluded { text-decoration: line-through }\n" \
        + ".example, .example a { color: cornflowerblue }\n" \
        + ".optional, a.optional { color: #6c8 }\n" \
        + ".relation { color: grey; font-size: 0.8em }\n" \
        + "#paths li { padding-bottom: 1em }\n" \
        + "#pom iframe {width: 100%; height: 60em;}"
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
