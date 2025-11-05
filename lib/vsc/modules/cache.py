#
# Copyright 2019-2025 Ghent University
#
# This file is part of vsc-modules,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://www.vscentrum.be),
# the Flemish Research Foundation (FWO) (http://www.fwo.be/en)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# https://github.com/hpcugent/vsc-modules
#
# vsc-modules is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# vsc-modules is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with vsc-modules.  If not, see <http://www.gnu.org/licenses/>.
#
"""
Interaction with Lmod lua cache and JSON conversion
"""

import os
import json
import re
import logging

from atomicwrites import atomic_write
from vsc.utils.run import run, RunNoShellAsyncLoop
from distutils.version import LooseVersion
from vsc.config.base import CLUSTER_DATA, MODULEROOT

LMOD_CONFIG = "/etc/lmodrc.lua"

CACHEFILENAME = "spiderT.lua"

JSON_MODULEMAP_FILENAME = "modulemap.json"

# this version key is the one holding the default version map
DEFAULTKEY = ".default"

MAIN_CLUSTERS_KEY = "clusters"
MAIN_SOFTWARE_KEY = "software"

# very dumb way to deal with difficulty to get utf8 safe data out of
# lua using print. issue might also be with run.async
SIMPLE_UTF_FIX_REGEX = re.compile(r"\\x")


class SoftwareVersion(LooseVersion):
    """Support even weirder non-sensical version schemes"""

    component_re = re.compile(r"(v?\d+ | [a-z]+ | \.)", re.VERBOSE)

    def parse(self, vstring):
        self.vstring = vstring
        components = [x for x in self.component_re.split(vstring) if x and x != "."]
        for i, obj in enumerate(components):
            try:
                # zfill and compare strings, to deal with mixed int/string versions
                #   64 should be enough for everyone etc
                #   negative versions are not properly supported anyway
                components[i] = f"{int(obj.lstrip('v')):064}"
            except ValueError:
                pass

        self.version = components

    def _cmp(self, other):
        try:
            return super()._cmp(other)
        except Exception as e:
            logging.exception(
                "Failed to compare %s (%s) with other %s (%s): %s", self, self.version, other, other.version, e
            )
            raise


def log_and_raise(msg):
    """log to error log and raise exception."""
    logging.error(msg)
    raise Exception(msg)


def run_cache_create():
    """Run the script to create the Lmod cache"""
    lmod_dir = os.environ.get("LMOD_DIR", None)
    if not lmod_dir:
        logging.error("Cannot find $LMOD_DIR in the environment.")
        raise RuntimeError("Cannot find $LMOD_DIR in the environment.")

    return run([os.path.join(lmod_dir, "update_lmod_system_cache_files"), MODULEROOT])


def get_lua_via_json(filename, tablenames):
    """Dump tables from lua data in filename to json as string. Return as list"""
    if not os.path.isfile(filename):
        log_and_raise("No valid file %s found", filename)

    tabledata = ",".join([f"['{x}']={x}" for x in tablenames])
    luatemplate = "json=require('json');dofile('%s');print(json.encode({%s}))"
    luacmd = luatemplate % (filename, tabledata)
    # default asyncloop.run is slow: if the output is very big, code reads in 1k chunks
    #   so use larger readsize
    arun = RunNoShellAsyncLoop(["lua", "-"], input=luacmd)
    arun.readsize = 1024**2
    ec, out = arun._run()
    if ec:
        log_and_raise(f'Lua export to json using "{luacmd}" failed: {out}')

    safe_out = SIMPLE_UTF_FIX_REGEX.sub("_____", out)
    data = json.loads(safe_out)

    return [data[x] for x in tablenames]


def get_lmod_conf():
    """Return Lmod config as dict"""
    # only one element, and it is a single-element list
    return get_lua_via_json(LMOD_CONFIG, ["scDescriptT"])[0][0]


def get_lmod_cache(cachefile):
    """Return Lmod lua cache as list of modulepaths and spider data"""
    return get_lua_via_json(cachefile, ["mpathMapT", "spiderT"])


def cluster_map(mpathMapT):
    """Return cluster -> cluster module and modulepath -> [cluster1, ...] mappings"""
    # map cluster to cluster module
    clustermap = {}
    # map modulepath to list of clusters
    modulepathmap = {}
    for mpath, data in mpathMapT.items():
        for clmod in [x for x in data.keys() if x.startswith("cluster/") or x.startswith("env/software/")]:
            all_parts = clmod.split("/")
            # in older versions of cluster-modules, the paths are set via cluster module itelf
            #   as of v2, they are set via env/software module
            if all_parts[0] == "cluster":
                start = 1
            else:
                start = 2
            parts = all_parts[start:]

            # also handle hidden cluster modules, incl hidden partitions
            #   (starting with . to indicate they are hidden)
            clustername = parts[0].lstrip(".")
            if len(parts) == 2:
                partition = parts[1].lstrip(".")
                cluster = f"{clustername}/{partition}"
                if clustername in clustermap:
                    log_and_raise(f"Found existing cluster module {clustername} for cluster/partition {partition}")
            else:
                cluster = clustername
                partitions = [x for x in clustermap.keys() if x.startswith(clustername + "/")]
                if partitions:
                    log_and_raise(f"Found existing partitions {partitions} for same cluster {clustername}")

            # recreate the cluster module to support env/software
            clmod_cl = "/".join(["cluster"] + parts)
            tmpclmod = clustermap.setdefault(cluster, clmod_cl)
            if tmpclmod != clmod_cl:
                log_and_raise(
                    f"Found 2 different cluster modules {tmpclmod} and {clmod_cl}"
                    f" for same cluster {cluster} (clmod {clmod})"
                )
            mpclusters = modulepathmap.setdefault(mpath, [])
            if cluster not in mpclusters:
                mpclusters.append(cluster)
            modulepathmap[mpath] = sorted(mpclusters)

    logging.debug("Generated clustermap %s", clustermap)
    logging.debug("Generated modulepathmap %s", modulepathmap)
    return clustermap, modulepathmap


def sort_modulepaths(spiderT, mpmap):
    """Return a sorted list of modulepaths"""
    modulepaths = []
    for mpath in spiderT.keys():
        if not mpath.startswith("/"):
            logging.debug("Skipping spiderT key %s", mpath)
            continue

        if mpath in mpmap:
            modulepaths.append(mpath)
        else:
            logging.debug("Skipping modulepath %s not in modulepath map %s", mpath, mpmap)

    modulepaths.sort()
    logging.debug("Found pre-sorted modulepaths %s", modulepaths)

    # sort them
    #   very trivial sort based on EXTRA_MODULEPATHS from CLUSTER_DATA
    #   we do not assume complicated nesting
    #   just search all EXTRA_MODULEPATHS last
    #      they are listed in prepend order, but default/most sensible one is prepended last
    #      so they are reversed: the first extra has to be moved as far as possible
    for extras in [x.get("EXTRA_MODULEPATHS", [])[::-1] for x in CLUSTER_DATA.values()]:
        for extra in extras:
            # move to the end (if present)
            if extra in modulepaths:
                logging.debug("Moving EXTRA_MODULEPATH %s to the end", extra)
                modulepaths.remove(extra)
                modulepaths.append(extra)

    logging.debug("Sorted modulepaths %s", modulepaths)
    return modulepaths


def sort_recent_versions(versions):
    """Sort versions using LooseVersion, most recent first"""
    try:
        return sorted(versions, key=SoftwareVersion, reverse=True)
    except TypeError:
        logging.exception("Failed to compare versions %s", versions)
        raise


def software_map(spiderT, mpmap):
    """Create a software map with what software has which versions on which cluster"""
    modulepaths = sort_modulepaths(spiderT, mpmap)

    softmap = {}
    for mpath in modulepaths:
        logging.debug("Processing modulepath %s", mpath)
        clusters = mpmap[mpath]
        for name, namedata in spiderT[mpath].items():
            soft = softmap.setdefault(name, {})
            # all versions of this software in current modulepath
            mpversions = []
            for fullname, fulldata in namedata["fileT"].items():
                version = fulldata["Version"]
                # sanity check
                txt = f"for modulepath {mpath} name {name} fullname {fullname}: {fulldata}"
                if version != fulldata["canonical"]:
                    log_and_raise("Version != canonical " + txt)
                if fullname != f"{name}/{version}":
                    log_and_raise("fullname != name/version " + txt)

                mpversions.append(version)
                softversion = soft.setdefault(version, [])
                soft[version] = sorted(softversion + clusters)

            # determine default
            #   the default is per clusters (actually per modulepath)
            default = None
            defaultdata = namedata["defaultT"]

            if defaultdata:
                value = defaultdata["value"]
                if value:
                    if value.startswith(name + "/"):
                        # default has full name, we only need the versions
                        default = value[len(name) + 1 :]
                    else:
                        default = value

                    if default not in soft:
                        log_and_raise(
                            f"Default value {default} found for {name} modulepath {mpmap} "
                            f"but not matching entry: {defaultdata}"
                        )
                else:
                    # see https://easybuild.readthedocs.io/en/latest/Wrapping_dependencies.html
                    logging.debug("Default without value found for %s modulepath %s: %s", name, mpath, defaultdata)

            if not default:
                default = sort_recent_versions(mpversions)[0]

            # track the default
            #   the keys also can be used as list of all clusters that have the software
            softdefault = soft.setdefault(DEFAULTKEY, {})
            for cluster in clusters:
                tmpdefault = softdefault.setdefault(cluster, default)
                if tmpdefault != default:
                    # typically due to modulepath ordering
                    logging.debug(
                        "Already found default for %s for cluster %s: found %s, new %s",
                        name,
                        cluster,
                        tmpdefault,
                        default,
                    )
    return softmap


def get_json_filename():
    """Return the filename of the JSON data"""
    config = get_lmod_conf()
    return os.path.join(config["dir"], JSON_MODULEMAP_FILENAME)


def write_json(clustermap, softmap, filename=None):
    """Write JSON with cluster and software map"""
    if filename is None:
        filename = get_json_filename()

    with atomic_write(filename, overwrite=True) as outfile:
        json.dump(
            {
                MAIN_CLUSTERS_KEY: clustermap,
                MAIN_SOFTWARE_KEY: softmap,
            },
            outfile,
        )
        logging.debug("Wrote %s", filename)

    os.chmod(filename, 0o644)


def read_json(filename=None):
    """Read JSON and return cluster and software map"""
    if filename is None:
        filename = get_json_filename()
    with open(filename, encoding="utf8") as outfile:
        data = json.load(outfile)
        logging.debug("Read %s", filename)

    return data[MAIN_CLUSTERS_KEY], data[MAIN_SOFTWARE_KEY]


def software_cluster_view(softmap=None):
    """
    Return a dict with list of software versions per cluster.
    First version is the default, the remainder is sorted
    """
    if softmap is None:
        _, softmap = read_json()

    clview = {}
    for name, vdata in softmap.items():
        defaults = vdata.pop(DEFAULTKEY)

        # the keys have all clusters that have software name
        for cluster in defaults.keys():
            # create nested structure cluster -> name -> versions
            clsoft = clview.setdefault(cluster, {})
            clsoft.setdefault(name, [])

        for version, clusters in vdata.items():
            for cluster in clusters:
                clview[cluster][name].append(version)

        for cluster, default in defaults.items():
            versions = sort_recent_versions(clview[cluster][name])
            clview[cluster][name] = versions
            try:
                versions.remove(default)
            except ValueError as err:
                logging.exception(
                    "Unable to remove default %s from versions %s for %s cluster %s: %s",
                    default,
                    versions,
                    name,
                    cluster,
                    err,
                )
                raise err
            versions.insert(0, default)  # default first

    return clview


def make_stats(clustermap, softmap):
    names = 0
    stats = {f"modules_{cl}": 0 for cl in clustermap.keys()}
    for moddata in softmap.values():
        names += 1
        for version, clusters in moddata.items():
            if version == DEFAULTKEY:
                continue
            for cl in clusters:
                stats[f"modules_{cl}"] += 1
    stats["total_modules"] = sum(stats.values())
    stats["total_names"] = names
    stats["clusters"] = len(clustermap)

    return stats


def convert_lmod_cache_to_json():
    """Main conversion of Lmod lua cache to cluster and software mapping in JSON"""
    cachefile = os.path.join(get_lmod_conf()["dir"], CACHEFILENAME)

    mpathMapT, spiderT = get_lmod_cache(cachefile)

    clustermap, mpmap = cluster_map(mpathMapT)
    softmap = software_map(spiderT, mpmap)
    write_json(clustermap, softmap)

    return make_stats(clustermap, softmap)
