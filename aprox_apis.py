from maven_repo_util import slashAtTheEnd

import hashlib
import httplib
import json
import logging
import os
import re
import urllib
import urlparse
from configuration import Configuration


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


class AproxApi(UrlRequester):
    """
    Class allowing to communicate with the AProx REST API.
    """

    API_PATH = "api/"
    CACHE_PATH = "cache"

    def __init__(self, aprox_url):
        self._aprox_url = slashAtTheEnd(aprox_url)

    def createWorkspace(self):
        """
        Creates new workspace. Example of returned object:
            {
                "config": {
                    "forceVersions": true
                },
                "selectedVersions": {},
                "wildcardSelectedVersions": {},
                "id": "1",
                "open": true,
                "lastAccess": 1377090886682
            }

        :returns: created workspace structure as returned by AProx
        """
        url = self._aprox_url + self.API_PATH + "depgraph/ws/new"
        logging.info("Creating new AProx workspace")
        response = self._postUrl(url)
        if response.status == 201:
            responseJson = json.loads(response.read())
            logging.info("Created AProx workspace with ID %s", responseJson["id"])
            return responseJson
        else:
            raise Exception("Failed to create new AProx workspace, status code %i, content: %s"
                            % (response.status, response.read()))

    def deleteWorkspace(self, wsid):
        """
        Deletes a specified workspace.

        :param wsid: workspace ID
        :returns: True if the workspace was deleted, False otherwise
        """
        strWsid = str(wsid)
        url = (self._aprox_url + self.API_PATH + "depgraph/ws/%s") % strWsid
        logging.info("Deleting AProx workspace with ID %s", strWsid)
        status = self._deleteUrl(url)
        if status == 200:
            logging.info("AProx workspace with ID %s was deleted", strWsid)
            return True
        else:
            logging.warning("An error occurred while deleting AProx workspace with ID %s, status code %i.",
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
            logging.info("Using cached version of AProx urlmap for roots %s", "-".join(gavs))
            return json.loads(cached)
        else:
            deleteWS = False

            if not wsid:
                # Create workspace
                ws = self.createWorkspace()
                wsid = ws["id"]
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
        Requests creation of the urlmap. It creates the configfile, posts it to AProx server
        and process the result, which has following structure:
            {
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

        :param wsid: AProx workspace ID
        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell AProx to run resolve for given roots
        :param analyze: flag that informs the API not to delete workspace, because analysis will be performed
        :returns: the requested urlmap
        """
        deleteWS = False

        if not wsid:
            # Create workspace
            ws = self.createWorkspace()
            wsid = ws["id"]
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
        Requests creation of the urlmap. It creates the configfile, posts it to AProx server
        and process the result, which has following structure:
            {
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

        :param wsid: AProx workspace ID
        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell AProx to run resolve for given roots
        :returns: the response string of the requested urlmap
        """
        url = self._aprox_url + self.API_PATH + "depgraph/repo/urlmap"

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
            logging.debug("AProx urlmap created. Response content:\n%s", responseContent)
            return responseContent
        else:
            logging.warning("An error occurred while creating AProx urlmap, status code %i, content '%s'.",
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
            logging.info("Using cached version of AProx paths for roots %s and targets %s", "-".join(roots),
                         "-".join(targets))
            return json.loads(cached)
        else:
            deleteWS = False

            if not wsid:
                # Create workspace
                ws = self.createWorkspace()
                wsid = ws["id"]
                deleteWS = True

            response = self.paths_response(wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                                           mutator, patcherIds, injectedBOMs, resolve)

            self.store_paths_cache(response, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                                   mutator, patcherIds, injectedBOMs, resolve)

            # cleanup
            if deleteWS:
                self.deleteWorkspace(wsid)

        return json.loads(response)

    def paths_nocache(self, wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset, mutator,
                      patcherIds, injectedBOMs, resolve=True):
        """
        See paths_response() for method docs. This is wrapping method to the one without caching.
        """
        deleteWS = False

        if not wsid:
            # Create workspace
            ws = self.createWorkspace()
            wsid = ws["id"]
            deleteWS = True

        response = self.paths_response(wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                                       mutator, patcherIds, injectedBOMs, resolve)

        # cleanup
        if deleteWS:
            self.deleteWorkspace(wsid)

        return json.loads(response)

    def paths_response(self, wsid, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset, mutator,
                       patcherIds, injectedBOMs, resolve=True):
        """
        Requests creation of the paths from roots to targets. It creates the configfile, posts it to AProx server
        and process the result, which has following structure:
            {
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

        :param wsid: AProx workspace ID
        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param roots: list of root GAVs as strings
        :param targets: list of target GAVs as strings
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell AProx to run resolve for given roots
        :returns: the response string of the requested paths
        """
        url = self._aprox_url + self.API_PATH + "depgraph/repo/paths"

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

        if response.status == 200:
            responseContent = response.read()
            logging.debug("AProx paths created. Response content:\n%s", responseContent)
            return responseContent
        else:
            logging.warning("An error occurred while creating AProx paths, status code %i, content '%s'.",
                            response.status, response.read())
            return "{}"

    def get_cached_urlmap(self, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset, mutator,
                          patcherIds, injectedBOMs, resolve):
        """
        Gets cache urlmap response if exists for given parameters.

        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell AProx to run resolve for given roots
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
        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell AProx to run resolve for given roots
        """
        cache_filename = self.get_urlmap_cache_filename(sourceKey, gavs, addclassifiers, excludedSources,
                                                        excludedSubgraphs, preset, mutator, patcherIds,
                                                        injectedBOMs)
        if not os.path.exists(self.CACHE_PATH):
            os.makedirs(self.CACHE_PATH)
        with open(cache_filename, "w") as cache_file:
            cache_file.write(response)

    def get_urlmap_cache_filename(self, sourceKey, gavs, addclassifiers, excludedSources, excludedSubgraphs, preset,
                                  mutator, patcherIds, injectedBOMs):
        """
        Creates a cache filename to use for urlmap request.

        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param gavs: list of GAV as strings
        :param addclassifiers: list of dictionaries with structure {"type": "<type>", "classifier": "<classifier>"}, any
                               value can be replaced by a star to include all types/classifiers
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
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

        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param roots: list of GAV as strings
        :param targets: list of GA as strings
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell AProx to run resolve for given roots
        :returns: the cached response or None if no cached response exists
        """
        cache_filename = self.get_paths_cache_filename(sourceKey, roots, targets, excludedSources, excludedSubgraphs,
                                                       preset, mutator, patcherIds, injectedBOMs)
        if os.path.isfile(cache_filename):
            with open(cache_filename) as cache_file:
                return cache_file.read()
        else:
            logging.info("Cache file %s not found.", cache_filename)
            return None

    def store_paths_cache(self, response, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                          mutator, patcherIds, injectedBOMs, resolve):
        """
        Stores paths response to cache.

        :param response: the response to store
        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param roots: list of GAV as strings
        :param targets: list of GA as strings
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
        :param injectedBOMs: list of injected BOMs used with dependency management injection
                             Maven extension
        :param resolve: flag to tell AProx to run resolve for given roots
        """
        cache_filename = self.get_paths_cache_filename(sourceKey, roots, targets, excludedSources, excludedSubgraphs,
                                                       preset, mutator, patcherIds, injectedBOMs)
        cache_dirname = os.path.dirname(cache_filename)
        if not os.path.exists(cache_dirname):
            os.makedirs(cache_dirname)
        with open(cache_filename, "w") as cache_file:
            cache_file.write(response)

    def get_paths_cache_filename(self, sourceKey, roots, targets, excludedSources, excludedSubgraphs, preset,
                                 mutator, patcherIds, injectedBOMs):
        """
        Creates a cache filename to use for paths request.

        :param sourceKey: the AProx artifact source key, consisting of the source type and
                          its name of the form <{repository|deploy|group}:<name>>
        :param roots: list of GAV as strings
        :param targets: list of GA as strings
        :param excludedSources: list of excluded sources' keys
        :param excludedSubgraphs: list of artifacts' GAVs which we want to exclude along with their subgraphs
        :param preset: preset used while creating the urlmap
        :param patcherIds: list of patcher ID strings for AProx
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
