
"""maven_artifact.py Python code representing a Maven artifact"""

import logging
import sys


class MavenArtifact:

    """
    Suffix of a snapshot version which should be used to construct filenames instead of version
    with "-SNAPSHOT" suffix.
    """
    snapshotVersionSuffix = None

    gav_cache = dict()

    def __init__(self, groupId, artifactId, artifactType, version, classifier=''):
        self.groupId = groupId
        self.artifactId = artifactId
        self.artifactType = artifactType
        self.version = version
        self.classifier = classifier

    @staticmethod
    def createFromGAV(gav):
        """
        Initialize an artifact using a colon separated
        GAV of the form groupId:artifactId:[type:][classifier:]version[:scope]

        :returns: MavenArtifact instance
        """
        if gav in MavenArtifact.gav_cache:
            return MavenArtifact.gav_cache[gav]

        gavParts = gav.split(':')
        if len(gavParts) not in [3, 4, 5, 6]:
            logging.error("Invalid GAV string: %s", gav)
            sys.exit(1)
        groupId = gavParts[0]
        artifactId = gavParts[1]

        scopes = ["compile", "test", "provided", "runtime", "system", "import"]
        if gavParts[-1] in scopes:
            effectiveParts = len(gavParts) - 1
        else:
            effectiveParts = len(gavParts)

        artifactType = ''
        classifier = ''
        if effectiveParts == 3:
            version = gavParts[2]
        else:
            artifactType = gavParts[2]
            if effectiveParts == 4:
                version = gavParts[3]
            else:
                classifier = gavParts[3]
                version = gavParts[4]

        result = MavenArtifact(groupId, artifactId, artifactType, version, classifier)

        MavenArtifact.gav_cache[gav] = result

        return result

    @staticmethod
    def createFromPomPath(path):
        """
        Initialize an artifact using a relative pom filepath from a repository root dir.

        :returns: MavenArtifact instance
        """
        path_parts = path.split('/')
        if len(path_parts) < 4:
            logging.error("Invalid POM path: %s", path)
            sys.exit(1)
        groupid = ".".join(path_parts[0:-3])
        artifactid = path_parts[-3]
        version = path_parts[-2]

        ma = MavenArtifact(groupid, artifactid, None, version)

        if ma.getPomFilename() != path_parts[-1]:
            raise "Found POM filename %s does not match the one generated from the path %s" % (path_parts[-1],
                                                                                               ma.getPomFilename())

        return ma

    def getArtifactType(self):
        return self.artifactType

    def getClassifier(self):
        return self.classifier

    def getDirPath(self):
        """Get the relative repository path to the GAV."""
        relativePath = self.getArtifactDirPath()
        relativePath += self.version + '/'
        return relativePath

    def getArtifactDirPath(self):
        """Get the relative repository path to the artifact (groupId + artifactId)."""
        relativePath = self.groupId.replace('.', '/') + '/'
        relativePath += self.artifactId + '/'
        return relativePath

    def getGA(self):
        """Get the groupId and artifactId using a colon separated form."""
        return self.groupId + ":" + self.artifactId

    def getGAT(self):
        """Get the groupId, artifactId and artifact type using a colon separated form."""
        return self.groupId + ":" + self.artifactId + ":" + self.artifactType

    def getGAV(self):
        """Get the groupId, artifactId and version using a colon separated form."""
        return self.groupId + ":" + self.artifactId + ":" + self.version

    def getGATCV(self):
        """Get the groupId, artifactId, optional type, optional classifier and version using a colon separated form."""
        result = self.groupId + ':' + self.artifactId
        if self.artifactType:
            result += ':' + self.artifactType
        if self.classifier:
            result += ':' + self.classifier
        result += ':' + self.version
        return result

    def getBaseFilename(self):
        """Returns the filename without the file extension"""
        if self.snapshotVersionSuffix:
            baseFilename = self.artifactId + '-' \
                + self.version.replace("-SNAPSHOT", self.snapshotVersionSuffix)
        else:
            baseFilename = self.artifactId + '-' + self.version
        return baseFilename

    def getArtifactFilename(self):
        """Returns the filename of the artifact"""
        if (self.classifier):
            return self.getBaseFilename() + '-' + self.classifier + '.' + self.artifactType
        else:
            return self.getBaseFilename() + '.' + self.artifactType

    def getArtifactFilepath(self):
        """Return the path to the artifact file"""
        return self.getDirPath() + self.getArtifactFilename()

    def getPomFilename(self):
        """Returns the filename of the pom file for this artifact"""
        return self.getBaseFilename() + '.pom'

    def getPomFilepath(self):
        """Return the path to the artifact file"""
        return self.getDirPath() + self.getPomFilename()

    def getSourcesFilename(self):
        """Returns the filename of the sources artifact"""
        return self.getClassifierFilename('sources')

    def getSourcesFilepath(self):
        """Return the path to the artifact file"""
        return self.getDirPath() + self.getSourcesFilename()

    def getClassifierFilename(self, classifier, artifactType="jar"):
        """Return the filename to the artifact's classifier file"""
        return self.getBaseFilename() + '-' + classifier + '.' + artifactType

    def getClassifierFilepath(self, classifier, artifactType="jar"):
        """Return teh path to the artifact's classifier file"""
        return self.getDirPath() + self.getClassifierFilename(classifier, artifactType)

    def is_example(self):
        gav = self.getGAV()
        return "example" in gav or "quickstart" in gav or "demo" in gav

    def isSnapshot(self):
        """Determines if the version of this artifact is a snapshot version."""
        return self.version.endswith("-SNAPSHOT")

    def __str__(self):
        return self.getGATCV()

    def __repr__(self):
        return "MavenArtifact(%s, %s, %s, %s, %s)" % (repr(self.groupId), repr(self.artifactId),
                repr(self.artifactType), repr(self.version), repr(self.classifier))

    def __eq__(self, other):
        return other is not None and repr(self) == repr(other)

    def __hash__(self):
        return hash(repr(self))

    def __cmp__(self, other):
        if other is None:
            return 1
        else:
            result = cmp(self.getGAV(), other.getGAV())
            if result == 0:
                result = cmp(self.getGATCV(), other.getGATCV())
            return result
