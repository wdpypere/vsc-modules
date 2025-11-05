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
'''
cache tests
'''
import json
import os
import shutil
import tempfile
from vsc.install.testing import TestCase
from vsc.modules import cache
from vsc.modules.cache import (
    CACHEFILENAME,
    cluster_map, software_map,
    get_lmod_conf, get_lmod_cache,
    get_json_filename, write_json, read_json,
    software_cluster_view, sort_recent_versions,
    make_stats,
    )

import logging
logging.basicConfig(level=logging.DEBUG)


class CacheTest(TestCase):
    def setUp(self):
        """Prepare to run test"""

        super().setUp()

        self.topdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # monkeypatch
        cache.LMOD_CONFIG = './test/data/lmodrc.lua'

    def test_lmod_conf(self):
        self.assertEqual(get_lmod_conf(), {
            'timestamp': '/apps/gent/lmodcache/timestamp',
            'dir': './test/data',
        })

    def test_versions(self):
        data = [
            [
                ('v2106-foss-2021a-highopt', '8-foss-2020b-20210316', '8-intel-2020b', '8-foss-2020b', 'v2106-foss-2021a', '9-foss-2021a'),
                ('v2106-foss-2021a-highopt', 'v2106-foss-2021a', '9-foss-2021a', '8-intel-2020b', '8-foss-2020b-20210316', '8-foss-2020b'),
            ],
            [
                # when split, it compares 2 (of 2.25) with 'binutils' at same position in self.version list
                ('1.2.8-GCC-4.9.3-2.25', '1.2.8-GCC-4.9.3-binutils-2.25'),
                ('1.2.8-GCC-4.9.3-binutils-2.25', '1.2.8-GCC-4.9.3-2.25'),
            ],

        ]
        for unsorted_v, sorted_v in data:
            self.assertEqual(tuple(sort_recent_versions(unsorted_v)), sorted_v)

    def test_read_write_json(self):
        """Test reading and writing the json data"""
        self.assertEqual(get_json_filename(), './test/data/modulemap.json')

        jsonfilename = os.path.join(self.tmpdir, 'some.json')
        clustermap = {'a': 'b'}
        softmap = {'g': 'h'}
        write_json(clustermap, softmap, filename=jsonfilename)
        self.assertEqual(json.loads(open(jsonfilename).read()), {'clusters': clustermap, 'software': softmap})
        self.assertEqual(read_json(filename=jsonfilename), (clustermap, softmap))
        self.assertEqual(os.stat(jsonfilename).st_mode & 0o777, 0o644)

    def test_cluster_map(self):
        """Test cluster map"""
        mpathMapT = {
            '/abc' : {
                'mod1' : {
                    'data': 'mod1',
                },
                'cluster/cluster1' : {
                    'data': 'cluster1',
                },
                'cluster/cluster2/.part1' : {
                    'data': 'cluster2 hidden part1',
                },
                'cluster/cluster2/part2' : {
                    'data': 'cluster2 part2',
                },
            },
            '/def' : {
                'cluster/.cluster3' : {
                    'data': 'hidden cluster3',
                },
            }
        }

        clustermap, modulepathmap = cluster_map(mpathMapT)

        self.assertEqual(clustermap, {
            'cluster1': 'cluster/cluster1',
            'cluster2/part1': 'cluster/cluster2/.part1',
            'cluster2/part2': 'cluster/cluster2/part2',
            'cluster3': 'cluster/.cluster3',
        })
        self.assertEqual(modulepathmap, {
            '/abc': ['cluster1', 'cluster2/part1', 'cluster2/part2'],
            '/def': ['cluster3'],
        })

    def test_make_json(self):
        """More or less the code from convert_lmod_cache_to_json"""
        cachefile = os.path.join(get_lmod_conf()['dir'], CACHEFILENAME)
        self.assertEqual(cachefile, './test/data/spiderT.lua')

        mpathMapT, spiderT = get_lmod_cache(cachefile)

        mpath = '/apps/gent/CO7/cascadelake-volta-ib-PILOT/modules/all'
        name = 'Autoconf'
        module = 'Autoconf/2.69-GCCcore-8.2.0'
        version = '2.69-GCCcore-8.2.0'

        # only test one value
        self.assertEqual(mpathMapT[mpath], {'env/software/.joltik': '/etc/modulefiles/vsc'})
        # only test one value
        self.assertEqual(spiderT[mpath][name]['fileT'][module]['Version'], version)

        clustermap, mpmap = cluster_map(mpathMapT)
        # test 2 values, one hidden cluster
        self.assertEqual(clustermap['banette'], 'cluster/.banette')
        self.assertEqual(clustermap['delcatty'], 'cluster/delcatty')

        # test one, multivalue
        self.assertEqual(mpmap['/apps/gent/CO7/haswell-ib/modules/all'], ['golett', 'phanpy', 'swalot'])

        # only two mpaths in the mocked spiderT
        mpath2 = '/apps/gent/CO7/skylake-ib/modules/all'
        newmpmap = {mpath: mpmap[mpath], mpath2: mpmap[mpath2]}
        softmap = software_map(spiderT, newmpmap)
        self.assertEqual(softmap['Autotools'],
                         {'.default': {'joltik': '20180311-GCCcore-8.3.0'},
                          '20180311-GCCcore-8.2.0': ['joltik'],
                          '20180311-GCCcore-8.3.0': ['joltik']})
        self.assertEqual(softmap['Bazel'],
                         {'.default': {'skitty': '0.20.0-GCCcore-8.2.0',
                                       'victini': '0.20.0-GCCcore-8.2.0'},
                          '0.20.0-GCCcore-8.2.0': ['skitty', 'victini'],
                          '0.24.1-GCCcore-8.2.0': ['skitty', 'victini'],
                          '0.25.2-GCCcore-8.2.0': ['skitty', 'victini'],
                          '0.26.1-GCCcore-8.2.0': ['skitty', 'victini']})


        clview = software_cluster_view(softmap=softmap)
        # regular ordered
        self.assertEqual(clview['joltik']['Autoconf'], ['2.69-GCCcore-8.3.0', '2.69-GCCcore-8.2.0'])
        # non-trivial (first) default, rest ordered
        #   this default was manually set in the spiderT.lua test data
        self.assertEqual(clview['skitty']['Bazel'],
                         ['0.20.0-GCCcore-8.2.0', '0.26.1-GCCcore-8.2.0',
                          '0.25.2-GCCcore-8.2.0', '0.24.1-GCCcore-8.2.0'])

        stats = make_stats(clustermap, softmap)
        self.assertEqual(stats, {
            'clusters': 11,
            'modules_banette': 0,
            'modules_delcatty': 0,
            'modules_ditto': 0,
            'modules_golett': 0,
            'modules_joltik': 6,
            'modules_metapod': 0,
            'modules_phanpy': 0,
            'modules_shuppet': 0,
            'modules_skitty': 8,
            'modules_swalot': 0,
            'modules_victini': 8,
            'total_modules': 22,
            'total_names': 6,
        })
