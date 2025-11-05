#!/usr/bin/env python
# -*- coding: latin-1 -*-
"""
Setup file for some modules related tooling
"""

from vsc.install import shared_setup
from vsc.install.shared_setup import sdw

PACKAGE = {
    "version": "0.1.7",
    "author": [sdw],
    "maintainer": [sdw],
    "setup_requires": [
        "vsc-install >= 0.15.3",
    ],
    "install_requires": [
        "vsc-base >= 3.1.1",  # fix for stdin.write in Python 3
        "vsc-config",
        "vsc-utils",
        "atomicwrites",
    ],
}

if __name__ == "__main__":
    shared_setup.action_target(PACKAGE)
