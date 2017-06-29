import glob
import json
import os
import logging
import re
import sys

logger = logging.getLogger(__name__)


class Variable(object):
    dependencyRe = re.compile(r"(?<=\${)[^}]*")

    def __init__(self, name, value=""):
        self.name = name
        self.dependencies = []
        self.dependents = []
        self.path = ""
        if value:
            self.append(value)

    def __repr__(self):
        return "{}\t{}".format(self.name, self.path)

    def append(self, value):
        if isinstance(value, basestring):
            value = value.split(os.pathsep)
        values = []

        for i in value:
            varDependencies = self.listDependencies(i)
            for vd in varDependencies:
                if vd not in self.dependencies:
                    self.dependencies.append(vd)
            if os.path.isfile(i) or os.path.isdir(i):
                i = os.path.normpath(os.path.realpath(i))
            values.append(i)
        self.path = os.pathsep.join(list(set([i for i in values if i])))

    def extend(self, value):
        for i in value:
            self.append(i)

    def listDependencies(self, value):
        matched = Variable.dependencyRe.findall(value)

        if matched:
            return list(set(matched))
        return []

    def hasValue(self):
        return len(self.path) > 0

    def solve(self, **kwargs):
        failed = []
        newStr = self.path
        for depend in self.dependencies:
            if depend not in kwargs:
                failed.append(depend)
                continue
            newStr = newStr.replace("${" + depend + "}", kwargs[depend])
        if failed:
            logger.warning("Failed to find tokens: {} for {}\n{}".format(",".join(failed), self.name, (kwargs,
                                                                                                       self.dependencies)))

        self.path = newStr

        return newStr


class Package(object):
    def __init__(self, filename, environ=None):
        self.filename = filename
        self.environment = environ

        with open(filename) as f:
            self.data = json.load(f)
        self.name = self.data["name"]
        self.version = self.data["version"]
        self.platforms = self.data["platforms"]
        self.requirements = self.data["requirements"]
        self.hasTests = self.data.get("tests", False)
        self.variables = self.data.get("environment", {})
        self.path = Variable(self.name, self.data.get("path", ""))

    def __repr__(self):
        return "{}\t{}".format(".".join([self.name, self.version]), self.path.path)


class Environment(object):
    packageFiles = "*.env"

    def __init__(self, requests=None):
        requests = requests or []
        self.requested = set(requests)
        self.variables = {}
        self.packages = {}
        self.definedVariables = []

    def initialize(self):
        packageFiles = "*.env"
        envLocation = os.environ["RESOLVER_ENV"]
        if envLocation:
            packageFiles = envLocation + os.altsep + packageFiles
        possibles = [Package(filename, self) for filename in glob.glob(packageFiles)]
        count = 0
        if not possibles:
            raise ValueError("Failed to find possible package files in directory {}".format(packageFiles))
        while self.requested or count < 256:
            for package in possibles:
                if package.name in self.requested:
                    self.requested.remove(package.name)
                    if package.name in self.packages:
                        continue
                    self.packages[package.name] = package
                    self.requested = self.requested | set([i for i in package.requirements if i not in self.packages])
            count += 1

        if self.requested:
            logger.error("Couldn't find requested packages: {}".format(",".join(self.requested)))

        for package in self.packages.values():
            self.processPackageVariables(package)
        extDependencies = []
        for name, var in self.variables.items():
            for dep in var.dependencies:
                if dep in self.variables:
                    self.variables[dep].dependents.append(name)
                    continue
                if dep not in extDependencies:
                    extDependencies.append(dep)
        missing = set([dep for dep in extDependencies if not os.environ.get(dep)])
        if missing:
            logger.warning("missing dependencies, {}".format(",".join(missing)))
        for p in self.packages.values():
            self._solve(p.path)
            logger.info("{}".format(p))

    def processPackageVariables(self, package):
        for k, value in package.variables.items():
            if k not in self.variables:
                self.variables[k] = Variable(k, value)
                continue
            self.variables[k].append(value)

    def _solve(self, variable):
        path = variable.path
        if not path:
            return None
        dependencies = re.findall(Variable.dependencyRe, path)
        if dependencies:
            kws = dict(os.environ)
            for i in dependencies:
                envP = os.environ.get(i)
                if envP:
                    newPath = self._solve(Variable(name=i, value=envP))
                elif i in self.variables:
                    newPath = self._solve(self.variables[i])
                else:
                    logger.warning("Could not solve dependency because it doesnt exist: {}".format(i))
                    continue
                kws[i] = newPath
            if kws:
                variable.solve(**kws)
        return variable.path

    def solve(self, setEnvironment=True):
        """Process the environment but doesn't set anything, itll however set the cache on the this instance
        """

        for name, var in self.variables.items():
            path = self._solve(var)

            if setEnvironment:
                os.environ[name] = path
                if name == "PYTHONPATH":
                    for i in path.split(os.pathsep):
                        if i not in sys.path:
                            sys.path.append(os.path.normpath(i))


def packages():
    packageFiles = "*.env"
    envLocation = os.environ["RESOLVER_ENV"]

    if envLocation:
        packageFiles = os.path.join(envLocation, packageFiles)
    possibles = [Package(filename, None) for filename in glob.glob(packageFiles)]
    return [i.name for i in possibles]
