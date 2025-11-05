#!/usr/bin/env python
#
# Copyright 2016-2024 Ghent University
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
This script runs the Lmod cache creation script and reports to nagios/icinga the exit status.
It also can check if the age of the current age and will report if it's too old.

@author: Ward Poelmans (Ghent University)
"""

import logging
import os
import time
from vsc.utils.script_tools import ExtendedSimpleOption

from vsc.modules.cache import run_cache_create, convert_lmod_cache_to_json, get_lmod_conf

NAGIOS_CHECK_INTERVAL_THRESHOLD = 2 * 60 * 60  # 2 hours


def main():
    """
    Set the options and initiates the main run.
    Returns the errors if any in a nagios/icinga friendly way.
    """
    options = {
        "nagios-check-interval-threshold": NAGIOS_CHECK_INTERVAL_THRESHOLD,
        "create-cache": ("Create the Lmod cache", None, "store_true", False),
        "freshness-threshold": (
            "The interval in minutes for how long we consider the cache to be fresh",
            "int",
            "store",
            120,
        ),
    }
    opts = ExtendedSimpleOption(options)

    try:
        if opts.options.create_cache:
            opts.log.info("Updating the Lmod cache")
            exitcode, msg = run_cache_create()
            if exitcode != 0:
                opts.log.error("Lmod cache update failed: %s", msg)
                opts.critical("Lmod cache update failed")

            try:
                stats = convert_lmod_cache_to_json()
                logging.info("Got %s clusters and %s total modules", stats["clusters"], stats["total_modules"])
                opts.thresholds = stats
            except Exception as err:
                opts.log.exception("Lmod to JSON failed: %s", err)
                opts.critical("Lmod to JSON failed.")

        opts.log.info("Checking the Lmod cache freshness")
        timestamp = os.stat(get_lmod_conf()["timestamp"])

        # give a warning when the cache is older then --freshness-threshold
        if (time.time() - timestamp.st_mtime) > opts.options.freshness_threshold * 60:
            errmsg = "Lmod cache is not fresh"
            opts.log.warn(errmsg)
            opts.warning(errmsg)

    except RuntimeError as err:
        opts.log.exception("Failed to update Lmod cache: %s", err)
        opts.critical("Failed to update Lmod cache. See logs.")
    except Exception as err:  # pylint: disable=W0703
        opts.log.exception("critical exception caught: %s", err)
        opts.critical("Script failed because of uncaught exception. See logs.")

    if opts.options.create_cache:
        opts.epilogue("Lmod cache updated.", opts.thresholds)
    else:
        opts.epilogue("Lmod cache is still fresh.")


if __name__ == "__main__":
    main()
