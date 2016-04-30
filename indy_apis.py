from maven_repo_util import slashAtTheEnd

import hashlib
import httplib
import json
import logging
import os
import re
import time
import urllib
import urlparse
from configuration import Configuration
from subprocess import Popen
from subprocess import PIPE

class UrlRequester:

    def _request(self, method, url, params, data, headers):
        """
        Makes request defined by input params.

        :returns: instance of httplib.HTTPResponse
        """
        parsed_url = urlparse.urlparse(url)
        protocol = parsed_url[0]
        if params:
            encParams = urllib.urlencode(params)
        else:
            encParams = ""
        if protocol == 'http':
            connection = httplib.HTTPConnection(parsed_url[1])
        else:
            connection = httplib.HTTPSConnection(parsed_url[1])
        if not headers:
            headers = {}
        connection.request(method, parsed_url[2] + "?" + encParams, data, headers)
        response = connection.getresponse()
        if response.status in (301, 302):
            location = response.getheader("Location")
            parsed_loc = urlparse.urlparse(location)
            if not parsed_loc.scheme:
                target = urlparse.urlunparse([parsed_url.scheme, parsed_url.netloc, parsed_loc.path, parsed_loc.params,
                                              parsed_loc.query, parsed_loc.fragment])
            else:
                target = location
            return self._request(method, target, params, data, headers)
        else:
            return response

    def _getUrl(self, url, params=None, headers=None):
        return self._request("GET", url, params, None, headers)

    def _postUrl(self, url, params=None, data=None, headers=None):
        """
        Calls POST http request to the given URL.

        :returns: instance of httplib.HTTPResponse
        """
        return self._request("POST", url, params, data, headers)

    def _putUrl(self, url, params=None, data=None, headers=None):
        """
        Calls PUT http request to the given URL.

        :returns: instance of httplib.HTTPResponse
        """
        return self._request("PUT", url, params, data, headers)

    def _deleteUrl(self, url, headers=None):
        """
        Calls DELETE http request of the given URL.

        :returns: response status code
        """
        response = self._request("DELETE", url, None, None, headers)
        return response.status


class IndyApi(UrlRequester):
    """
    Class allowing to communicate with the Indy REST API.
    """

    API_PATH = "api/"
    CACHE_PATH = "cache"

    def __init__(self, indy_url):
        self._indy_url = slashAtTheEnd(indy_url)

    def deleteWorkspace(self, wsid):
        """
        Deletes a specified workspace.

        :param wsid: workspace ID
        :returns: True if the workspace was deleted, False otherwise
        """
        strWsid = str(wsid)
        url = (self._indy_url + self.API_PATH + "depgraph/ws/%s") % strWsid
        logging.info("Deleting Indy workspace with ID %s", strWsid)
        status = self._deleteUrl(url)
        if status / 10 == 20: # any 20x status code
            logging.info("Indy workspace with ID %s was deleted", strWsid)
            return True
        else:
            logging.warning("An error occurred while deleting Indy workspace with ID %s, status code %i.",
                            strWsid, status)
            return False

    def urlmap(self, wsid, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset, mutator,
               patcherIds, injectedBOMs, resolve=True):
        """
        See urlmap_nocache() for method docs. This is caching version of the method.
        """
        cached = self.get_cached_urlmap(sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset,
                                        mutator, patcherIds, injectedBOMs, resolve)
        if cached:
            logging.info("Using cached version of Indy urlmap for roots %s", "-".join(gavs))
            return json.loads(cached)
        else:
            deleteWS = False

            if not wsid:
                # Generate workspace id
                wsid = "temp_%f.2" % time.time()
                deleteWS = True

            response = self.urlmap_response(wsid, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs,
                                            preset, mutator, patcherIds, injectedBOMs, resolve)
            if response != "{}":
                self.store_urlmap_cache(response, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs,
                                        preset, mutator, patcherIds, injectedBOMs, resolve)

            # cleanup
            if deleteWS:
                self.deleteWorkspace(wsid)

            return json.loads(response)

    def urlmap_nocache(self, wsid, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset,
                       mutator, patcherIds, injectedBOMs, resolve=True):
        """
        Requests creation of the urlmap. It creates the configfile, posts it to Indy server
        and process the result, which has following structure:
            {
                "projects": {
                    "group:artifact:1.0": {
                        "files": [
                            "artifact-1.0.pom",
                            "artifact-1.0.pom.md5",
                            "artifact-1.0.pom.sha1"
                        ],
                        "repoUrl": "http://maven.repo.org/repos/repo1/"
                    },
                    "group:artifact2:1.1": {
                        "files": [
                            "artifact2-1.1.pom",
                            "artifact2-1.1.pom.md5",
                            "artifact2-1.1.pom.sha1"
                            "artifact2-1.1.jar",
                            "artifact2-1.1.jar.md5",
                            "artifact2-1.1.jar.sha1"
                            "artifact2-1.1-sources.jar",
                            "artifact2-1.1-sources.jar.md5",
                            "artifact2-1.1-sources.jar.sha1"
                        ],
                        "repoUrl": "http://maven.repo.org/repos/repo1/"
                    },
                    ...
                }
            }

        :param wsid: Indy workspace ID
        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell Indy to run resolve for given roots
        :param analyze: flag that informs the API not to delete workspace, because analysis will be performed
        :returns: the requested urlmap
        """
        deleteWS = False

        if not wsid:
            # Generate workspace id
            wsid = "temp_%f.2" % time.time()
            deleteWS = True

        response = self.urlmap_response(wsid, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs,
                                        preset, mutator, patcherIds, injectedBOMs, resolve)

        # cleanup
        if deleteWS:
            self.deleteWorkspace(wsid)

        return json.loads(response)

    def urlmap_response(self, wsid, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset,
                        mutator, patcherIds, injectedBOMs, resolve=True):
        """
        Requests creation of the urlmap. It creates the configfile, posts it to Indy server
        and process the result, which has following structure:
            {
                "projects": {
                    "group:artifact:1.0": {
                        "files": [
                            "artifact-1.0.pom",
                            "artifact-1.0.pom.md5",
                            "artifact-1.0.pom.sha1"
                        ],
                        "repoUrl": "http://maven.repo.org/repos/repo1/"
                    },
                    "group:artifact2:1.1": {
                        "files": [
                            "artifact2-1.1.pom",
                            "artifact2-1.1.pom.md5",
                            "artifact2-1.1.pom.sha1"
                            "artifact2-1.1.jar",
                            "artifact2-1.1.jar.md5",
                            "artifact2-1.1.jar.sha1"
                            "artifact2-1.1-sources.jar",
                            "artifact2-1.1-sources.jar.md5",
                            "artifact2-1.1-sources.jar.sha1"
                        ],
                        "repoUrl": "http://maven.repo.org/repos/repo1/"
                    },
                    ...
                }
            }

        :param wsid: Indy workspace ID
        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell Indy to run resolve for given roots
        :returns: the response string of the requested urlmap
        """
        url = self._indy_url + self.API_PATH + "depgraph/repo/urlmap"

        request = {}
        if addclassifiers:
            if addclassifiers == Configuration.ALL_CLASSIFIERS_VALUE:
                request["extras"] = [{"classifier": "*", "type": "*"}]
            else:
                request["extras"] = addclassifiers
        request["workspaceId"] = wsid
        request["source"] = sourceKey
        if len(excludedSources):
            request["excludedSources"] = excludedSources
        if len(excludedSubgraphs):
            request["excludedSubgraphs"] = excludedSubgraphs
        request["resolve"] = resolve
        if mutator:
            request["graphComposition"] = {"graphs": [{"roots": gavs, "preset": preset, "mutator": mutator}]}
        else:
            request["graphComposition"] = {"graphs": [{"roots": gavs, "preset": preset}]}
        if len(patcherIds):
            request["patcherIds"] = patcherIds
        if injectedBOMs and len(injectedBOMs):
            request["injectedBOMs"] = injectedBOMs
        data = json.dumps(request)

        headers = {"Content-Type": "application/json"}

        logging.debug("Requesting urlmap with config '%s'", data)

        response = self._postUrl(url, data=data, headers=headers)

        if response.status == 200:
            responseContent = response.read()
            logging.debug("Indy urlmap created. Response content:\n%s", responseContent)
            return responseContent
        else:
            logging.warning("An error occurred while creating Indy urlmap, status code %i, content '%s'.",
                            response.status, response.read())
            return "{}"

    def paths(self, wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset, mutator,
              patcherIds, injectedBOMs, resolve=True):
        """
        See paths_response() for method docs. This is wrapping method to the one with caching.
        """
        cached = self.get_cached_paths(sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                                       mutator, patcherIds, injectedBOMs, resolve)
        if cached:
            logging.info("Using cached version of Indy paths for roots %s and targets %s", "-".join(roots),
                         "-".join(targets))
            return json.loads(cached)
        else:
            deleteWS = False

            if not wsid:
                # Generate workspace id
                wsid = "temp_%f.2" % time.time()
                deleteWS = True

            response = self.paths_response(wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                                           mutator, patcherIds, injectedBOMs, resolve)

            cache_file = self.store_paths_cache(response, sourceKey, roots, targets, excludedSources, excludedSubgraphs,
                                                 preset, mutator, patcherIds, injectedBOMs, resolve)

            # cleanup
            if deleteWS:
                self.deleteWorkspace(wsid)

            return json.loads(self.minimize_paths_json(cache_file))

    def paths_nocache(self, wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset, mutator,
                      patcherIds, injectedBOMs, resolve=True):
        """
        See paths_response() for method docs. This is wrapping method to the one without caching.
        """
        deleteWS = False

        if not wsid:
            # Generate workspace id
            wsid = "temp_%f.2" % time.time()
            deleteWS = True

        response = self.paths_response(wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                                       mutator, patcherIds, injectedBOMs, resolve)

        # cleanup
        if deleteWS:
            self.deleteWorkspace(wsid)

        return json.loads(self.minimize_paths_json(raw_content=response))

    def paths_response(self, wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset, mutator,
                       patcherIds, injectedBOMs, resolve=True):
        """
        Requests creation of the paths from roots to targets. It creates the configfile, posts it to Indy server
        and process the result, which has following structure:
            {
              "projects": {
                "org.apache:apache:4" : [
                  [
                    {
                      "jsonVersion" : 1,
                      "rel" : "DEPENDENCY",
                      "declaring" : "org.apache.ant:ant:1.8.0",
                      "target" : "xerces:xercesImpl:jar:2.9.0",
                      "idx" : 1,
                      "scope" : "runtime"
                    }, {
                      "jsonVersion" : 1,
                      "rel" : "PARENT",
                      "declaring" : "xerces:xercesImpl:2.9.0",
                      "target" : "org.apache:apache:4",
                      "idx" : 0
                    }
                  ]
                ],
                "org.apache:apache:3" : [
                  [
                    {
                      "jsonVersion" : 1,
                      "rel" : "DEPENDENCY",
                      "declaring" : "org.apache.ant:ant:1.8.0",
                      "target" : "xerces:xercesImpl:jar:2.9.0",
                      "idx" : 1,
                      "scope" : "runtime"
                    }, {
                      "jsonVersion" : 1,
                      "rel" : "DEPENDENCY",
                      "declaring" : "xerces:xercesImpl:2.9.0",
                      "target" : "xml-apis:xml-apis:jar:1.3.04",
                      "idx" : 0,
                      "scope" : "compile"
                    }, {
                      "jsonVersion" : 1,
                      "rel" : "PARENT",
                      "declaring" : "xml-apis:xml-apis:1.3.04",
                      "target" : "org.apache:apache:3",
                      "idx" : 0
                    }
                  ],
                  [
                    {
                      "jsonVersion" : 1,
                      "rel" : "DEPENDENCY",
                      "declaring" : "org.apache.ant:ant:1.8.0",
                      "target" : "xerces:xercesImpl:jar:2.9.0",
                      "idx" : 1,
                      "scope" : "runtime"
                    }, {
                      "jsonVersion" : 1,
                      "rel" : "DEPENDENCY",
                      "declaring" : "xerces:xercesImpl:2.9.0",
                      "target" : "xml-resolver:xml-resolver:jar:1.2",
                      "idx" : 1,
                      "scope" : "compile"
                    }, {
                      "jsonVersion" : 1,
                      "rel" : "PARENT",
                      "declaring" : "xml-resolver:xml-resolver:1.2",
                      "target" : "org.apache:apache:3",
                      "idx" : 0
                    }
                  ],
                  ...
                ]
              }
            }

        :param wsid: Indy workspace ID
        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param roots: list of root GAVs as strings
        :param targets: list of target GAVs as strings
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell Indy to run resolve for given roots
        :returns: the response string of the requested paths
        """
        url = self._indy_url + self.API_PATH + "depgraph/repo/paths"

        request = {}
        request["workspaceId"] = wsid
        request["source"] = sourceKey
        if len(excludedSources):
            request["excludedSources"] = excludedSources
        if len(excludedSubgraphs):
            request["excludedSubgraphs"] = excludedSubgraphs
        request["resolve"] = resolve
        if mutator:
            request["graphComposition"] = {"graphs": [{"roots": roots, "preset": preset, "mutator": mutator}]}
        else:
            request["graphComposition"] = {"graphs": [{"roots": roots, "preset": preset}]}
        request["targets"] = targets
        if len(patcherIds):
            request["patcherIds"] = patcherIds
        if len(injectedBOMs):
            request["injectedBOMs"] = injectedBOMs
        data = json.dumps(request)

        headers = {"Content-Type": "application/json"}

        logging.debug("Requesting paths with config '%s'", data)

        response = self._postUrl(url, data=data, headers=headers)

        if response.status == 404:
            url = self._indy_url + self.API_PATH + "depgraph/graph/paths"
            response = self._postUrl(url, data=data, headers=headers)

        if response.status == 200:
            responseContent = response.read()
            logging.debug("Indy paths created. Response content:\n%s", responseContent)
            return responseContent
        else:
            logging.warning("An error occurred while creating Indy paths, status code %i, content '%s'.",
                            response.status, response.read())
            return "{}"

    def get_cached_urlmap(self, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset, mutator,
                          patcherIds, injectedBOMs, resolve):
        """
        Gets cache urlmap response if exists for given parameters.

        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell Indy to run resolve for given roots
        :returns: the cached response or None if no cached response exists
        """
        cache_filename = self.get_urlmap_cache_filename(sourceKey, gavs, addclassifiers, excludedSources,
                                                        excludedSubgraphs, preset, mutator, patcherIds,
                                                        injectedBOMs)
        if os.path.isfile(cache_filename):
            with open(cache_filename) as cache_file:
                return cache_file.read()
        else:
            logging.info("Cache file %s not found.", cache_filename)
            return None

    def store_urlmap_cache(self, response, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset,
                           mutator, patcherIds, injectedBOMs, resolve):
        """
        Stores urlmap response to cache.

        :param response: the response to store
        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell Indy to run resolve for given roots
        :returns: the created cache filename
        """
        cache_filename = self.get_urlmap_cache_filename(sourceKey, gavs, addclassifiers, excludedSources,
                                                        excludedSubgraphs, preset, mutator, patcherIds,
                                                        injectedBOMs)
        if not os.path.exists(self.CACHE_PATH):
            os.makedirs(self.CACHE_PATH)
        with open(cache_filename, "w") as cache_file:
            cache_file.write(response)
        return cache_filename

    def get_urlmap_cache_filename(self, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset,
                                  mutator, patcherIds, injectedBOMs):
        """
        Creates a cache filename to use for urlmap request.

        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        """
        cache_filename = "%s_|_%s_|_%s_|_%s_|_%s_|_%s_|_%s_|_%s_|_%s" % ("_".join(gavs), sourceKey, addclassifiers,
                                                                         "_".join(excludedSources),
                                                                         "_".join(excludedSubgraphs), preset,
                                                                         str(mutator),
                                                                         "_".join(patcherIds), "_".join(injectedBOMs))
        if len(cache_filename) > 243:
            sha256 = hashlib.sha256(cache_filename)
            cache_filename = "%s_|_%s" % ("-".join(gavs), sha256.hexdigest())
            if len(cache_filename) > 243:
                cache_filename = sha256.hexdigest()
        return "%s/urlmap_%s.json" % (self.CACHE_PATH, cache_filename)

    def get_cached_paths(self, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset, mutator,
                         patcherIds, injectedBOMs, resolve):
        """
        Gets cache paths response if exists for given parameters.

        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param roots: list of GAV as strings
        :param targets: list of GA as strings
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell Indy to run resolve for given roots
        :returns: the cached response or None if no cached response exists
        """
        cache_filename = self.get_paths_cache_filename(sourceKey, roots, targets, excludedSources, excludedSubgraphs,
                                                       preset, mutator, patcherIds, injectedBOMs)
        if os.path.isfile(cache_filename):
            return self.minimize_paths_json(cache_filename)
        else:
            logging.info("Cache file %s not found.", cache_filename)
            return None

    def store_paths_cache(self, response, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                          mutator, patcherIds, injectedBOMs, resolve):
        """
        Stores paths response to cache.

        :param response: the response to store
        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param roots: list of GAV as strings
        :param targets: list of GA as strings
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell Indy to run resolve for given roots
        :returns: the created cache filename
        """
        cache_filename = self.get_paths_cache_filename(sourceKey, roots, targets, excludedSources, excludedSubgraphs,
                                                       preset, mutator, patcherIds, injectedBOMs)
        cache_dirname = os.path.dirname(cache_filename)
        if not os.path.exists(cache_dirname):
            os.makedirs(cache_dirname)
        with open(cache_filename, "w") as cache_file:
            cache_file.write(response)
        return cache_filename

    def get_paths_cache_filename(self, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                                 mutator, patcherIds, injectedBOMs):
        """
        Creates a cache filename to use for paths request.

        :param sourceKey: the Indy artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param roots: list of GAV as strings
        :param targets: list of GA as strings
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for Indy
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        """
        cache_filename = "%s_|_%s_|_%s_|_%s_|_%s_|_%s_|_%s_|_%s_|_%s" % ("_".join(roots), "_".join(targets), sourceKey,
                                                                         "_".join(excludedSources),
                                                                         "_".join(excludedSubgraphs), preset,
                                                                         str(mutator),
                                                                         "_".join(patcherIds), "-".join(injectedBOMs))
        if len(cache_filename) > 244:
            sha256 = hashlib.sha256(cache_filename)
            cache_filename = "%s_|_%s_|_%s" % ("_".join(roots), "_".join(targets), sha256.hexdigest())
            if len(cache_filename) > 244:
                cache_filename = sha256.hexdigest()

        root_dir = ""
        for root in roots:
            root_filename = root.replace(":", "$")
            if root_dir:
                temp = "%s_|_%s" % (root_dir, root_filename)
            else:
                temp = root_filename
            if len(temp) < 255:
                root_dir = temp
            else:
                break

        target_dir = ""
        for target in targets:
            target_groupid = re.sub(":.*", "", target)
            if target_dir:
                temp = "%s_|_%s" % (target_dir, target_groupid)
            else:
                temp = target_groupid
            if len(temp) < 255:
                target_dir = temp
            else:
                break

        return "%s/%s/%s/paths_%s.json" % (self.CACHE_PATH, root_dir, target_dir, cache_filename)

    def minimize_paths_json(self, raw_file=None, raw_content=None):
        """
        Drops all whitespace and unused fields from loaded json to allow processing of really large results.
        Sometimes we have to process a file bigger than 2GB, which is the maximum filesize for json library.
        By dropping whitespace and unused fields before processing we get around 50% of the original size.

        :param raw_file: the raw json file
        :returns: shrinked data
        """
        if not raw_file and raw_content:
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            try:
                temp_file.write(someStuff)
                temp_file.close()
                args = ["./minimize-json.sh", temp_file.name]
                minimize = Popen(args, stdout=PIPE)
                minimized = minimize.communicate()[0]
            finally:
                os.remove(temp_file.name)
        else:
            args = ["./minimize-json.sh", raw_file]
            minimize = Popen(args, stdout=PIPE)
            minimized = minimize.communicate()[0]
        return minimized
