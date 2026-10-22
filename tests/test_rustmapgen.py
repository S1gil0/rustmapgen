#!/usr/bin/env python3
"""RustMapGen's own tests. They need no game: maps are written here, and the game is a stand-in script.

    python -m unittest discover -s tests        (or: python tests/test_rustmapgen.py)
"""
import glob
import gzip
import hashlib
import io
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

import lz4.block
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rustmapgen import analyze, checks, library, mapfile, paths, recipe as recipes, release, render, runner, server, templates  # noqa: E402

MONUMENT = 'assets/bundled/prefabs/autospawn/monument/medium/compound.prefab'
RECYCLER = 'assets/bundled/prefabs/static/recycler_static.prefab'
EDITOR_ONLY = 4000000001          # an id the game has no prefab for


def _field(number, payload):
    if isinstance(payload, float):
        return mapfile._varint_bytes(number << 3 | 5) + struct.pack('<f', payload)
    if isinstance(payload, int):
        return mapfile._varint_bytes(number << 3) + mapfile._varint_bytes(payload)
    if isinstance(payload, str):
        payload = payload.encode()
    return mapfile._varint_bytes(number << 3 | 2) + mapfile._varint_bytes(len(payload)) + payload


def _world(size, prefabs, res=5, paths_=(), topology=None):
    """A `WorldData` as the game serialises it: size, layers, prefabs, paths."""
    out = _field(1, size)
    terrain = np.full((res, res), 16383 + 300, dtype='<i2').tobytes()
    tags = bytes(4 * 16) if topology is None else np.asarray(topology, dtype='<i4').tobytes()
    for name, blob in (('terrain', terrain), ('height', terrain), ('water', bytes(len(terrain))),
                       ('splat', bytes(8 * 16)), ('biome', bytes(5 * 16)), ('topology', tags), ('alpha', bytes(16))):
        out += _field(2, _field(1, name) + _field(2, blob))
    for category, ident, position, rotation in prefabs:
        out += mapfile.encode_prefab(category, ident, position, rotation, (1.0, 1.0, 1.0))
    for name, nodes in paths_:
        body = _field(1, name) + _field(5, 4.0)
        for x, y, z in nodes:
            body += _field(15, b''.join(_field(i + 1, float(v)) for i, v in enumerate((x, y, z)) if v))
        out += _field(4, body)
    return out


def _write(path, world, version=10):
    blob = struct.pack('<I', version) + (struct.pack('<q', 1790000000000) if version == 10 else b'')
    packed = lz4.block.compress(world, store_size=False, mode='high_compression')
    blob += mapfile._varint_bytes(3) + mapfile._varint_bytes(len(world)) + mapfile._varint_bytes(len(packed)) + packed
    with open(path, 'wb') as handle:
        handle.write(blob)


class Isolated(unittest.TestCase):
    """A data folder and a game folder of the test's own."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.saved = {key: os.environ.get(key) for key in ('RUSTMAPGEN_DATA', 'RUSTMAPGEN_GAME', 'RUSTMAPGEN_GAME_NEXT')}
        os.environ['RUSTMAPGEN_DATA'] = os.path.join(self.folder.name, 'data')
        os.environ['RUSTMAPGEN_GAME'] = os.path.join(self.folder.name, 'game')
        os.environ['RUSTMAPGEN_GAME_NEXT'] = os.path.join(self.folder.name, 'game-next')
        paths.ensure_data()

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.folder.cleanup()


class MapFileTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, 'small.map')
        self.prefabs = [('Monument', mapfile.prefab_id(MONUMENT), (10.0, 5.0, -20.0), (0.0, 90.0, 0.0)),
                        ('Decor', 77, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))]
        _write(self.path, _world(800, self.prefabs, paths_=[('Rail 0', [(0, 0, 0), (30, 0, 40)]), ('Road 3', [(0, 0, 0), (0, 0, 10)])]))

    def tearDown(self):
        self.folder.cleanup()

    def test_the_road_tag_comes_off_the_whole_map_and_nothing_else_changes(self):
        topology = np.zeros((16, 16), dtype='<i4')
        topology[2:5, :] = 2048 | 4096 | 128        # road, roadside, ocean
        topology[9, 3] = 2048
        _write(self.path, _world(800, self.prefabs, topology=topology))
        world = mapfile.MapFile(self.path)
        height = world.layer('height').copy()
        self.assertEqual(world.clear_topology(2048), 49)
        out = os.path.join(self.folder.name, 'out.map')
        world.save(out)
        again = mapfile.MapFile(out)
        self.assertEqual(int(np.count_nonzero(again.layer('topology') & 2048)), 0)
        self.assertEqual(int(np.count_nonzero(again.layer('topology') & 4096)), 48)
        self.assertEqual(int(np.count_nonzero(again.layer('topology') & 128)), 48)
        self.assertTrue(np.array_equal(again.layer('height'), height))
        self.assertEqual(len(again.id), 2)

    def test_reads_prefabs_paths_and_layers(self):
        world = mapfile.MapFile(self.path)
        self.assertEqual((world.size, world.version, len(world.id)), (800, 10, 2))
        self.assertEqual(world.category, ['Monument', 'Decor'])
        np.testing.assert_allclose(world.position[0], (10.0, 5.0, -20.0))
        np.testing.assert_allclose(world.rotation[0], (0.0, 90.0, 0.0))
        self.assertEqual([p['name'] for p in world.paths], ['Rail 0', 'Road 3'])
        self.assertEqual(world.paths[0]['nodes'][1], (30.0, 0.0, 40.0))
        self.assertEqual(world.paths[0]['width'], 4.0)
        self.assertEqual(world.layer('height').shape, (5, 5))
        self.assertEqual(world.layer('splat').shape, (8, 4, 4))
        self.assertAlmostEqual(float(world.metres('height')[0, 0]), 300 / 32766 * 1000 - 500 + 16383 / 32766 * 1000, places=3)

    def test_saving_appends_and_keeps_every_original_byte(self):
        world = mapfile.MapFile(self.path)
        before = bytes(world.data)
        world.add_prefab('Decor', mapfile.prefab_id(RECYCLER), (1.5, 0.0, -2.25), (0.0, 270.0, 0.0))
        out = os.path.join(self.folder.name, 'out.map')
        world.save(out, timestamp=1791000000000)
        again = mapfile.MapFile(out)
        self.assertEqual(bytes(again.data[:len(before)]), before)           # nothing the game wrote was re-encoded
        self.assertEqual((again.version, again.timestamp, len(again.id)), (10, 1791000000000, 3))
        self.assertEqual(int(again.id[2]), mapfile.prefab_id(RECYCLER))
        np.testing.assert_allclose(again.position[2], (1.5, 0.0, -2.25))
        np.testing.assert_allclose(again.rotation[2], (0.0, 270.0, 0.0))
        np.testing.assert_allclose(again.scale[2], (1.0, 1.0, 1.0))
        self.assertEqual(again.category[2], 'Decor')
        self.assertEqual(len(again.paths), 2)
        self.assertFalse(os.path.exists(out + '.partial'))

    def test_a_large_world_is_cut_in_blocks_the_reader_joins(self):
        world = mapfile.MapFile(self.path)
        world.data = bytearray(world.data) + mapfile.encode_prefab('Decor', 5, (1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)) * 90000
        out = os.path.join(self.folder.name, 'big.map')
        world.save(out)
        self.assertGreater(len(world.data), 2 * mapfile.CHUNK)
        self.assertEqual(len(mapfile.MapFile(out, paths=False).id), 2 + 90000)

    def test_a_version_9_file_is_written_back_as_version_10(self):
        old = os.path.join(self.folder.name, 'old.map')
        _write(old, _world(800, self.prefabs), version=9)
        world = mapfile.MapFile(old)
        self.assertEqual(world.version, 9)
        out = os.path.join(self.folder.name, 'new.map')
        world.save(out)
        self.assertEqual(mapfile.MapFile(out).version, 10)

    def test_a_file_that_is_no_map_is_refused(self):
        bad = os.path.join(self.folder.name, 'bad.map')
        with open(bad, 'wb') as handle:
            handle.write(b'not a map at all')
        with self.assertRaises(ValueError):
            mapfile.MapFile(bad)
        with open(self.path, 'rb') as handle:
            whole = handle.read()
        with open(bad, 'wb') as handle:
            handle.write(whole[:len(whole) // 2])
        with self.assertRaises(ValueError):
            mapfile.MapFile(bad)

    def test_the_prefab_number_is_the_games(self):
        # StringEx.ManifestHash: the first four bytes of the MD5 of the lower-case path. The number is the
        # one the game's own prefab table gives the Outpost.
        self.assertEqual(mapfile.prefab_id(MONUMENT), mapfile.prefab_id(MONUMENT.upper()))
        self.assertEqual(mapfile.prefab_id(MONUMENT), 1879405026)


class TemplateLimitsTest(unittest.TestCase):
    """A template is someone else's file: what reading it may cost is bounded before it is read."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_a_file_that_says_it_holds_too_much_is_refused_before_anything_is_unpacked(self):
        # About 2 MB that says it holds 33 blocks of 16 MiB: more than a template may, though each block passes alone.
        block = mapfile.MAX_BLOCK
        packed = block // 255 + 1
        body = struct.pack('<I', 10) + struct.pack('<q', 0)
        for _ in range(33):
            body += mapfile._varint_bytes(1) + mapfile._varint_bytes(block) + mapfile._varint_bytes(packed) + bytes(packed)
        path = os.path.join(self.folder.name, 'big.map')
        with open(path, 'wb') as handle:
            handle.write(body)
        unpacked = []
        decompress = mapfile.lz4.block.decompress
        self.addCleanup(setattr, mapfile.lz4.block, 'decompress', decompress)
        mapfile.lz4.block.decompress = lambda *a, **k: unpacked.append(1) or decompress(*a, **k)
        with self.assertRaises(ValueError) as caught:
            mapfile.MapFile(path, paths=False, limit=mapfile.TEMPLATE_WORLD, records=mapfile.TEMPLATE_RECORDS)
        self.assertIn('more than 512 MB', str(caught.exception))
        self.assertEqual(unpacked, [])

    def test_a_template_saved_from_a_large_map_is_read(self):
        # A template cut from a map of about 3,300 m unpacks to some 150 MB (a 6,000 m map to some 500): read as before.
        res = 5000
        world = _world(3300, [('Decor', 77, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))], res=res)
        self.assertGreater(len(world), 128 << 20)
        path = os.path.join(self.folder.name, 'large.map')
        body = struct.pack('<I', 10) + struct.pack('<q', 0)
        for start in range(0, len(world), 1 << 20):      # in the game's own blocks of 1 MiB
            chunk = world[start:start + (1 << 20)]
            packed = lz4.block.compress(chunk, store_size=False)
            body += mapfile._varint_bytes(3) + mapfile._varint_bytes(len(chunk)) + mapfile._varint_bytes(len(packed)) + packed
        with open(path, 'wb') as handle:
            handle.write(body)
        del world
        read = mapfile.MapFile(path, paths=False, limit=mapfile.TEMPLATE_WORLD, records=mapfile.TEMPLATE_RECORDS)
        self.assertEqual((read.size, len(read.id)), (3300, 1))

    def test_a_number_longer_than_ten_bytes_and_a_field_past_its_end_are_refused(self):
        with self.assertRaises(ValueError):
            mapfile.varint(bytes([0x80] * 11 + [0x01]), 0)
        self.assertEqual(mapfile.varint(bytes([0xff] * 9 + [0x01]), 0)[1], 10)
        field = mapfile._varint_bytes(3 << 3 | 2) + mapfile._varint_bytes(50) + b'short'
        with self.assertRaises(ValueError):
            list(mapfile.raw_fields(field, 0, len(field)))

    def test_a_template_with_more_prefabs_than_a_template_holds_is_refused(self):
        path = os.path.join(self.folder.name, 'crowd.map')
        _write(path, _world(1000, [('Decor', 77, (float(i), 0.0, 0.0), (0.0, 0.0, 0.0)) for i in range(12)]))
        records = templates.TEMPLATE_RECORDS
        self.addCleanup(setattr, templates, 'TEMPLATE_RECORDS', records)
        templates.TEMPLATE_RECORDS = 10
        with self.assertRaises(ValueError) as caught:
            templates.Template(path, {77: 'assets/bundled/prefabs/autospawn/decor/x.prefab'})
        self.assertIn('more than 10 prefabs', str(caught.exception))
        self.assertEqual(len(mapfile.MapFile(path).id), 12)          # read as a whole map, it is fine


class RailNetworkTest(unittest.TestCase):
    """Tracks join as the game joins them: an end on another track's node within 0.1 m, the two running within 10°."""

    def rails(self, *extra):
        import math as _m
        ring = [(round(300 * _m.cos(a / 30 * 2 * _m.pi), 2), 10.0, round(300 * _m.sin(a / 30 * 2 * _m.pi), 2)) for a in range(31)]
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, 'rails.map')
        _write(path, _world(1000, [], paths_=[('Rail 0', ring)] + list(extra)))
        return analyze._rails(mapfile.MapFile(path)), ring

    @staticmethod
    def along(ring, node, turn_deg, metres=30.0):
        """A point `metres` from a ring node, in the ring's direction there turned by `turn_deg`."""
        import math as _m
        way = np.array(ring[node + 1]) - np.array(ring[node - 1])
        way = way / np.linalg.norm(way)
        turn = _m.radians(turn_deg)
        x, z = way[0] * _m.cos(turn) - way[2] * _m.sin(turn), way[0] * _m.sin(turn) + way[2] * _m.cos(turn)
        return (ring[node][0] + metres * x, 10.0, ring[node][2] + metres * z)

    def test_a_spur_that_leaves_the_ring_along_it_joins_it(self):
        _, ring = self.rails()
        found, _ = self.rails(('Rail 1', [ring[5], self.along(ring, 5, 5)]))
        self.assertEqual((found['spurs'], found['loose'], found['near_misses']), (1, 0, []))

    def test_a_spur_that_meets_the_ring_at_more_than_ten_degrees_is_a_cut_and_said_so(self):
        # CollateTrainTracks joins two tracks only where they run within 10° of each other (MAX_NODE_ANGLE).
        _, ring = self.rails()
        for degrees, loose in ((9, 0), (11, 1)):
            found, _ = self.rails(('Rail 1', [ring[5], self.along(ring, 5, degrees)]))
            self.assertEqual(found['loose'], loose, f'{degrees}°')
        found, _ = self.rails(('Rail 1', [ring[5], self.along(ring, 5, 90)]))
        self.assertEqual(found['near_misses'][0]['angle_deg'], 90.0)
        detail = checks.structure({'rails': found, 'rail_m': 2000})[0]['detail']
        self.assertIn('a track meets another at 90°, and the game joins tracks only where they run within 10° of each other', detail)

    def test_an_end_0_79_m_below_the_ring_is_a_cut_and_said_where(self):
        _, ring = self.rails()
        low = (ring[5][0], 10.0 - 0.79, ring[5][2])
        found, _ = self.rails(('Rail 1', [low, (low[0] + 40, 10.0, low[2] + 40), (low[0] + 80, 10.0, low[2] + 60)]))
        self.assertEqual(found['loose'], 1)
        self.assertEqual(found['near_misses'], [{'x': round(low[0]), 'z': round(low[2]), 'gap_m': 0.79, 'higher_m': 0.79}])
        self.assertEqual(found['loose_at'], [[round(low[0] + 40), round(low[2] + 40)]])
        detail = checks.structure({'rails': found, 'rail_m': 2000})[0]['detail']
        self.assertIn(f"a track ends 0.79 m from another (below it)", detail)
        self.assertIn('red on the map', detail)


class RotationTest(unittest.TestCase):
    def test_a_turn_about_y_moves_x_towards_minus_z(self):
        moved = templates.qrot(templates.quat((0.0, 90.0, 0.0)), (0.0, 0.0, 1.0))      # Unity: forward becomes right
        np.testing.assert_allclose(moved, (1.0, 0.0, 0.0), atol=1e-9)

    def test_euler_angles_survive_the_round_trip(self):
        for angles in ((0.0, 0.0, 0.0), (0.0, 291.2, 0.0), (10.0, 348.5, 0.0), (350.0, 45.0, 20.0), (30.0, 200.0, 315.0), (90.0, 30.0, 0.0)):
            back = templates.euler(templates.quat(angles))
            self.assertLess(templates.angle_between(templates.quat(angles), templates.quat(back)), 0.01, angles)


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.names = {mapfile.prefab_id(MONUMENT): MONUMENT, mapfile.prefab_id(RECYCLER): RECYCLER}
        monument, recycler = mapfile.prefab_id(MONUMENT), mapfile.prefab_id(RECYCLER)
        # The anchor is not the first prefab and stands off the origin, as map editors save it.
        self.template = os.path.join(self.folder.name, 'template.map')
        _write(self.template, _world(1000, [('Decor', recycler, (13.0, 6.0, 0.0), (0.0, 30.0, 0.0)),
                                              ('Decor', EDITOR_ONLY, (1.0, 5.0, 1.0), (0.0, 0.0, 0.0)),
                                              ('Dungeon', monument, (3.0, 5.0, 0.0), (0.0, 0.0, 0.0))]), version=9)
        self.target = os.path.join(self.folder.name, 'target.map')
        _write(self.target, _world(3500, [('Monument', monument, (100.0, 20.0, 200.0), (0.0, 90.0, 0.0)),
                                           ('Monument', monument, (-400.0, 8.0, 50.0), (0.0, 180.0, 0.0))]))

    def tearDown(self):
        self.folder.cleanup()

    def test_every_copy_gets_the_additions_where_they_stand_around_the_anchor(self):
        template = templates.Template(self.template, self.names)
        self.assertEqual((len(template.rows), template.dropped), (1, [EDITOR_ONLY]))
        target = mapfile.MapFile(self.target)
        self.assertEqual(template.compare(target)['found'], 0)
        self.assertEqual(template.apply(target), (2, 2))
        out = os.path.join(self.folder.name, 'merged.map')
        target.save(out)
        merged = mapfile.MapFile(out)
        self.assertEqual(len(merged.id), 4)
        # 10 m east of the anchor in the template; the first copy is turned 90 degrees (east becomes south).
        np.testing.assert_allclose(merged.position[2], (100.0, 21.0, 190.0), atol=1e-4)
        np.testing.assert_allclose(merged.rotation[2], (0.0, 120.0, 0.0), atol=1e-3)
        np.testing.assert_allclose(merged.position[3], (-410.0, 9.0, 50.0), atol=1e-4)
        np.testing.assert_allclose(merged.rotation[3], (0.0, 210.0, 0.0), atol=1e-3)
        self.assertEqual(merged.category[2:], ['Decor', 'Decor'])
        result = template.compare(merged)
        self.assertEqual((result['copies'], result['found'], result['expected']), (2, 2, 2))
        self.assertLess(result['worst_m'], 0.001)
        self.assertLess(result['worst_deg'], 0.01)

    def test_a_prefab_the_game_has_moved_is_placed_as_the_one_it_is_now(self):
        old, new = templates.MOVED[0]
        was, now = old + 'npcvendingmachine_weapons_hapis.prefab', new + 'npcvendingmachine_weapons_hapis.prefab'
        monument = mapfile.prefab_id(MONUMENT)
        names = {**self.names, mapfile.prefab_id(now): now}
        self.assertEqual(templates.moved_ids(names), {mapfile.prefab_id(was): mapfile.prefab_id(now)})
        path = os.path.join(self.folder.name, 'moved.map')
        _write(path, _world(1000, [('Decor', mapfile.prefab_id(was), (13.0, 6.0, 0.0), (0.0, 30.0, 0.0)),
                                   ('Dungeon', monument, (3.0, 5.0, 0.0), (0.0, 0.0, 0.0))]), version=9)
        template = templates.Template(path, names)
        self.assertEqual(([row[0] for row in template.rows], template.moved), ([mapfile.prefab_id(now)], 1))
        # A game that does not have the prefab in its new folder either cannot spawn it: dropped.
        self.assertEqual(templates.Template(path, self.names).dropped, [mapfile.prefab_id(was)])

    def test_a_template_needs_exactly_one_monument(self):
        with self.assertRaises(ValueError):
            templates.Template(self.target, self.names)


class RecipeTest(unittest.TestCase):
    def presets(self):
        found = []
        for path in sorted(glob.glob(paths.package('presets', '*.json'))):
            with open(path) as handle:
                found.append((os.path.basename(path)[:-len('.json')], json.load(handle)))
        return found

    def test_the_presets_are_whole_and_ship_no_template(self):
        found = self.presets()
        self.assertGreaterEqual(len(found), 4)
        for name, recipe in found:
            self.assertEqual(recipe['name'], name)
            self.assertEqual(recipes.normalise(recipe), recipe, name)       # nothing missing, nothing the reader would change
            self.assertEqual(recipe['templates'], [], name)
            world, job = recipes.compile(recipe, '/out', seed=7)
            self.assertEqual(len(world['PrefabBlacklist']), len(set(world['PrefabBlacklist'])))
            self.assertTrue(all(fragment == fragment.lower() for fragment in world['PrefabBlacklist']))    # the game compares lower-case paths
            wanted = {prefab for prefab, state in recipe['monuments'].items() if state == 'want'}
            blocked = {prefab for prefab, state in recipe['monuments'].items() if state == 'block'}
            self.assertFalse(wanted & blocked)
            if recipe['centre']['monument']:
                self.assertIn(recipe['centre']['monument'], wanted)

    def test_each_decision_reaches_the_game_or_the_mod(self):
        recipe = recipes.defaults()
        recipe.update({'size': 3500, 'monuments': {'compound': 'want', 'airfield_1': 'want', 'bandit_town': 'block', 'lighthouse': 'any'},
                       'scenery': {'icebergs': 'block', 'lakes': 'want', 'road car wrecks': 'block'}, 'block_extra': ['Autospawn/Decor/Roadside/'],
                       'rail_ring': 'always', 'road_ring': 'never', 'steps': {'monument/cave': {'count': 4, 'distance_same': 300}},
                       'relax_distances': 0.5, 'centre': {'monument': 'compound', 'start_m': 4.0, 'grow_m': 0.5, 'within_m': 300, 'any_tier': True},
                       'underwater_labs': {'min': 2, 'max': 3},
                       'terrain': {'land_share': 0.62, 'profile': {'p10': 5, 'p50': 16, 'p90': 33, 'p99': 59, 'max': 83}, 'shallows': [[1, 0.05], [3, 0.07]],
                                   'relief_radius_m': 70, 'relief_keep': 0.85, 'middle_plateau_m': 120}})
        recipe['world']['Rivers'] = False
        world, job = recipes.compile(recipe, '/out', seed=11)
        self.assertIs(world['Rivers'], False)
        self.assertIn('autospawn/monument/medium/bandit_town.', world['PrefabBlacklist'])
        self.assertIn('autospawn/decor/iceberg/', world['PrefabBlacklist'])
        self.assertIn('autospawn/decor/roadside/', world['PrefabBlacklist'])
        self.assertFalse(any('lighthouse' in fragment or 'lake' in fragment for fragment in world['PrefabBlacklist']))
        self.assertEqual(job['MonumentPriority'], {'monument/large/airfield_1.': 'Highest', 'monument/medium/compound.': 'First'})
        self.assertEqual(job['MonumentMinWorldSize'], {'monument/large/airfield_1.': 0, 'monument/medium/compound.': 0})
        self.assertEqual((job['CentreMonument'], job['CentreStart'], job['CentreGrow']), ('monument/medium/compound.', 4.0, 0.5))
        self.assertEqual((job['RingMinWorldSize'], job['RoadRingMinWorldSize']), (0, 1000000))
        self.assertEqual(job['Steps']['monument/cave'], {'Count': 4, 'MinWorldSize': -1, 'DistanceSame': 300, 'DistanceOther': -1})
        self.assertEqual(job['Steps']['unique_environment/lake']['MinWorldSize'], 0)          # wanted scenery: its step runs on any world
        self.assertEqual(job['Steps']['monument/underwater_lab']['Count'], 3)
        self.assertEqual(recipes.compile(recipe, '/out', seed=10)[1]['Steps']['monument/underwater_lab']['Count'], 2)
        self.assertEqual((job['RelaxDistances'], job['LandShare']), (0.5, 0.62))
        self.assertEqual(job['HeightProfile'], [5.0, 16.0, 33.0, 59.0, 83.0])
        self.assertEqual((job['SeaDepths'], job['SeaShares']), ([1.0, 3.0], [0.05, 0.07]))
        self.assertEqual((job['ReliefRadius'], job['ReliefKeep'], job['MiddlePlateau'], job['CentreAnyTier']), (70.0, 0.85, 120.0, True))

    def test_electrical_boxes_and_road_signs_are_two_kinds(self):
        boxes = recipes.normalise(dict(recipes.defaults(), scenery={'electrical boxes': 'block'}))
        blacklist = recipes.compile(boxes, '/out', seed=1)[0]['PrefabBlacklist']
        self.assertIn('autospawn/decor/roadside/electrical_box', blacklist)
        self.assertFalse(any('roadsign' in fragment for fragment in blacklist))
        # A recipe saved when they were one kind blocks both, and a newer word for one of them wins.
        old = recipes.normalise(dict(recipes.defaults(), scenery={'roadside boxes and signs': 'block', 'road signs': 'any'}))
        self.assertEqual(old['scenery'], {'electrical boxes': 'block'})
        self.assertEqual(recipes.normalise(dict(recipes.defaults(), scenery={'roadside boxes and signs': 'block'}))['scenery'],
                         {'electrical boxes': 'block', 'road signs': 'block'})

    def test_a_recipe_that_changes_nothing_asks_for_nothing(self):
        world, job = recipes.compile(recipes.defaults(), '/out')
        self.assertEqual(world['PrefabBlacklist'], [])
        self.assertEqual((job['RingMinWorldSize'], job['RoadRingMinWorldSize'], job['MonumentPriority'], job['Steps']), (-1, -1, {}, {}))
        self.assertEqual((job['LandShare'], job['CoastLift'], job['HeightScale'], job['HeightProfile'], job['CentreMonument']), (0.0, 0.0, 1.0, [], ''))
        self.assertEqual(job['BiomeJungle'], -1.0)

    def test_the_jungle_share_goes_to_the_mod(self):
        # The game's world config file drops PercentageBiomeJungle, so the mod has to be told.
        recipe = recipes.defaults()
        recipe['world']['PercentageBiomeJungle'] = 0.0
        world, job = recipes.compile(recipe, '/out')
        self.assertEqual((job['BiomeJungle'], world['PercentageBiomeJungle']), (0.0, 0.0))

    def test_a_monument_whose_biomes_are_all_at_zero_is_named(self):
        world = recipes.defaults()['world']
        self.assertEqual(recipes.biomes_off({'biomes': ['Arid']}, world), [])
        world['PercentageBiomeArid'] = 0.0
        self.assertEqual(recipes.biomes_off({'biomes': ['Arid']}, world), ['Desert'])
        self.assertEqual(recipes.biomes_off({'biomes': ['Arid', 'Temperate']}, world), [])
        self.assertEqual(recipes.biomes_off({'biomes': 'any'}, world), [])
        self.assertEqual(recipes.biomes_off({}, world), [])

    def test_what_makes_no_sense_is_refused_with_the_reason(self):
        def bad(**change):
            recipe = recipes.defaults()
            for key, value in change.items():
                if isinstance(value, dict) and isinstance(recipe.get(key), dict):
                    recipe[key] = dict(recipe[key], **value)
                else:
                    recipe[key] = value
            with self.assertRaises(ValueError) as caught:
                recipes.normalise(recipe)
            return str(caught.exception)
        self.assertIn('size', bad(size=900))
        self.assertIn('tier shares', bad(world={'PercentageTier0': 0.9}))
        self.assertIn('want, block, any', bad(monuments={'compound': 'maybe'}))
        self.assertIn('not a monument', bad(centre={'monument': 'no_such_monument'}))
        self.assertIn('roadside and offshore', bad(centre={'monument': 'gas_station_1'}))
        self.assertIn('under the sea', bad(centre={'monument': 'harbor_1'}))
        self.assertIn('rise', bad(terrain={'profile': {'p10': 5, 'p50': 4, 'p90': 33, 'p99': 59, 'max': 83}}))
        self.assertIn('shallows', bad(terrain={'shallows': [[3, 0.07], [1, 0.05]]}))
        self.assertIn('always, never, game', bad(rail_ring='sometimes'))
        self.assertIn('underwater labs', bad(underwater_labs={'min': 4, 'max': 2}))
        self.assertIn('filenames', bad(templates=['../other/outpost.map']))
        # What would break the map or cannot be made.
        self.assertIn('open holes', bad(world={'BelowGroundRails': False}))
        self.assertIn('land_share', bad(terrain={'land_share': 0.8}))
        self.assertIn('200 m', bad(terrain={'profile': {'p10': 5, 'p50': 16, 'p90': 33, 'p99': 59, 'max': 260}}))
        self.assertIn('0.1 to 20', bad(centre={'grow_m': 0.0}))
        # A monument the game no longer has is dropped quietly: an old recipe must still open.
        self.assertEqual(recipes.normalise(dict(recipes.defaults(), monuments={'gone_monument': 'want'}))['monuments'], {})


class ChecksTest(unittest.TestCase):
    RECIPE = {'monuments': {'compound': 'want', 'launch_site_1': 'want', 'bandit_town': 'block'}, 'scenery': {'icebergs': 'block', 'road power poles': 'block'},
              'rail_ring': 'always', 'world': {'AboveGroundRails': True}, 'centre': {'monument': 'compound', 'within_m': 150}}
    REFERENCE = {'ground_m': {'compound': 0.0, 'launch_site_1': -1.0}}

    def facts(self, **change):
        facts = {'size': 3500, 'rail_m': 15000, 'rail_paths': 20, 'monument_counts': {'compound': 1, 'launch_site_1': 1, 'lighthouse': 2},
                 'features': {'icebergs': 0, 'road power poles': 1}, 'outpost_from_centre_m': 12,
                 'monuments': [{'prefab': 'compound', 'label': 'Outpost', 'x': 9.0, 'z': 8.0, 'ground_m': 0.1},
                               {'prefab': 'launch_site_1', 'label': 'Launch Site', 'x': 500.0, 'z': 0.0, 'ground_m': -1.2}],
                 'rails': {'paths': 20, 'rings': 1, 'ring_m': 7000, 'sidings': 15, 'spurs': 4, 'loose': 0, 'max_grade': 0.2, 'max_turn_deg': 18.0,
                           'ring_grade': 0.2, 'ring_turn_deg': 18.0, 'side_grade': 0.2, 'side_turn_deg': 18.0, 'lowest_m': 2.0},
                 'coast': {'sea_tag_agrees': 0.996, 'land_tagged_sea': 0.005}, 'terrain': {'land': 0.62}}
        facts.update(change)
        return facts

    def failed(self, recipe=None, **change):
        return [check['rule'] for check in checks.checks(recipe or self.RECIPE, self.facts(**change), self.REFERENCE) if not check['ok']]

    def test_a_kind_blocked_under_its_old_name_is_looked_for_under_its_new_ones(self):
        recipe = dict(self.RECIPE, scenery={'roadside boxes and signs': 'block'})
        self.assertEqual(self.failed(recipe, features={'electrical boxes': 0, 'road signs': 0}), [])
        self.assertEqual(self.failed(recipe, features={'electrical boxes': 0, 'road signs': 3}), ['Nothing blocked is on the map'])

    def test_a_step_that_places_one_puts_the_wanted_one_first_and_the_check_says_why(self):
        catalog = recipes.load_catalog()
        recipe = recipes.normalise(dict(recipes.defaults(), monuments={'desert_military_base_a': 'want', 'desert_military_base_b': 'want', 'airfield_1': 'want'}))
        _, job = recipes.compile(recipe, 'out', 1, catalog)
        self.assertEqual(job['MonumentPriority']['monument/military_bases/desert_military_base_a.'], 'First')
        self.assertEqual(job['MonumentPriority']['monument/large/airfield_1.'], 'Highest')        # its step places as many as fit
        self.assertEqual([(step['label'], places, len(rows)) for step, places, rows in recipes.over_wanted(recipe, catalog)], [('Desert Military', 1, 2)])
        self.assertEqual(recipes.over_wanted(dict(recipe, steps={'monument/military_bases': {'count': 2}}), catalog), [])
        labs = dict(recipe, monuments={'underwater_lab_a': 'want', 'underwater_lab_b': 'want'})
        self.assertEqual(len(recipes.over_wanted(labs, catalog)), 1)                              # the game places one
        self.assertEqual(recipes.over_wanted(dict(labs, underwater_labs={'min': 2, 'max': 3}), catalog), [])
        counts = dict(self.facts()['monument_counts'], desert_military_base_a=1, airfield_1=1)
        found = {check['rule']: check for check in checks.checks(recipe, self.facts(monument_counts=counts), self.REFERENCE)}
        self.assertFalse(found['Wanted monuments placed']['ok'])
        self.assertIn('Desert Military Base B (the Desert Military step places 1 a map)', found['Wanted monuments placed']['detail'])

    def test_the_land_a_setting_may_ask_follows_the_size(self):
        self.assertEqual([recipes.land_share_max(size) for size in (2000, 3000, 3500, 4000, 6000)], [0.5, 0.62, 0.68, 0.7, 0.7])
        recipe = dict(self.RECIPE, terrain={'land_share': 0.7})
        census = {'steps': [{'step': 'Land', 'land_share_by_shore_move_m': {'20': 0.6}}]}
        found = {check['rule']: check for check in checks.checks(recipe, self.facts(terrain={'land': 0.6}, size=3000), {}, census)}
        self.assertIn('a map of 3,000 may ask at most 62 %', found['Land as asked']['detail'])

    def test_a_sound_map_passes_everything(self):
        found = checks.checks(self.RECIPE, self.facts(), self.REFERENCE)
        self.assertEqual([check['rule'] for check in found if not check['ok']], [])
        self.assertEqual([check['kind'] for check in found].count('structure'), 5)

    def test_each_fault_fails_its_own_check(self):
        rails = self.facts()['rails']
        cases = {'Rail ring closed, nothing cut off': {'rails': dict(rails, loose=1)},
                 'Rail grades and curves': {'rails': dict(rails, ring_grade=0.5)},
                 'Rails above the water': {'rails': dict(rails, lowest_m=-3.0)},
                 'Sea and land tagged as the heights say': {'coast': {'sea_tag_agrees': 0.95, 'land_tagged_sea': 0.1}},
                 'Wanted monuments placed': {'monument_counts': {'compound': 1, 'lighthouse': 1}}}
        for rule, change in cases.items():
            self.assertEqual(self.failed(**change), [rule])
        self.assertEqual(self.failed(monument_counts={'compound': 1, 'launch_site_1': 1, 'bandit_town': 1}), ['Nothing blocked is on the map'])
        self.assertEqual(self.failed(features={'icebergs': 3, 'road power poles': 1}), ['Nothing blocked is on the map'])
        self.assertEqual(self.failed(features={'icebergs': 0, 'road power poles': 2}), ['Nothing blocked is on the map'])     # one stray pole is normal
        floating = [dict(item, ground_m=item['ground_m'] + (6.0 if item['prefab'] == 'launch_site_1' else 0)) for item in self.facts()['monuments']]
        self.assertEqual(self.failed(monuments=floating), ['Monuments on the ground'])
        far = [dict(item, x=600.0) if item['prefab'] == 'compound' else item for item in self.facts()['monuments']]
        self.assertEqual(self.failed(monuments=far), ['Outpost around the middle'])

    def test_a_track_off_the_ring_may_be_as_steep_as_a_tunnel_ramp_the_ring_may_not(self):
        rails = self.facts()['rails']
        self.assertEqual(self.failed(rails=dict(rails, side_grade=0.415, side_turn_deg=38.2)), [])
        self.assertEqual(self.failed(rails=dict(rails, ring_grade=0.415)), ['Rail grades and curves'])
        self.assertEqual(self.failed(rails=dict(rails, side_grade=0.45)), ['Rail grades and curves'])

    def test_a_ring_asked_for_has_to_be_there(self):
        self.assertEqual(self.failed(rail_m=0, rails={'paths': 0, 'rings': 0, 'ring_m': 0}), ['Rail ring'])
        self.assertNotIn('Rail ring', [check['rule'] for check in checks.checks(dict(self.RECIPE, rail_ring='game'), self.facts(rail_m=0, rails={'paths': 0}), {})])

    def test_the_land_has_to_be_the_land_that_was_asked_for(self):
        recipe = dict(self.RECIPE, terrain={'land_share': 0.62})
        self.assertEqual(self.failed(recipe), [])
        self.assertEqual(self.failed(recipe, terrain={'land': 0.51}), ['Land as asked'])
        self.assertEqual(self.failed(recipe, terrain={}), ['Land as asked'])
        # Short of land: the most the seed could give, from the mod's Land step, is named.
        census = {'steps': [{'step': 'Land', 'land_share_by_shore_move_m': {'0': 0.49, '20': 0.6}}]}
        found = checks.checks(dict(recipe, terrain={'land_share': 0.7}), self.facts(terrain={'land': 0.6}), {}, census)
        self.assertIn('reaches 60 %', next(check['detail'] for check in found if check['rule'] == 'Land as asked'))

    def test_roads_that_can_be_built_on(self):
        recipe = dict(self.RECIPE, build_on_roads=True)
        self.assertEqual(self.failed(recipe, terrain={'land': 0.62, 'topology_road': 0.0}), [])
        self.assertEqual(self.failed(recipe, terrain={'land': 0.62, 'topology_road': 0.00001}), ['Roads can be built on'])

    def test_a_power_line_tower_in_the_air_fails_the_map(self):
        towers = {'count': 86, 'highest_air_m': 7.5, 'in_the_air': [{'prefab': 'powerline_a', 'x': 143.1, 'z': 1065.6, 'air_m': 7.5, 'near': 'Cave Small Hard', 'near_m': 11}]}
        found = checks.checks(self.RECIPE, self.facts(towers=towers), self.REFERENCE)
        rule = next(check for check in found if check['rule'] == checks.TOWER_RULE)
        self.assertEqual((rule['ok'], rule['detail']), (False, 'a tower 7.5 m in the air at x 143, z 1066 (11 m from Cave Small Hard) (limit 3.0 m)'))
        self.assertEqual(self.failed(towers=dict(towers, highest_air_m=1.4, in_the_air=[dict(towers['in_the_air'][0], air_m=1.4)])), [])

    def test_every_tunnel_entrance_has_its_way_down(self):
        ways = [{'x': 10.0, 'z': 20.0, 'bunker': True, 'depth_m': 15.0}, {'x': 90.0, 'z': 8.0, 'bunker': False, 'depth_m': 5.7},
                {'x': 500.0, 'z': 203.0, 'bunker': False, 'depth_m': 6.0}]
        tunnels = {'stations': 3, 'bunkers': 1, 'bunkers_without_stairs': [], 'ways_down': ways}
        self.assertEqual(self.failed(tunnels=tunnels), [])
        self.assertEqual(self.failed(tunnels=dict(tunnels, stations=4)), [checks.TUNNEL_RULE])
        self.assertEqual(self.failed(tunnels=dict(tunnels, bunkers=2, bunkers_without_stairs=[[700.0, -40.0]])), [checks.TUNNEL_RULE])

    def test_a_map_without_settings_still_gets_the_structure_checks(self):
        found = checks.checks(None, self.facts(size=1600), {})
        self.assertEqual({check['kind'] for check in found}, {'structure'})

    def test_templates_merged(self):
        rows = [{'template': 'outpost.map', 'monument': 'Outpost', 'copies': 1, 'placed': 159, 'of': 159}]
        self.assertTrue(checks.template_check(rows)['ok'])
        self.assertFalse(checks.template_check([dict(rows[0], placed=158)])['ok'])
        self.assertFalse(checks.template_check([dict(rows[0], copies=0, placed=0)])['ok'])
        self.assertIsNone(checks.template_check([]))
        # A template that cannot be checked fails the check, even beside one that passes.
        gone = {'template': 'airfield.map', 'monument': 'airfield.map', 'copies': 0, 'placed': 0, 'of': 0, 'ok': False, 'unverifiable': True}
        found = checks.template_check(rows + [gone])
        self.assertFalse(found['ok'])
        self.assertIn('cannot be checked: airfield.map', found['detail'])
        self.assertFalse(checks.template_check(rows + [dict(gone, copies=1, placed=1, of=1)])['ok'])

    def test_the_reference_names_monuments_of_the_catalog(self):
        reference = checks.load_reference()['ground_m']
        catalog = {row['prefab'] for row in recipes.load_catalog()['monuments']}
        self.assertGreater(len(reference), 20)
        self.assertFalse(set(reference) - catalog)


class StandingTest(unittest.TestCase):
    """What `describe` measures of the power line towers and of the ways down to the train tunnels."""
    AUTO = 'assets/bundled/prefabs/autospawn/'
    PATHS = {'tower': AUTO + 'decor/powerline/powerline_a.prefab', 'cave': AUTO + 'monument/cave/cave_small_hard.prefab',
             'bunker': AUTO + 'tunnel-entrance/entrance_bunker_b.prefab', 'station': AUTO + 'tunnel-station/station-sn-0.prefab',
             '1200': AUTO + 'tunnel-link/link-stairwell-a-1200.prefab', 'bottom': AUTO + 'tunnel-link/link-stairwell-bottom-a.prefab'}

    def describe(self, *prefabs):
        names = {mapfile.prefab_id(path): path for path in self.PATHS.values()}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'flat.map')
            _write(path, _world(800, [('Decor', mapfile.prefab_id(MONUMENT), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))]))
            ground = float(mapfile.MapFile(path).metres('height')[0, 0])
            _write(path, _world(800, [('Decor', mapfile.prefab_id(self.PATHS[kind]), (x, ground + up, z), (0.0, 0.0, 0.0))
                                      for kind, x, up, z in prefabs]))
            return analyze.describe(mapfile.MapFile(path), names)

    def test_a_tower_is_measured_against_the_ground_under_it(self):
        facts = self.describe(('tower', -100.0, 2.0, 50.0), ('tower', 143.1, 9.5, 65.6), ('cave', 132.4, 0.0, 63.6))
        self.assertEqual((facts['towers']['count'], facts['towers']['highest_air_m']), (2, 7.5))
        self.assertEqual([check['rule'] for check in checks.structure(facts, {}) if not check['ok']], [checks.TOWER_RULE])

    def test_an_entrance_with_nothing_under_it_and_stairs_with_nothing_over_them(self):
        facts = self.describe(('bunker', 700.0, 0.0, -40.0), ('1200', 10.0, -27.0, 20.0), ('bottom', 10.0, -30.0, 20.0), ('station', 10.0, -48.0, 90.0))
        self.assertEqual(facts['tunnels']['bunkers_without_stairs'], [[700.0, -40.0]])
        rule = next(check for check in checks.structure(facts, {}) if check['rule'] == checks.TUNNEL_RULE)
        self.assertEqual(rule['detail'], 'the entrance at x 700, z -40 has no stairs under it; '
                                         'the stairs at x 10, z 20 end 15 m under the ground with no entrance over them')


@unittest.skipIf(os.name == 'nt', 'the stand-in game is a shell script')
class RunnerTest(Isolated):
    """The runner against a stand-in for the game: a script that writes a log and leaves."""

    def game(self, body):
        runner.claim()
        script = paths.game('RustDedicated')
        with open(script, 'w') as handle:
            handle.write('#!/bin/sh\n' + textwrap.dedent(body))
        os.chmod(script, 0o755)
        fake = os.path.join(self.folder.name, runner.MOD_FILE)
        with open(fake, 'wb') as handle:
            handle.write(b'the mod')
        self.addCleanup(setattr, runner, 'mod_source', runner.mod_source)
        runner.mod_source = lambda: fake
        self.job = paths.data('jobs', 'test-job')
        os.makedirs(self.job)
        with open(os.path.join(self.job, 'job.json'), 'w') as handle:
            json.dump({'Out': self.job}, handle)
        with open(paths.data('settings.json'), 'w') as handle:
            json.dump({'min_free_gb': 0, 'max_temp_c': 999, 'isolate_network': False}, handle)

    # The stand-in finds its log after "-logfile" and its job folder in RUSTMAPGEN_JOB, as the mod does.
    PRELUDE = '''
        while [ "$1" != "-logfile" ]; do shift; done; LOG="$2"
        JOB=$(dirname "$RUSTMAPGEN_JOB")
        '''

    def run_game(self):
        quiet = []
        return runner.run(self.job, ['+server.level', 'Procedural Map'], None, say=lambda *a, **k: quiet.append(a))

    def test_a_game_that_loads_the_mod_and_finishes_is_done(self):
        self.game(self.PRELUDE + '''
            echo "[RustMapGen] patches active" >> "$LOG"
            echo saved > "$JOB/done"
            ''')
        state, _, facts = self.run_game()
        self.assertEqual((state, facts['mod_loaded'], facts['exit_code']), ('done', True, 0))
        self.assertTrue(os.path.isfile(paths.game('HarmonyMods', runner.MOD_FILE)))

    CRASH = '''
            echo "[RustMapGen] patches active" >> "$LOG"
            echo "[1.1s] Caves" >> "$LOG"
            echo "DivideByZeroException: Attempted to divide by zero." >> "$LOG"
            echo "  at SeedRandom.Range (System.UInt32& seed, System.Int32 min, System.Int32 max) [0x0000b] in <2bc6>:0 " >> "$LOG"
            echo "  at GenerateDungeonBase.Process (System.UInt32 seed) [0x0019a] in <7e9d>:0 " >> "$LOG"
            echo "  at WorldSetup+<InitCoroutine>d__20.MoveNext () [0x00a1a] in <7e9d>:0 " >> "$LOG"
            '''

    def test_a_game_whose_own_map_generation_crashed_is_stopped(self):
        # A staging build of 2026-10 fails so on small maps, with or without the mod: the game then idles until the time limit.
        self.addCleanup(setattr, runner, 'CRASH_PATIENCE', runner.CRASH_PATIENCE)
        runner.CRASH_PATIENCE = 3
        self.game(self.PRELUDE + self.CRASH + '''
            sleep 60
            ''')
        started = time.monotonic()
        state, _, facts = self.run_game()
        self.assertEqual((state, facts['game_error'], facts['mod_loaded']), ('crashed', 'DivideByZeroException in GenerateDungeonBase.Process', True))
        self.assertLess(time.monotonic() - started, 20)

    def test_an_error_the_map_goes_on_after_is_not_a_crash(self):
        self.addCleanup(setattr, runner, 'CRASH_PATIENCE', runner.CRASH_PATIENCE)
        runner.CRASH_PATIENCE = 3
        self.game(self.PRELUDE + self.CRASH + '''
            sleep 1
            echo "[0.4s] Tiny Monuments" >> "$LOG"
            sleep 5
            echo saved > "$JOB/done"
            ''')
        state, _, facts = self.run_game()
        self.assertEqual((state, facts['game_error']), ('done', None))

    def test_a_game_that_failed_to_patch_is_stopped_at_once(self):
        self.game(self.PRELUDE + '''
            echo "Failed to patch all hooks" >> "$LOG"
            sleep 60
            ''')
        started = time.monotonic()
        state, _, _ = self.run_game()
        self.assertEqual(state, 'mod')
        self.assertLess(time.monotonic() - started, 20)

    def test_a_game_that_never_says_the_mod_is_in_is_stopped(self):
        self.game(self.PRELUDE + '''
            echo "starting an ordinary server" >> "$LOG"
            sleep 60
            ''')
        patience, runner.MOD_PATIENCE = runner.MOD_PATIENCE, 3
        try:
            started = time.monotonic()
            state, _, facts = self.run_game()
        finally:
            runner.MOD_PATIENCE = patience
        self.assertEqual((state, facts['mod_loaded']), ('mod', False))
        self.assertLess(time.monotonic() - started, 20)
        # Silence is no word of the game: a slow start does not keep every later map of this build from being made.
        self.assertIsNone(runner.mod_broken())

    def test_a_game_that_begins_the_map_without_the_mod_is_stopped_at_once(self):
        self.game(self.PRELUDE + '''
            echo "Generating procedural map of size 2000 with seed 1" >> "$LOG"
            sleep 60
            ''')
        started = time.monotonic()
        state, _, facts = self.run_game()
        self.assertEqual((state, facts['mod_loaded']), ('mod', False))
        self.assertLess(time.monotonic() - started, 20)
        self.assertIsNotNone(runner.mod_broken())          # the game said so: not tried again on this build

    def test_the_map_begun_before_the_mod_in_one_read_is_still_stopped(self):
        self.game(self.PRELUDE + '''
            printf "Generating procedural map of size 2000 with seed 1\\n[RustMapGen] patches active\\n" >> "$LOG"
            sleep 60
            ''')
        started = time.monotonic()
        self.assertEqual(self.run_game()[0], 'mod')
        self.assertLess(time.monotonic() - started, 20)

    def test_the_games_terminal_colours_never_reach_a_step(self):
        text, rest = runner._lines(b'', b'[2.1s] Road Monuments\x1b[0m\n\x1b[1;31m[RustMapGen] patches active\x1b[0m\npart')
        self.assertEqual((text, rest), ('[2.1s] Road Monuments\n[RustMapGen] patches active\n', b'part'))

    def test_a_failure_the_game_wrote_last_without_a_line_end_is_read(self):
        self.game(self.PRELUDE + '''
            echo "[RustMapGen] patches active" >> "$LOG"
            printf "Failed to patch all hooks" >> "$LOG"
            exit 1
            ''')
        self.assertEqual(self.run_game()[0], 'mod')

    def only_the_last_read_sees_the_log(self):
        """What the game writes between the last read and its end: the reads while it runs find nothing."""
        real, calls = runner._tail, []

        def late(path, start):
            calls.append(path)
            return (b'', start) if len(calls) == 1 else real(path, start)
        self.addCleanup(setattr, runner, '_tail', real)
        runner._tail = late

    def test_a_failure_at_the_end_outweighs_the_done_mark(self):
        self.only_the_last_read_sees_the_log()
        self.game(self.PRELUDE + '''
            echo "[RustMapGen] patches active" >> "$LOG"
            printf "Failed to patch all hooks" >> "$LOG"
            echo saved > "$JOB/done"
            ''')
        self.assertEqual(self.run_game()[0], 'mod')

    def test_the_map_begun_before_the_mod_is_seen_in_the_last_read_too(self):
        self.only_the_last_read_sees_the_log()
        self.game(self.PRELUDE + '''
            printf "Generating procedural map of size 2000 with seed 1\\n[RustMapGen] patches active\\n" >> "$LOG"
            echo saved > "$JOB/done"
            ''')
        self.assertEqual(self.run_game()[0], 'mod')

    def test_a_whole_failure_without_a_line_end_stops_a_running_game(self):
        self.game(self.PRELUDE + '''
            echo "[RustMapGen] patches active" >> "$LOG"
            printf "Failed to patch all hooks" >> "$LOG"
            sleep 60
            ''')
        started = time.monotonic()
        self.assertEqual(self.run_game()[0], 'mod')
        self.assertLess(time.monotonic() - started, 20)

    def test_a_game_an_update_left_unfinished_is_not_started(self):
        self.game(self.PRELUDE + '''
            echo started > "$JOB/started"
            ''')
        open(paths.game(runner.UPDATING), 'w').close()
        with self.assertRaises(RuntimeError):
            self.run_game()
        self.assertFalse(os.path.exists(os.path.join(self.job, 'started')))

    @unittest.skipIf(os.name == 'nt', 'POSIX signals')
    def test_a_command_stopped_by_a_signal_stops_its_game(self):
        import signal
        import subprocess
        self.game(self.PRELUDE + '''
            echo $$ > "$JOB/pid"
            echo "[RustMapGen] patches active" >> "$LOG"
            sleep 60
            ''')
        code = ('import sys; sys.path.insert(0, sys.argv[1]); from rustmapgen import runner\n'
                'runner.mod_source = lambda: sys.argv[2]\nrunner.stop_on_signals()\n'
                'runner.run(sys.argv[3], ["+server.level", "Procedural Map"], None, say=lambda *a, **k: None)')
        command = subprocess.Popen([sys.executable, '-c', code, ROOT, runner.mod_source(), self.job], env=dict(os.environ),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid_file = os.path.join(self.job, 'pid')
        for _ in range(100):
            if os.path.exists(pid_file) and os.path.getsize(pid_file):
                break
            time.sleep(0.1)
        with open(pid_file) as handle:
            pid = int(handle.read())
        command.send_signal(signal.SIGTERM)
        command.wait(timeout=30)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_a_marker_cut_in_two_by_a_read_is_still_seen(self):
        self.game(self.PRELUDE + '''
            echo "[RustMapGen] patches active" >> "$LOG"
            printf "Failed to pa" >> "$LOG"
            sleep 3
            printf "tch all hooks\\n" >> "$LOG"
            sleep 60
            ''')
        started = time.monotonic()
        self.assertEqual(self.run_game()[0], 'mod')
        self.assertLess(time.monotonic() - started, 20)

    def test_a_run_that_ends_early_leaves_no_game_running(self):
        self.game(self.PRELUDE + '''
            echo $$ > "$JOB/pid"
            echo "[RustMapGen] patches active" >> "$LOG"
            echo "Step one" >> "$LOG"
            sleep 60
            ''')

        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            runner.run(self.job, ['+server.level', 'Procedural Map'], 'Step', say=interrupted)
        with open(os.path.join(self.job, 'pid')) as handle:
            pid = int(handle.read())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_bad_settings_stop_the_run_before_the_game_starts(self):
        self.game(self.PRELUDE + '''
            echo started > "$JOB/started"
            ''')
        with open(paths.data('settings.json'), 'w') as handle:
            json.dump({'min_free_gb': 0, 'max_temp_c': 999, 'isolate_network': False, 'timeout_minutes': 'soon'}, handle)
        with self.assertRaises(ValueError):
            self.run_game()
        time.sleep(1)
        self.assertFalse(os.path.exists(os.path.join(self.job, 'started')))

    def test_a_game_without_its_job_file_read_is_stopped(self):
        self.game(self.PRELUDE + '''
            echo "[RustMapGen] patches idle" >> "$LOG"
            sleep 60
            ''')
        self.assertEqual(self.run_game()[0], 'mod')

    def test_the_game_is_bound_to_this_computer(self):
        argv, _ = runner._command(['+server.level', 'Procedural Map'], 'log.txt', dict(paths.DEFAULT_SETTINGS, isolate_network=False))
        joined = ' '.join(argv)
        for part in ('+server.ip 127.0.0.1', '+rcon.ip 127.0.0.1', '+app.port -1', '+server.maxplayers 1'):
            self.assertIn(part, joined)
        self.assertNotIn('rcon.password', joined)

    def test_one_run_at_a_time(self):
        with runner.lock():
            if os.name != 'nt':
                import subprocess
                probe = subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); from rustmapgen import runner\n'
                                        'try:\n with runner.lock(): print("got it")\nexcept runner.Busy: print("busy")', ROOT],
                                       capture_output=True, text=True, env=dict(os.environ))
                self.assertEqual(probe.stdout.strip(), 'busy')

    def test_a_game_folder_with_oxide_is_refused(self):
        self.game(self.PRELUDE)
        os.makedirs(paths.game('RustDedicated_Data', 'Managed'))
        open(paths.game('RustDedicated_Data', 'Managed', 'Oxide.Core.dll'), 'w').close()
        with self.assertRaises(RuntimeError):
            self.run_game()


class ServerTest(Isolated):
    """The panel's server: who may ask it for what."""

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.queue = server.Queue()
        self.queue._start = lambda job: (subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']), os.devnull)
        self.addCleanup(lambda: self.queue.process and self.queue.process.kill())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(self.queue, set(server.LOCAL_HOSTS)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        folder = paths.data('maps', 'test-map-1')
        os.makedirs(folder)
        with open(os.path.join(folder, 'final.map'), 'wb') as handle:
            handle.write(b'map bytes')
        with open(os.path.join(folder, 'map.json'), 'w') as handle:
            json.dump({'name': 'test-map-1'}, handle)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def ask(self, path, **request):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, **request), timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_the_page_and_a_map_file_are_served(self):
        self.assertEqual(self.ask('/')[0], 200)
        self.assertEqual(self.ask('/files/test-map-1/final.map'), (200, b'map bytes'))
        self.assertEqual(self.ask('/files/test-map-1/run.json')[0], 404)
        self.assertEqual(self.ask('/files/..%2Ftest-map-1/final.map')[0], 404)

    def test_no_page_of_the_panel_can_be_put_in_another_sites_frame(self):
        for path in ('/', '/app.js', '/api/status'):
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                self.assertEqual(response.headers.get('X-Frame-Options'), 'DENY', path)

    def test_a_request_that_names_another_host_is_refused(self):
        self.assertEqual(self.ask('/api/status', headers={'Host': 'evil.example:5350'})[0], 403)
        self.assertEqual(self.ask('/api/status', headers={'Host': 'localhost:5350'})[0], 200)

    def test_a_write_needs_the_panels_own_header(self):
        body = json.dumps({'recipe': 'large'}).encode()
        plain = {'Content-Type': 'application/json'}
        self.assertEqual(self.ask('/api/generate', data=body, headers=plain, method='POST')[0], 403)
        marked = dict(plain, **{'X-RustMapGen': '1'})
        self.assertEqual(self.ask('/api/generate', data=b'{"recipe":"no-such-recipe"}', headers=marked, method='POST')[0], 400)
        self.assertEqual(self.ask('/api/generate', data=b'{"recipe":"large","count":40}', headers=marked, method='POST')[0], 400)
        self.assertEqual(self.ask('/api/generate', data=b'{"settings":{"size":3500,"rail_ring":"sometimes"}}', headers=marked, method='POST')[0], 400)
        self.assertEqual(self.ask('/api/generate', data=b'{"settings":{"size":3500,"templates":["missing.map"]}}', headers=marked, method='POST')[0], 400)
        self.assertEqual(self.ask('/api/maps/test-map-1', method='DELETE')[0], 403)

    def test_presets_can_be_taken_off_the_list_but_the_game_default(self):
        marked = {'X-RustMapGen': '1'}
        names = lambda: sorted(r['name'] for r in json.loads(self.ask('/api/recipes')[1])['recipes'])   # noqa: E731
        self.assertIn('large', names())
        self.assertEqual(self.ask('/api/recipes/large', method='DELETE')[0], 403)
        self.assertEqual(self.ask('/api/recipes/large', headers=marked, method='DELETE')[0], 200)
        self.assertNotIn('large', names())
        self.assertTrue(os.path.isfile(paths.package('presets', 'large.json')))         # the file that came with the code stays
        self.assertEqual(self.ask('/api/recipes/large', headers=marked, method='DELETE')[0], 404)
        for name in names():
            self.assertEqual(self.ask('/api/recipes/' + name, headers=marked, method='DELETE')[0], 409 if name == 'game-default' else 200, name)
        self.assertEqual(names(), ['game-default'])
        self.assertEqual(self.ask('/api/presets-removed', headers=marked, method='DELETE')[0], 404)
        # Written into the list by hand, the game default is listed all the same.
        with open(paths.data('presets-removed.json'), 'w') as handle:
            json.dump(['game-default', 'small'], handle)
        self.assertIn('game-default', names())

    def test_the_game_update_is_queued_once_and_a_self_update_waits_for_an_empty_queue(self):
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        self.assertEqual(self.ask('/api/game-update', data=b'{}', method='POST')[0], 403)
        status = json.loads(self.ask('/api/status')[1])
        self.assertIn('version', status)
        code, body = self.ask('/api/game-update', data=b'{}', headers=marked, method='POST')
        self.assertEqual(code, 200)
        self.ask('/api/game-update', data=b'{}', headers=marked, method='POST')
        kinds = [job['kind'] for job in ([self.queue.current['job']] if self.queue.current else []) + self.queue.jobs]
        self.assertEqual(kinds, ['update'])
        self.assertEqual(self.ask('/api/game-check', data=b'{}', headers=marked, method='POST')[0], 409)
        api = release.API
        self.addCleanup(setattr, release, 'API', api)
        self.assertEqual(self.ask('/api/app-update', data=b'{}', headers=marked, method='POST')[0], 409)     # the queue is busy
        release.API = 'http://127.0.0.1:9'                  # nothing answers: the check says so, nothing is changed
        self.assertEqual(self.ask('/api/app-check', data=b'{}', headers=marked, method='POST')[0], 502)

    def test_one_self_update_at_a_time_and_no_job_gets_in_meanwhile(self):
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        inside, go = threading.Event(), threading.Event()
        check, apply = release.check, release.apply
        self.addCleanup(setattr, release, 'check', check)
        self.addCleanup(setattr, release, 'apply', apply)
        release.check = lambda: {'current': '0.1.0', 'latest': '0.2.0', 'newer': True, 'how': 'zip'}

        def slow(found):                            # the update, held at its download until the test lets it go
            inside.set()
            go.wait(10)
            return {'version': '0.2.0', 'restart': 'by hand', 'files': 1}
        release.apply = slow
        answers = []
        first = threading.Thread(target=lambda: answers.append(self.ask('/api/app-update', data=b'{}', headers=marked, method='POST')[0]))
        first.start()
        self.assertTrue(inside.wait(10))
        self.assertEqual(self.ask('/api/app-update', data=b'{}', headers=marked, method='POST')[0], 409)
        for path, body in (('/api/generate', b'{"recipe":"large"}'), ('/api/load-test', b'{"name":"test-map-1"}'), ('/api/game-update', b'{}')):
            self.assertEqual(self.ask(path, data=body, headers=marked, method='POST')[0], 409, path)
        self.assertEqual(self.queue.jobs, [])
        go.set()
        first.join(10)
        # Updated: the panel still runs the code it had, so it stays held until it starts again.
        self.assertEqual((answers, self.queue.held), ([200], True))
        self.assertEqual(self.ask('/api/load-test', data=b'{"name":"test-map-1"}', headers=marked, method='POST')[0], 409)
        self.assertEqual(self.ask('/api/app-update', data=b'{}', headers=marked, method='POST')[0], 409)

    def test_no_job_starts_while_an_update_of_rustmapgen_is_unfinished(self):
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        release._keep_pending('0.2.0')
        for path, body in (('/api/generate', b'{"recipe":"large"}'), ('/api/load-test', b'{"name":"test-map-1"}'), ('/api/game-update', b'{}')):
            code, answer = self.ask(path, data=body, headers=marked, method='POST')
            self.assertEqual(code, 409, path)
            self.assertIn('did not finish', answer.decode(), path)
        self.assertEqual(self.queue.add([{'kind': 'load-test', 'name': 'test-map-1'}]), 0)
        maps = json.loads(self.ask('/api/maps')[1])
        self.assertEqual(maps['install']['update_pending'], '0.2.0')
        release._keep_pending(None)
        self.assertEqual(self.ask('/api/load-test', data=b'{"name":"test-map-1"}', headers=marked, method='POST')[0], 200)

    def test_settings_are_saved_beside_the_data_and_never_over_a_preset(self):
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        body = json.dumps(recipes.defaults()).encode()
        self.assertEqual(self.ask('/api/recipes/large', data=body, headers=marked, method='PUT')[0], 409)
        self.assertEqual(self.ask('/api/recipes/mine', data=body, headers=marked, method='PUT')[0], 200)
        self.assertTrue(os.path.isfile(paths.data('recipes', 'mine.json')))
        names = [r['name'] for r in json.loads(self.ask('/api/recipes')[1])['recipes']]
        self.assertIn('mine', names)
        self.assertEqual(self.ask('/api/recipes/mine', headers={'X-RustMapGen': '1'}, method='DELETE')[0], 200)

    def test_a_template_must_be_a_map(self):
        headers = {'X-RustMapGen': '1', 'Content-Type': 'application/octet-stream'}
        self.assertEqual(self.ask('/api/templates/notes.txt', data=b'abc', headers=headers, method='PUT')[0], 400)
        self.assertEqual(self.ask('/api/templates/outpost.map', data=b'not a map', headers=headers, method='PUT')[0], 400)
        self.assertFalse(os.listdir(paths.data('templates')))
        world = _world(1000, [('Decor', mapfile.prefab_id(RECYCLER), (13.0, 6.0, 0.0), (0.0, 30.0, 0.0)),
                              ('Dungeon', mapfile.prefab_id(MONUMENT), (3.0, 5.0, 0.0), (0.0, 0.0, 0.0))])
        path = os.path.join(self.folder.name, 'outpost.map')
        _write(path, world)
        with open(path, 'rb') as handle:
            blob = handle.read()
        with open(library.prefab_table(), 'w') as handle:
            json.dump({str(mapfile.prefab_id(MONUMENT)): MONUMENT, str(mapfile.prefab_id(RECYCLER)): RECYCLER}, handle)
        status, body = self.ask('/api/templates/my%20outpost.map', data=blob, headers=headers, method='PUT')
        self.assertEqual(status, 200, body)
        self.assertEqual((json.loads(body)['monument'], json.loads(body)['prefabs']), ('Outpost', 1))
        listed = json.loads(self.ask('/api/recipes')[1])['templates']
        self.assertEqual([(row['file'], row['prefab']) for row in listed], [('my outpost.map', 'compound')])
        # A second template around the same monument cannot be used with the first: both would be added.
        self.assertEqual(self.ask('/api/templates/second.map', data=blob, headers=headers, method='PUT')[0], 200)
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        status, body = self.ask('/api/generate', data=b'{"settings":{"size":3500,"templates":["my outpost.map","second.map"]}}', headers=marked, method='POST')
        self.assertEqual(status, 400)
        self.assertIn('two templates', json.loads(body)['error'])
        self.assertEqual(self.ask('/api/templates/second.map', headers={'X-RustMapGen': '1'}, method='DELETE')[0], 200)
        self.assertEqual(self.ask('/api/templates/my%20outpost.map', headers={'X-RustMapGen': '1'}, method='DELETE')[0], 200)

    def test_a_map_can_be_deleted(self):
        self.assertEqual(self.ask('/api/maps/test-map-1', headers={'X-RustMapGen': '1'}, method='DELETE')[0], 200)
        self.assertFalse(os.path.exists(paths.data('maps', 'test-map-1')))
        self.assertEqual(self.ask('/api/maps/test-map-1', headers={'X-RustMapGen': '1'}, method='DELETE')[0], 404)
        self.assertEqual(self.ask('/api/maps/..', headers={'X-RustMapGen': '1'}, method='DELETE')[0], 404)


    def test_the_3d_view_of_a_map_is_made_when_it_is_asked_for(self):
        _three_d_map(paths.data('maps', 'test-map-1', 'final.map'))
        self.assertEqual(self.ask('/3d/open?name=no-such-map')[0], 404)
        request = urllib.request.Request(self.base + '/3d/open?name=test-map-1&for=Mine')
        opener = urllib.request.build_opener(type('Stay', (urllib.request.HTTPRedirectHandler,), {'redirect_request': lambda *a, **k: None}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=10)
        key = server.three_d_key('test-map-1')
        self.assertEqual((caught.exception.code, caught.exception.headers['Location']), (302, f'/3d/?id={key}&for=Mine'))
        status, body = self.ask(f'/3d/data/{key}/3d_meta.json')
        self.assertEqual(status, 200)
        self.assertEqual((json.loads(body)['models'], json.loads(body)['placed'][0]['short']), ('none', 'compound'))
        self.assertTrue(os.path.isfile(paths.data('maps', 'test-map-1', '3d_heights.png')))
        self.assertEqual(self.ask(f'/3d/data/{key}/3d_trees.bin')[0], 200)
        self.assertEqual(self.ask(f'/3d/data/{key}/final.map')[0], 404)
        self.assertEqual(self.ask('/3d/data/0123456789abcdef/3d_meta.json')[0], 404)
        # Without models the page gets empty ones, and draws the ground.
        self.assertEqual(self.ask('/3d/data/3d-models/none/monuments/index.json'), (200, b'{}'))
        self.assertEqual(self.ask('/3d/data/3d-models/none/scenery/far.bin.gz')[0], 200)
        self.assertEqual(self.ask('/3d/data/3d-models/25902425-0123456789/monuments/index.json')[0], 404)
        self.assertEqual(self.ask('/3d/data/3d-models/..%2F..%2Fmaps/monuments/index.json')[0], 404)

    def test_the_3d_page_and_its_scripts_are_served(self):
        self.assertEqual(self.ask('/3d/')[0], 200)
        with urllib.request.urlopen(self.base + '/3d/viewer/vendor/three.module.min.js', timeout=10) as response:
            self.assertTrue(response.headers['Content-Type'].startswith('text/javascript'))
        self.assertEqual(self.ask('/3d/map3d.js')[0], 200)
        self.assertEqual(self.ask('/3d/viewer/vendor/three.LICENSE')[0], 404)
        self.assertEqual(self.ask('/3d/..%2Fserver.py')[0], 404)
        self.assertEqual(self.ask('/3d/index.html')[0], 404)

    def test_the_models_are_asked_for_from_the_panel_only(self):
        self.assertEqual(json.loads(self.ask('/api/status')[1])['three_d'], {'ready': False, 'game': False, 'failed': None})
        body = b'{}'
        self.assertEqual(self.ask('/api/models-3d', data=body, headers={'Content-Type': 'application/json'}, method='POST')[0], 403)
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        status, answer = self.ask('/api/models-3d', data=body, headers=marked, method='POST')
        self.assertEqual(status, 409)
        self.assertIn('install the game first', json.loads(answer)['error'])

    def test_maps_of_the_next_update_are_made_in_its_days_only_on_its_game_brought_up_first(self):
        from unittest import mock
        from rustmapgen import staging
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        body = json.dumps({'recipe': 'small', 'count': 2, 'game': 'staging'}).encode()
        with mock.patch.object(staging, 'refusal', return_value='maps for the next update are made from Sat 31 Oct'):
            status, answer = self.ask('/api/generate', data=body, headers=marked, method='POST')
            self.assertEqual((status, json.loads(answer)['error']), (409, 'maps for the next update are made from Sat 31 Oct'))
            self.assertEqual(self.ask('/api/load-test', data=b'{"name":"test-map-1","game":"staging"}', headers=marked, method='POST')[0], 409)
        self.assertEqual(self.ask('/api/generate', data=b'{"recipe":"small","game":"beta"}', headers=marked, method='POST')[0], 400)
        self.assertEqual(self.ask('/api/remove-staging', data=b'{}', headers=marked, method='POST')[0], 404)   # none there
        with mock.patch.object(staging, 'refusal', return_value=None):
            status, answer = self.ask('/api/generate', data=body, headers=marked, method='POST')
        self.assertEqual(status, 200)
        self.assertIn("2 maps queued for the next update, after its game is brought to Steam's newest build of its branch", json.loads(answer)['message'])
        state = self.queue.status()
        jobs = ([state['job']] if state['job'] else []) + state['queue']
        self.assertEqual([(job['kind'], job.get('game')) for job in jobs], [('update', 'staging'), ('generate', 'staging'), ('generate', 'staging')])
        self.assertIn('come with an update of RustMapGen', json.loads(self.ask('/api/status')[1])['staging']['reason'])
        os.makedirs(paths.data('..', 'game-next'))
        self.assertEqual(self.ask('/api/remove-staging', data=b'{}', headers=marked, method='POST')[0], 409)   # not while jobs wait

    def test_a_map_of_the_next_update_is_load_tested_on_the_public_game_brought_to_the_release_first(self):
        with open(paths.data('maps', 'test-map-1', 'map.json'), 'w') as handle:
            json.dump({'name': 'test-map-1', 'run': {'game_branch': 'release'}}, handle)
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        status, _ = self.ask('/api/load-test', data=b'{"name":"test-map-1","game":"public"}', headers=marked, method='POST')
        self.assertEqual(status, 200)
        state = self.queue.status()
        jobs = ([state['job']] if state['job'] else []) + state['queue']
        self.assertEqual([job['kind'] for job in jobs], ['update', 'load-test'])
        self.assertEqual(server.job_arguments({'kind': 'update', 'if_newer': True}), ['update', '--if-newer'])

    def test_a_load_test_on_the_next_update_is_on_steams_newest_release_build(self):
        from unittest import mock
        from rustmapgen import staging
        os.makedirs(paths.data('..', 'game-next'))
        open(runner.executable(staging.folder()), 'w').close()
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        with mock.patch.object(staging, 'refusal', return_value=None):
            status, _ = self.ask('/api/load-test', data=b'{"name":"test-map-1","game":"staging"}', headers=marked, method='POST')
        self.assertEqual(status, 200)
        state = self.queue.status()
        jobs = ([state['job']] if state['job'] else []) + state['queue']
        self.assertEqual([(job['kind'], job.get('game')) for job in jobs], [('update', 'staging'), ('load-test', 'staging')])

    def test_a_game_older_than_the_forced_wipe_is_brought_to_its_update_before_public_maps(self):
        from unittest import mock
        from rustmapgen import game
        marked = {'Content-Type': 'application/json', 'X-RustMapGen': '1'}
        body = json.dumps({'recipe': 'small', 'count': 2}).encode()
        with mock.patch.object(game, 'behind_forced_wipe', return_value=True):
            status, answer = self.ask('/api/generate', data=body, headers=marked, method='POST')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(answer)['message'], "2 maps queued, after the game is brought to the forced wipe's update, when it is out")
        state = self.queue.status()
        jobs = ([state['job']] if state['job'] else []) + state['queue']
        self.assertEqual([(job['kind'], job.get('forced_wipe')) for job in jobs], [('update', True), ('generate', None), ('generate', None)])
        self.assertEqual(server.job_arguments({'kind': 'update', 'forced_wipe': True}), ['update', '--forced-wipe'])
        with mock.patch.object(game, 'behind_forced_wipe', return_value=False):
            status, answer = self.ask('/api/generate', data=body, headers=marked, method='POST')
        self.assertEqual(json.loads(answer)['message'], '2 maps queued')


class PictureTest(unittest.TestCase):
    def test_the_sea_around_the_square_holds_the_oil_rigs(self):
        layer = np.array([[1, 2], [3, 4]])
        self.assertEqual(render._sample(layer, 4, 100.0, 0).tolist(), [[1, 1, 2, 2], [1, 1, 2, 2], [3, 3, 4, 4], [3, 3, 4, 4]])
        # 50 m of sea on each side of a 100 m square: the edge cells go on outside it.
        self.assertEqual(render._sample(layer, 4, 100.0, 50.0).tolist(), [[1, 1, 2, 2], [1, 1, 2, 2], [3, 3, 4, 4], [3, 3, 4, 4]])
        self.assertEqual(render._sample(np.arange(16).reshape(4, 4), 8, 100.0, 50.0)[0].tolist(), [0, 0, 0, 1, 2, 3, 3, 3])
        # The game puts a rig up to 500 m out from a 2000 m square: on the picture, with its dot where it stands.
        rig = {'prefab': 'oilrig_1', 'folder': 'offshore', 'label': 'Large Oil Rig', 'x': -1490.0, 'z': 0.0}
        drawn = render.with_names(Image.new('RGB', (320, 320)), 2000, [rig], render.MARGIN)
        x = (rig['x'] + 1000 + render.MARGIN) / (2000 + 2 * render.MARGIN) * 320
        self.assertGreater(x, 0)
        self.assertGreater(min(drawn.getpixel((round(x), 160))), 200)


    def test_every_dot_on_the_picture_has_its_name(self):
        # A crowd: a harbour with labs, lighthouses and wells around it, and a rig out at sea.
        crowd = [{'prefab': 'harbor_1', 'folder': 'harbor', 'label': 'Large Harbor', 'x': 0.0, 'z': 0.0}]
        crowd += [{'prefab': f'underwater_lab_{i}', 'folder': 'underwater_lab', 'label': 'Underwater Lab', 'x': 8.0 * i, 'z': 5.0 * i} for i in range(6)]
        crowd += [{'prefab': 'lighthouse', 'folder': 'lighthouse', 'label': 'Lighthouse', 'x': -6.0 * i, 'z': 4.0 * i} for i in range(6)]
        crowd += [{'prefab': 'oilrig_2', 'folder': 'offshore', 'label': 'Oil Rig', 'x': 1350.0, 'z': -1300.0}]
        places = render.name_places(900, 2000, crowd, render.MARGIN, lambda label, small: (0, 0, len(label) * (6 if small else 7), 10))
        self.assertEqual(len(places), len(crowd))
        for m, x, y, radius, small, box in places:
            self.assertTrue(0 < box[0] < box[2] < 900 and 0 < box[1] < box[3] < 900, m['label'])
            self.assertLess(min(abs(x - box[0]), abs(x - box[2]), abs(x - (box[0] + box[2]) / 2)) + min(abs(y - box[1]), abs(y - box[3])), 120, m['label'])
        self.assertEqual(sum(small for *_, small, box in places), 12)
        drawn = render.with_names(Image.new('RGB', (900, 900), (90, 90, 90)), 2000, crowd, render.MARGIN)
        self.assertEqual(drawn.size, (900, 900))


class LibraryTest(Isolated):
    """A map the generator brought back, end to end without the game: merge, describe, check, draw."""

    def test_a_map_is_merged_described_checked_and_drawn(self):
        monument, recycler = mapfile.prefab_id(MONUMENT), mapfile.prefab_id(RECYCLER)
        with open(library.prefab_table(), 'w') as handle:
            json.dump({str(monument): MONUMENT, str(recycler): RECYCLER}, handle)
        _write(paths.data('templates', 'outpost.map'), _world(1000, [('Decor', recycler, (13.0, 6.0, 0.0), (0.0, 30.0, 0.0)),
                                                                       ('Dungeon', monument, (3.0, 5.0, 0.0), (0.0, 0.0, 0.0))]))
        folder = paths.data('maps', 'test-3500-1')
        os.makedirs(folder)
        _write(os.path.join(folder, 'raw.map'), _world(3500, [('Monument', monument, (10.0, 9.2, 20.0), (0.0, 90.0, 0.0))], res=17))      # on the ground: 9.16 m
        recipe = dict(recipes.defaults(), name='test', title='Test', monuments={'compound': 'want'}, templates=['outpost.map'])
        with open(os.path.join(folder, 'recipe.json'), 'w') as handle:
            json.dump(recipe, handle)
        with open(os.path.join(folder, 'run.json'), 'w') as handle:
            json.dump({'seed': 1, 'recipe': 'test', 'state': 'done', 'wall_seconds': 5}, handle)
        summary = library.add('test-3500-1')
        self.assertEqual(summary['failed'], [])
        entry = library.entry('test-3500-1')
        self.assertEqual(entry['merge'][0]['placed'], 1)
        self.assertEqual(len(mapfile.MapFile(os.path.join(folder, 'final.map')).id), 2)
        for file in ('terrain.webp', 'thumb.webp', 'map_labels.webp', 'card.json'):
            self.assertTrue(os.path.isfile(os.path.join(folder, file)), file)
        self.assertEqual(entry['margin_m'], render.MARGIN)
        self.assertEqual([card['name'] for card in library.cards()], ['test-3500-1'])
        # Described again, the file a server would hold stays the same bytes.
        before = library._sha256(os.path.join(folder, 'final.map'))
        library.add('test-3500-1')
        self.assertEqual(library._sha256(os.path.join(folder, 'final.map')), before)
        # A template gone or changed since: the map keeps the record of its own merge.
        merged = library.entry('test-3500-1')['merge'][0]
        for change in ('changed', 'gone'):
            if change == 'changed':
                _write(paths.data('templates', 'outpost.map'), _world(1000, [('Dungeon', monument, (3.0, 5.0, 0.0), (0.0, 0.0, 0.0))]))
            else:
                os.unlink(paths.data('templates', 'outpost.map'))
            self.assertEqual(library.add('test-3500-1')['failed'], [], change)
            row = library.entry('test-3500-1')['merge'][0]
            self.assertEqual((row['placed'], row['template_sha256']), (merged['placed'], merged['template_sha256']), change)
            self.assertIn('kept', row)
        # The record is of those bytes: with final.map changed, it cannot vouch for the new file.
        with open(os.path.join(folder, 'raw.map'), 'rb') as source, open(os.path.join(folder, 'final.map'), 'wb') as target:
            target.write(source.read())
        self.assertIn('Templates merged', ' '.join(library.add('test-3500-1')['failed']))
        # With no record of the merge either (map.json lost before it was written), it fails, and says why.
        os.unlink(os.path.join(folder, 'map.json'))
        self.assertIn('cannot be checked: outpost.map', ' '.join(library.add('test-3500-1')['failed']))

    def test_a_blocked_prefab_a_template_brings_in_is_found(self):
        monument, recycler = mapfile.prefab_id(MONUMENT), mapfile.prefab_id(RECYCLER)
        with open(library.prefab_table(), 'w') as handle:
            json.dump({str(monument): MONUMENT, str(recycler): RECYCLER}, handle)
        _write(paths.data('templates', 'outpost.map'), _world(1000, [('Decor', recycler, (13.0, 6.0, 0.0), (0.0, 30.0, 0.0)),
                                                                       ('Dungeon', monument, (3.0, 5.0, 0.0), (0.0, 0.0, 0.0))]))
        folder = paths.data('maps', 'test-3500-2')
        os.makedirs(folder)
        _write(os.path.join(folder, 'raw.map'), _world(3500, [('Monument', monument, (10.0, 9.2, 20.0), (0.0, 90.0, 0.0))], res=17))
        recipe = dict(recipes.defaults(), name='test', templates=['outpost.map'], block_extra=['recycler_static'])
        with open(os.path.join(folder, 'recipe.json'), 'w') as handle:
            json.dump(recipe, handle)
        with open(os.path.join(folder, 'run.json'), 'w') as handle:
            json.dump({'seed': 1, 'recipe': 'test', 'state': 'done'}, handle)
        failed = library.add('test-3500-2')['failed']
        self.assertEqual(failed, ['Nothing blocked is on the map: present: recycler_static (1)'])


class GameFolderTest(Isolated):
    """The game folder is the generator's own: nothing is written into a server of someone else's."""

    def make(self, *files):
        for name in files:
            path = paths.game(name)
            if name.endswith('/'):
                os.makedirs(path, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, 'w').close()

    def test_an_empty_folder_is_claimed_and_locked_inside(self):
        with runner.lock():
            self.assertTrue(os.path.isfile(paths.game(runner.MARKER)))
            self.assertTrue(os.path.isfile(paths.game(runner.LOCK_FILE)))
        runner.claim()                                  # again: it is ours

    def test_a_server_of_someone_elses_is_refused_and_left_as_it_was(self):
        for files in (['RustDedicated', 'server/myserver/cfg/'], ['RustDedicated', 'HarmonyMods/Other.dll'],
                      ['RustDedicated', 'carbon/'], ['RustDedicated', 'oxide/'], ['RustDedicated', 'somefile.txt']):
            folder = tempfile.mkdtemp(dir=self.folder.name)
            os.environ['RUSTMAPGEN_GAME'] = folder
            self.make(*files)
            before = sorted(os.listdir(folder))
            with self.assertRaises(RuntimeError, msg=files):
                with runner.lock():
                    pass
            with self.assertRaises(RuntimeError):
                runner.install_mod()
            self.assertEqual(sorted(os.listdir(folder)), before, files)
            self.assertFalse(os.path.exists(os.path.join(folder, 'HarmonyMods', runner.MOD_FILE)))

    def test_a_folder_claimed_once_is_refused_when_a_server_moves_in(self):
        runner.claim()
        self.make('server/myserver/')
        with self.assertRaises(RuntimeError):
            runner.claim()

    def test_an_install_from_before_the_marker_is_taken(self):
        self.make('RustDedicated', 'HarmonyMods/' + runner.MOD_FILE, 'server/' + paths.IDENTITY + '/')
        runner.claim()
        self.assertTrue(os.path.isfile(paths.game(runner.MARKER)))

    def test_an_install_made_before_the_rename_is_taken(self):
        self.make('RustDedicated', 'server/rustmap/', 'rustmap-game.txt')
        runner.claim()
        self.assertTrue(os.path.isfile(paths.game(runner.MARKER)))

    def test_different_data_folders_share_the_lock_of_one_game(self):
        if os.name == 'nt':
            self.skipTest('the probe is a POSIX process')
        import subprocess
        with runner.lock():
            other = dict(os.environ, RUSTMAPGEN_DATA=os.path.join(self.folder.name, 'other-data'))
            probe = subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); from rustmapgen import runner\n'
                                    'try:\n with runner.lock(): print("got it")\nexcept runner.Busy: print("busy")', ROOT],
                                   capture_output=True, text=True, env=other)
            self.assertEqual(probe.stdout.strip(), 'busy')

    def test_a_relative_folder_is_made_whole(self):
        os.environ['RUSTMAPGEN_GAME'] = 'some-game'
        self.assertEqual(paths.game('x'), os.path.join(os.getcwd(), 'some-game', 'x'))

    def test_steamcmd_cut_short_is_not_taken_for_whole(self):
        from rustmapgen import game
        os.environ['RUSTMAPGEN_STEAMCMD'] = os.path.join(self.folder.name, 'steamcmd')
        self.addCleanup(os.environ.pop, 'RUSTMAPGEN_STEAMCMD', None)
        os.makedirs(paths.steamcmd('linux32'))
        open(game.steamcmd_program(), 'w').close()
        if os.name != 'nt':
            self.assertFalse(game._steamcmd_whole())
            open(paths.steamcmd('linux32', 'steamcmd'), 'w').close()
        self.assertTrue(game._steamcmd_whole())


@unittest.skipIf(os.name == 'nt', 'the stand-in steamcmd is a shell script')
class BranchTest(Isolated):
    """An update names its branch, and moves an install from one branch to another."""

    STEAMCMD = '''#!/bin/sh
        while [ "$1" != "+force_install_dir" ]; do shift; done; DIR="$2"
        if [ -f "$DIR/steamapps/appmanifest_258550.acf" ]; then echo present > "$DIR/../manifest-seen"; else echo absent > "$DIR/../manifest-seen"; fi
        while [ "$1" != "-beta" ]; do shift; done; BRANCH="$2"
        mkdir -p "$DIR/steamapps"
        printf '"AppState"\\n{\\n\\t"StateFlags"\\t\\t"4"\\n\\t"buildid"\\t\\t"200"\\n\\t"UserConfig"\\n\\t{\\n\\t\\t"BetaKey"\\t\\t"%s"\\n\\t}\\n}\\n' "$BRANCH" > "$DIR/steamapps/appmanifest_258550.acf"
        touch "$DIR/RustDedicated"
        echo "Success! App '258550' fully installed."
        '''

    def setUp(self):
        super().setUp()
        from rustmapgen import game
        self.game = game
        os.environ['RUSTMAPGEN_STEAMCMD'] = os.path.join(self.folder.name, 'steamcmd')
        self.addCleanup(os.environ.pop, 'RUSTMAPGEN_STEAMCMD', None)
        os.makedirs(paths.steamcmd('linux32'))
        open(paths.steamcmd('linux32', 'steamcmd'), 'w').close()
        with open(game.steamcmd_program(), 'w') as handle:
            handle.write(textwrap.dedent(self.STEAMCMD))
        os.chmod(game.steamcmd_program(), 0o755)
        runner.claim()
        os.makedirs(paths.game('steamapps'))

    def installed_on(self, branch):
        with open(paths.game('steamapps', 'appmanifest_258550.acf'), 'w') as handle:
            handle.write('"AppState"\n{\n\t"StateFlags"\t\t"4"\n\t"buildid"\t\t"100"\n\t"UserConfig"\n\t{\n\t\t"BetaKey"\t\t"%s"\n\t}\n}\n' % branch)

    def seen(self):
        with open(os.path.join(self.folder.name, 'manifest-seen')) as handle:
            return handle.read().strip()

    def test_staging_goes_back_to_public_with_its_record_put_aside(self):
        self.installed_on('staging')
        self.game.update_game(say=lambda *a, **k: None)
        self.assertEqual(self.seen(), 'absent')
        self.assertEqual(runner.game_branch(), 'public')
        self.assertEqual(sorted(os.listdir(paths.game('steamapps'))), ['appmanifest_258550.acf'])

    def test_an_update_cut_short_blocks_maps_until_one_finishes(self):
        self.installed_on('staging')
        good = textwrap.dedent(self.STEAMCMD)
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write('#!/bin/sh\necho "ERROR! Failed to install app 258550 (Disk write failure)"\nexit 8\n')
        with self.assertRaises(RuntimeError):
            self.game.update_game(say=lambda *a, **k: None)
        self.assertTrue(os.path.exists(paths.game(runner.UPDATING)))
        self.assertEqual(len([name for name in os.listdir(paths.game('steamapps')) if '.staging-' in name]), 1)
        with self.assertRaises(RuntimeError):
            runner.run(paths.data('jobs'), [], None, say=lambda *a, **k: None)
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write(good)
        self.game.update_game(say=lambda *a, **k: None)
        self.assertFalse(os.path.exists(paths.game(runner.UPDATING)))
        self.assertEqual(runner.game_branch(), 'public')

    def test_an_update_that_exits_with_an_error_is_not_taken_for_finished(self):
        self.installed_on('public')
        with open(paths.game('RustDedicated'), 'w') as handle:
            handle.write('')
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write("#!/bin/sh\necho \"Success! App '258550' fully installed.\"\nexit 8\n")
        with self.assertRaises(RuntimeError):
            self.game.update_game(say=lambda *a, **k: None)
        self.assertTrue(os.path.exists(paths.game(runner.UPDATING)))

    def test_an_update_steam_did_not_record_as_whole_is_not_taken_for_finished(self):
        self.installed_on('public')
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write(textwrap.dedent(self.STEAMCMD).replace('"StateFlags"\\t\\t"4"', '"StateFlags"\\t\\t"1158"'))
        with self.assertRaises(RuntimeError):
            self.game.update_game(say=lambda *a, **k: None)
        self.assertEqual(runner.game_state(), '1158')
        self.assertTrue(os.path.exists(paths.game(runner.UPDATING)))

    def test_an_update_stopped_by_a_signal_stops_steamcmd_and_what_it_started(self):
        import signal
        import subprocess
        child = os.path.join(self.folder.name, 'child-pid')
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write(f'#!/bin/sh\nsleep 60 &\necho $! > {child}\nwait\n')
        code = ('import sys; sys.path.insert(0, sys.argv[1]); from rustmapgen import game, runner\n'
                'runner.stop_on_signals()\nwith runner.lock():\n game.update_game(say=lambda *a, **k: None)')
        command = subprocess.Popen([sys.executable, '-c', code, ROOT], env=dict(os.environ),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if os.path.exists(child) and os.path.getsize(child):
                break
            time.sleep(0.1)
        with open(child) as handle:
            pid = int(handle.read())
        command.send_signal(signal.SIGTERM)
        command.wait(timeout=60)
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_steamcmd_s_progress_is_shown_while_it_works(self):
        self.installed_on('public')
        # The stand-in waits until its first progress line was shown: read only at its end, it would give up.
        lines = ''.join(f'echo " Update state (0x61) downloading, progress: {p:.2f} ({int(p * 61117621)} / 6111762123)"\n'
                        for p in (0, 5, 12.5, 50, 99.9))
        wait = 'i=0; while [ ! -f "$DIR/../shown" ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done; [ -f "$DIR/../shown" ] || exit 9\n'
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write(textwrap.dedent(self.STEAMCMD).replace('echo "Success!', lines.replace('\n', '\n' + wait, 1)
                                                                 + 'echo " Update state (0x101) committing, progress: 40.00 (2444704849 / 6111762123)"\necho "Success!'))
        said = []

        def say(text, **_):
            said.append(text)
            if 'downloading' in text:
                open(os.path.join(self.folder.name, 'shown'), 'w').close()
        self.game.update_game(say=say)
        self.assertEqual([text for text in said if '%' in text],
                         ['  downloading the game: 0 % (0.0 of 6.1 GB)', '  downloading the game: 12 % (0.8 of 6.1 GB)',
                          '  downloading the game: 50 % (3.1 of 6.1 GB)', '  downloading the game: 99 % (6.1 of 6.1 GB)',
                          '  writing the game files: 40 % (2.4 of 6.1 GB)'])

    def test_a_finished_step_is_shown_full_on_a_terminal(self):
        from unittest import mock

        class Screen(io.StringIO):
            def isatty(self):
                return True
        for done, last in ((True, 100), (False, 18)):
            screen = Screen()
            with mock.patch.object(sys, 'stdout', screen):
                seen, end = self.game._progress(print)
                for line in (' Update state (0x61) downloading, progress: 99.90 (6105650360 / 6111762123)',
                             ' Update state (0x5) verifying update, progress: 18.00 (1100117182 / 6111762123)'):
                    seen(line)
                end(done)
            shown = [part.split('\r')[-1].rstrip() for part in screen.getvalue().split('\n') if part.strip()]
            # steamcmd's last word on the download was 99.9 %: once it went on to checking, the download is done.
            self.assertEqual(shown[0], '  downloading the game               [####################] 100 %  6.1 of 6.1 GB')
            self.assertIn(f'{last:3d} %', shown[1])     # the last step: full once steamcmd ended well, as it was otherwise

    def test_steam_is_asked_for_the_builds_of_both_branches(self):
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write('#!/bin/sh\nprintf \'\\033[0m\\t"branches"\\n\\t{\\n\\t\\t"public"\\n\\t\\t{\\n\\t\\t\\t"buildid"\\t\\t"25902425"\\n\\t\\t}\\n'
                         '\\t\\t"aux02"\\n\\t\\t{\\n\\t\\t\\t"buildid"\\t\\t"1"\\n\\t\\t}\\n\\t\\t"staging"\\n\\t\\t{\\n\\t\\t\\t"buildid"\\t\\t"25999658"\\n\\t\\t}\\n\'\n')
        self.assertEqual(self.game.steam_builds(), {'public': '25902425', 'staging': '25999658'})
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write('#!/bin/sh\necho "ERROR! Not logged on."\nexit 5\n')
        with self.assertRaises(RuntimeError):
            self.game.steam_builds()

    def test_steamcmd_is_stopped_even_when_the_progress_display_fails(self):
        import subprocess
        child = os.path.join(self.folder.name, 'child-pid')
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write(f'#!/bin/sh\necho " Update state (0x61) downloading, progress: 1.00 (1 / 100)"\nsleep 60 &\necho $! > {child}\nwait\n')

        def broken(say):                            # a terminal gone: showing progress fails, and so does ending it
            def seen(line):
                for _ in range(100):
                    if os.path.exists(child) and os.path.getsize(child):
                        break
                    time.sleep(0.05)
                raise OSError('the terminal is gone')

            def end(done=False):
                raise OSError('the terminal is gone')
            return seen, end
        progress = self.game._progress
        self.addCleanup(setattr, self.game, '_progress', progress)
        self.game._progress = broken
        with self.assertRaises(OSError):
            self.game._steamcmd([self.game.steamcmd_program()], say=lambda *a, **k: None)
        with open(child) as handle:
            pid = int(handle.read())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_what_steamcmd_leaves_running_when_it_ends_is_stopped(self):
        child = os.path.join(self.folder.name, 'child-pid')
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write(f'#!/bin/sh\nsleep 60 >/dev/null 2>&1 &\necho $! > {child}\nexit 0\n')
        self.game._steamcmd([self.game.steamcmd_program()], say=lambda *a, **k: None)
        with open(child) as handle:
            pid = int(handle.read())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_a_steam_check_that_fails_or_hangs_is_not_believed(self):
        branches = 'printf \'"branches"\\n{\\n"public"\\n{\\n"buildid" "25902425"\\n}\\n\'\n'
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write('#!/bin/sh\n' + branches + 'exit 8\n')
        with self.assertRaises(RuntimeError):
            self.game.steam_builds()
        child = os.path.join(self.folder.name, 'child-pid')
        with open(self.game.steamcmd_program(), 'w') as handle:
            handle.write(f'#!/bin/sh\nsleep 60 &\necho $! > {child}\nwait\n')
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            self.game.steam_builds(timeout=1)
        self.assertLess(time.monotonic() - started, 15)
        with open(child) as handle:
            pid = int(handle.read())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_an_update_written_to_the_window_counts_only_when_steam_has_that_build(self):
        # On Windows steamcmd writes to the window and its word cannot be read (out is None): a pass that only
        # updated steamcmd ends with 0 too. The game's record must show the build Steam has.
        self.installed_on('public')
        open(paths.game('RustDedicated'), 'w').close()
        steamcmd, builds = self.game._steamcmd, self.game.steam_builds
        self.addCleanup(setattr, self.game, '_steamcmd', steamcmd)
        self.addCleanup(setattr, self.game, 'steam_builds', builds)
        passes = []
        self.game._steamcmd = lambda command, say=print, timeout=None: passes.append(1) or (0, None, '')
        self.game.steam_builds = lambda timeout=120: {'public': '200', 'staging': '300'}
        with self.assertRaises(RuntimeError):
            self.game.update_game(say=lambda *a, **k: None)
        self.assertEqual(len(passes), 3)
        self.assertTrue(os.path.exists(paths.game(runner.UPDATING)))
        with open(paths.game('steamapps', 'appmanifest_258550.acf')) as handle:
            record = handle.read()
        with open(paths.game('steamapps', 'appmanifest_258550.acf'), 'w') as handle:
            handle.write(record.replace('"100"', '"200"'))
        self.assertEqual(self.game.update_game(say=lambda *a, **k: None), '200')
        self.assertFalse(os.path.exists(paths.game(runner.UPDATING)))

    def test_an_update_on_the_same_branch_keeps_its_record(self):
        self.installed_on('public')
        self.game.update_game(say=lambda *a, **k: None)
        self.assertEqual((self.seen(), runner.game_build()), ('present', '200'))


class SelfUpdateTest(unittest.TestCase):
    """RustMapGen's own update, from a stand-in of the repository's tag list and release ZIP."""

    def setUp(self):
        import http.server
        self.copy = tempfile.TemporaryDirectory()
        self.root, self.api, self.launcher = paths.ROOT, release.API, os.environ.pop('RUSTMAPGEN_LAUNCHER', None)
        self.data = os.environ.get('RUSTMAPGEN_DATA')
        # The stand-in copy is 0.1.0 and its newest tag 0.2.0, whatever version this code is.
        self.addCleanup(setattr, release, '__version__', release.__version__)
        release.__version__ = '0.1.0'
        paths.ROOT = self.copy.name
        os.environ['RUSTMAPGEN_DATA'] = os.path.join(self.copy.name, 'data')
        # pip is not run in the tests: each call is counted, and may be told to fail.
        self.pip_calls, self.pip_fails, self.install = [], 0, release.install_requirements

        def pip(text):
            self.pip_calls.append(text)
            if self.pip_fails:
                self.pip_fails -= 1
                raise RuntimeError('the Python packages of the release did not install, nothing was changed')
        release.install_requirements = pip
        for name, body in (('rustmapgen.sh', 'launcher\n'), ('rustmapgen.cmd', 'launcher\r\n'), ('requirements.txt', 'numpy\n'),
                           ('rustmapgen/__init__.py', "__version__ = '0.1.0'\n"), ('data/settings.json', '{"mine": 1}')):
            os.makedirs(os.path.dirname(os.path.join(self.copy.name, name)), exist_ok=True)
            with open(os.path.join(self.copy.name, name), 'w', newline='') as handle:
                handle.write(body)
        self.files = {'rustmapgen-0.2.0/rustmapgen/__init__.py': "__version__ = '0.2.0'\n", 'rustmapgen-0.2.0/rustmapgen/new.py': 'x = 1\n',
                      'rustmapgen-0.2.0/rustmapgen.sh': 'launcher\n', 'rustmapgen-0.2.0/rustmapgen.cmd': 'launcher\r\n',
                      'rustmapgen-0.2.0/requirements.txt': 'numpy\n'}
        self.tags = None
        test = self

        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if test.tags == 404:
                    self.send_error(404)
                    return
                if self.path.startswith('/tags'):
                    blob = json.dumps(test.tags if test.tags is not None else
                                      [{'name': 'v0.1.0', 'zipball_url': 'x'}, {'name': 'v0.2.0', 'zipball_url': test.base + '/zip'}, {'name': 'nightly'}]).encode()
                else:
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, 'w') as archive:
                        for name, body in test.files.items():
                            archive.writestr(name, body)
                    blob = buffer.getvalue()
                self.send_response(200)
                self.send_header('Content-Length', str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        release.API = self.base

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        paths.ROOT, release.API, release.install_requirements = self.root, self.api, self.install
        if self.launcher is not None:
            os.environ['RUSTMAPGEN_LAUNCHER'] = self.launcher
        if self.data is None:
            os.environ.pop('RUSTMAPGEN_DATA', None)
        else:
            os.environ['RUSTMAPGEN_DATA'] = self.data
        self.copy.cleanup()

    def read(self, name):
        with open(os.path.join(self.copy.name, name), newline='') as handle:
            return handle.read()

    def test_the_newest_tag_is_found_and_put_in_place_over_a_zip_copy(self):
        found = release.check()
        self.assertEqual((found['current'], found['latest'], found['newer'], found['how']), (release.__version__, '0.2.0', True, 'zip'))
        self.assertEqual(found['notes'], 'https://github.com/S1gil0/rustmapgen/blob/v0.2.0/CHANGELOG.md')
        os.environ['RUSTMAPGEN_LAUNCHER'] = '1'
        self.addCleanup(os.environ.pop, 'RUSTMAPGEN_LAUNCHER', None)
        done = release.apply(found)
        self.assertEqual(done, {'version': '0.2.0', 'restart': 'auto', 'files': 2})       # the launchers were the same bytes
        self.assertEqual(self.read('rustmapgen/__init__.py'), "__version__ = '0.2.0'\n")
        self.assertEqual(self.read('data/settings.json'), '{"mine": 1}')
        self.assertEqual(self.read('rustmapgen/new.py'), 'x = 1\n')

    def test_a_release_that_would_write_this_copys_own_or_outside_it_writes_nothing(self):
        for bad in ('rustmapgen-0.2.0/data/settings.json', 'rustmapgen-0.2.0/../escaped.py', 'rustmapgen-0.2.0/game/RustDedicated'):
            self.files[bad] = 'nope'
            with self.assertRaises(RuntimeError):
                release.apply(release.check())
            del self.files[bad]
            self.assertEqual(self.read('rustmapgen/__init__.py'), "__version__ = '0.1.0'\n", bad)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.copy.name), 'escaped.py')))

    def test_a_changed_launcher_or_no_launcher_means_a_restart_by_hand(self):
        os.environ['RUSTMAPGEN_LAUNCHER'] = '1'
        self.addCleanup(os.environ.pop, 'RUSTMAPGEN_LAUNCHER', None)
        self.files['rustmapgen-0.2.0/rustmapgen.cmd'] = 'launcher, changed\r\n'
        self.assertEqual(release.apply(release.check())['restart'], 'by hand')
        os.environ.pop('RUSTMAPGEN_LAUNCHER')
        with open(os.path.join(self.copy.name, 'rustmapgen/__init__.py'), 'w') as handle:
            handle.write("__version__ = '0.1.0'\n")
        self.files['rustmapgen-0.2.0/rustmapgen.cmd'] = 'launcher, changed\r\n'
        self.assertEqual(release.apply(release.check())['restart'], 'by hand')

    def test_the_panel_ends_for_its_launcher_once_it_has_updated_itself(self):
        from http.server import ThreadingHTTPServer
        os.environ['RUSTMAPGEN_LAUNCHER'] = '1'
        self.addCleanup(os.environ.pop, 'RUSTMAPGEN_LAUNCHER', None)
        ended = threading.Event()
        control = {'server': type('Stand', (), {'shutdown': lambda self: ended.set()})(), 'exit': None}
        panel = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(server.Queue(), set(server.LOCAL_HOSTS), control=control))
        threading.Thread(target=panel.serve_forever, daemon=True).start()
        self.addCleanup(panel.server_close)
        self.addCleanup(panel.shutdown)
        request = urllib.request.Request(f'http://127.0.0.1:{panel.server_address[1]}/api/app-update', data=b'{}', method='POST',
                                         headers={'Content-Type': 'application/json', 'X-RustMapGen': '1'})
        with urllib.request.urlopen(request, timeout=30) as response:
            self.assertEqual(json.load(response)['restart'], 'auto')
        self.assertTrue(ended.wait(5))
        self.assertEqual(control['exit'], release.RESTART)

    def test_an_update_that_stops_half_way_is_finished_by_the_next_one(self):
        self.files['rustmapgen-0.2.0/rustmapgen/third.py'] = 'z = 3\n'
        real = os.replace
        for fail_at in (1, 2, 3):                    # early, in the middle, at the version file
            with open(os.path.join(self.copy.name, 'rustmapgen/__init__.py'), 'w') as handle:
                handle.write("__version__ = '0.1.0'\n")
            calls = []

            def flaky(source, target):
                if source.endswith('.partial') and not source.endswith('update-pending.json.partial'):
                    calls.append(target)
                    if len(calls) == fail_at:
                        raise OSError('disk full')
                return real(source, target)
            os.replace = flaky
            try:
                with self.assertRaises(RuntimeError):
                    release.apply(release.check())
            finally:
                os.replace = real
            self.assertEqual(self.read('rustmapgen/__init__.py'), "__version__ = '0.1.0'\n", fail_at)
            found = release.check()
            self.assertEqual((found['unfinished'], found['newer']), ('0.2.0', True), fail_at)
            release.apply(found)
            self.assertEqual((self.read('rustmapgen/__init__.py'), self.read('rustmapgen/new.py'), self.read('rustmapgen/third.py')),
                             ("__version__ = '0.2.0'\n", 'x = 1\n', 'z = 3\n'), fail_at)
            self.assertIsNone(release.pending())
            for name in ('rustmapgen/new.py', 'rustmapgen/third.py'):
                os.unlink(os.path.join(self.copy.name, name))
        # Its version file already says 0.2.0 when the record is there: the same release is offered again.
        release._keep_pending('0.2.0')
        self.assertEqual(release.check()['newer'], True)
        release.apply(release.check())
        self.assertNotIn('unfinished', release.check())               # done; a new panel reads 0.2.0 from the file

    def test_finish_update_completes_an_unfinished_one_and_one_update_at_a_time_per_folder(self):
        release._keep_pending('0.2.0')
        said = []
        done = release.finish(say=lambda text, **_: said.append(text))
        self.assertEqual((done['version'], self.read('rustmapgen/__init__.py'), release.pending()), ('0.2.0', "__version__ = '0.2.0'\n", None))
        self.assertEqual(release.finish(say=lambda text, **_: said.append(text)), None)
        self.assertIn('no update of RustMapGen is unfinished', said)
        with open(os.path.join(self.copy.name, 'rustmapgen/__init__.py'), 'w') as handle:
            handle.write("__version__ = '0.1.0'\n")
        with release._install_lock():
            with self.assertRaises(RuntimeError):            # another panel or command updating this folder
                release.apply(release.check())

    def test_a_failed_package_install_changes_nothing_and_a_retry_installs_them(self):
        self.pip_fails = 1
        with self.assertRaises(RuntimeError):
            release.apply(release.check())
        self.assertEqual(self.read('rustmapgen/__init__.py'), "__version__ = '0.1.0'\n")
        self.assertIsNone(release.pending())
        release.apply(release.check())
        self.assertEqual(len(self.pip_calls), 2)                     # the retry installed them again
        self.assertEqual(self.read('rustmapgen/__init__.py'), "__version__ = '0.2.0'\n")

    def test_a_damaged_release_or_one_that_reaches_this_copys_own_writes_nothing(self):
        good = dict(self.files)
        for bad in ({'rustmapgen-0.2.0/./data/settings.json': 'nope'}, {'rustmapgen-0.2.0/docs/x.json': 'nope'}):
            if 'docs' in next(iter(bad)):
                os.symlink(os.path.join(self.copy.name, 'data'), os.path.join(self.copy.name, 'docs'))
            self.files = dict(good, **bad)
            with self.assertRaises(RuntimeError):
                release.apply(release.check())
            self.assertEqual((self.read('rustmapgen/__init__.py'), self.read('data/settings.json')), ("__version__ = '0.1.0'\n", '{"mine": 1}'))
            self.assertEqual(self.pip_calls, [])
        self.assertFalse(release.ORIGIN.match('https://notgithub.com/S1gil0/rustmapgen.git'))
        for origin in ('https://github.com/S1gil0/rustmapgen.git', 'git@github.com:S1gil0/rustmapgen.git', 'https://github.com/S1gil0/rustmapgen'):
            self.assertTrue(release.ORIGIN.match(origin), origin)

    def test_no_release_yet_and_a_development_clone(self):
        for nothing in ([], 404):                           # no tag yet, or a repository not public yet
            self.tags = nothing
            self.assertEqual((release.check()['latest'], release.check()['newer']), (None, False))
        os.makedirs(os.path.join(self.copy.name, '.git'))
        import subprocess
        if subprocess.run(['git', 'init', '-q', self.copy.name]).returncode == 0:
            subprocess.run(['git', '-C', self.copy.name, 'remote', 'add', 'origin', 'git@github.com:someone/a-fork.git'])
            self.assertEqual(release.how(), 'development')
            subprocess.run(['git', '-C', self.copy.name, 'remote', 'set-url', 'origin', 'https://github.com/S1gil0/rustmapgen.git'])
            self.assertEqual(release.how(), 'git')
            self.tags = None
            with self.assertRaises(RuntimeError):            # a clone of another repository is never written by the panel
                release.apply(dict(release.check(), how='development'))


class FinishUpdateCommandTest(Isolated):
    """finish-update needs nothing of RustMapGen but its release module: it works when the rest cannot load."""

    def test_it_runs_when_another_module_is_broken_and_game_work_waits_for_it(self):
        import shutil
        import subprocess
        copy = os.path.join(self.folder.name, 'copy')
        shutil.copytree(os.path.join(ROOT, 'rustmapgen'), os.path.join(copy, 'rustmapgen'), ignore=shutil.ignore_patterns('__pycache__'))
        with open(os.path.join(copy, 'rustmapgen', 'server.py'), 'w') as handle:
            handle.write('this is not python\n')           # half of an update: a module that cannot load
        env = dict(os.environ, PYTHONPATH=copy)

        def run(*arguments):
            return subprocess.run([sys.executable, '-m', 'rustmapgen'] + list(arguments), cwd=copy, env=env, capture_output=True, text=True, timeout=60)
        finished = run('finish-update')
        self.assertEqual(finished.returncode, 0, finished.stderr)
        self.assertIn('no update of RustMapGen is unfinished', finished.stdout)
        os.makedirs(paths.data(), exist_ok=True)
        with open(paths.data('update-pending.json'), 'w') as handle:
            json.dump({'version': '0.2.0', 'started': 'now'}, handle)
        refused = run('generate', '--recipe', 'small')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('finish-update', refused.stderr)


class ModChoiceTest(Isolated):
    """A mod built here is used only while it was built from the source as it is now."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, paths, 'ROOT', paths.ROOT)
        paths.ROOT = os.path.join(self.folder.name, 'code')
        for folder in ('prebuilt', 'build'):
            os.makedirs(os.path.join(paths.ROOT, 'mod', folder))
            with open(os.path.join(paths.ROOT, 'mod', folder, runner.MOD_FILE), 'w') as handle:
                handle.write(folder)
        self.source('the source')

    def source(self, text):
        with open(os.path.join(paths.ROOT, 'mod', 'RustMapGen.cs'), 'w') as handle:
            handle.write(text)

    def built_from(self, sha):
        dll = runner._digest(os.path.join(paths.ROOT, 'mod', 'build', runner.MOD_FILE))
        with open(os.path.join(paths.ROOT, 'mod', 'build', 'build.json'), 'w') as handle:
            json.dump({'source_sha256': sha, 'sha256': dll, 'game_build': '1'}, handle)

    def test_a_build_of_this_source_is_used(self):
        self.built_from(runner.source_sha256())
        self.assertEqual(os.path.basename(os.path.dirname(runner.mod_source())), 'build')
        self.assertIn('built on this computer', runner.describe_mod())

    def test_a_build_of_older_source_or_of_no_known_source_is_not(self):
        self.built_from(runner.source_sha256())
        self.source('the source, changed')
        self.assertEqual(os.path.basename(os.path.dirname(runner.mod_source())), 'prebuilt')
        self.source('the source')
        with open(os.path.join(paths.ROOT, 'mod', 'build', runner.MOD_FILE), 'w') as handle:
            handle.write('another build, not the one recorded')
        self.assertEqual(os.path.basename(os.path.dirname(runner.mod_source())), 'prebuilt')
        os.unlink(os.path.join(paths.ROOT, 'mod', 'build', 'build.json'))
        self.assertEqual(os.path.basename(os.path.dirname(runner.mod_source())), 'prebuilt')


class ModBuildTest(Isolated):
    """The mod is built at install only when the one that came with the code is for another game build, and only with a
    .NET SDK that can (8 or newer): an older one is said in a line, not in a compiler's error."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, paths, 'ROOT', paths.ROOT)
        paths.ROOT = os.path.join(self.folder.name, 'code')
        os.makedirs(os.path.join(paths.ROOT, 'mod', 'prebuilt'))
        with open(os.path.join(paths.ROOT, 'mod', 'prebuilt', 'build.json'), 'w') as handle:
            json.dump({'game_build': '25902425'}, handle)

    def install(self, build):
        from unittest import mock
        from rustmapgen import game
        said = []
        with mock.patch.object(game, 'update_game'), mock.patch.object(runner, 'game_build', return_value=build), \
                mock.patch.object(runner, 'describe_mod', return_value='prebuilt'), mock.patch.object(runner, 'install_mod'), \
                mock.patch.object(runner, 'describe_install', return_value={}), \
                mock.patch.object(game.shutil, 'which', return_value='/usr/bin/dotnet'), \
                mock.patch.object(game, 'build_mod') as built:
            game.install(say=lambda text, **_: said.append(text))
        return built.called

    def test_the_mod_is_built_at_install_only_for_another_game_build(self):
        self.assertFalse(self.install('25902425'))      # the one that came with the code is for this game
        self.assertTrue(self.install('26200000'))       # a later game: built here, when it can be

    def test_an_older_sdk_is_said_plainly_and_nothing_is_built(self):
        from unittest import mock
        from rustmapgen import game
        runs = []

        def dotnet(argv, **kwargs):
            runs.append(argv[1])
            return mock.Mock(returncode=0, stdout='6.0.428 [C:\\Program Files\\dotnet\\sdk]\n7.0.410 [C:\\Program Files\\dotnet\\sdk]\n')
        with mock.patch.object(game.shutil, 'which', return_value='dotnet'), mock.patch.object(game.subprocess, 'run', side_effect=dotnet):
            with self.assertRaisesRegex(RuntimeError, 'has the .NET SDK 7; the mod is built with the .NET SDK 8 or newer'):
                game.build_mod(say=lambda *a, **k: None)
        self.assertEqual(runs, ['--list-sdks'])          # no `dotnet build`
        with mock.patch.object(game.subprocess, 'run', return_value=mock.Mock(stdout='8.0.100 [/usr/share/dotnet/sdk]\n9.0.101 [x]\n')):
            self.assertEqual(game.sdk_version('dotnet'), 9)
        with mock.patch.object(game.subprocess, 'run', return_value=mock.Mock(stdout='')):
            self.assertIsNone(game.sdk_version('dotnet'))     # the runtime only


class PrebuiltTest(unittest.TestCase):
    def test_the_mod_that_comes_with_the_code_is_built_from_its_source(self):
        # A source changed without a new prebuilt mod would ship a mod that ignores what the new code asks of it.
        with open(os.path.join(ROOT, 'mod', 'prebuilt', 'build.json')) as handle:
            record = json.load(handle)
        with open(os.path.join(ROOT, 'mod', 'RustMapGen.cs'), 'rb') as handle:
            self.assertEqual(record['source_sha256'], hashlib.sha256(handle.read()).hexdigest(),
                             'mod/RustMapGen.cs changed: build the mod again into mod/prebuilt and record it in build.json')
        with open(os.path.join(ROOT, 'mod', 'prebuilt', runner.MOD_FILE), 'rb') as handle:
            self.assertEqual(record['sha256'], hashlib.sha256(handle.read()).hexdigest())


class CatalogTest(Isolated):
    def test_a_catalog_an_older_version_wrote_gets_todays_kinds_of_scenery(self):
        with open(recipes.CATALOG) as handle:
            old = json.load(handle)
        old['scenery'] = [{'key': 'roadside boxes and signs', 'label': 'Electrical boxes and road signs',
                           'fragments': ['autospawn/decor/roadside/'], 'step': None, 'game_min_world_size': 0}]
        with open(paths.data('tables', 'catalog.json'), 'w') as handle:
            json.dump(old, handle)
        keys = [row['key'] for row in recipes.load_catalog()['scenery']]
        self.assertIn('electrical boxes', keys)
        self.assertIn('road signs', keys)
        self.assertNotIn('roadside boxes and signs', keys)


class SafetyTest(Isolated):
    """Input that would cost far more than it should, and the panel opened to the network."""

    def test_a_map_that_says_it_holds_too_much_is_refused_before_anything_is_set_aside(self):
        path = os.path.join(self.folder.name, 'huge.map')
        with open(path, 'wb') as handle:
            handle.write(struct.pack('<I', 10) + struct.pack('<q', 0) + mapfile._varint_bytes(1)
                         + mapfile._varint_bytes(1 << 40) + mapfile._varint_bytes(4) + b'abcd')

        def guarded(size=0):                # the reader's one allocation, watched: refused before it is made
            self.assertLess(size, 1 << 26)
            return bytearray(size)
        mapfile.bytearray = guarded
        self.addCleanup(delattr, mapfile, 'bytearray')
        with self.assertRaises(ValueError):
            mapfile.MapFile(path, paths=False)

    def test_a_map_of_endless_empty_blocks_is_refused(self):
        path = os.path.join(self.folder.name, 'empty-blocks.map')
        with open(path, 'wb') as handle:
            handle.write(struct.pack('<I', 10) + struct.pack('<q', 0) + b'\x00\x00' * (mapfile.MAX_BLOCKS + 10))
        with self.assertRaisesRegex(ValueError, f'more than {mapfile.MAX_BLOCKS} blocks'):
            mapfile.MapFile(path, paths=False)

    def test_a_block_list_must_be_a_list(self):
        for key in ('block_extra', 'template_skip'):
            with self.assertRaises(ValueError):
                recipes.normalise(dict(recipes.defaults(), **{key: 'iceberg'}))
            for wrong in ([3], '', 0, {}):
                with self.assertRaises(ValueError):
                    recipes.normalise(dict(recipes.defaults(), **{key: wrong}))
            self.assertEqual(recipes.normalise(dict(recipes.defaults(), **{key: ['Iceberg', ' ']}))[key], ['iceberg'])

    def test_a_run_asks_no_more_land_than_its_size_reaches(self):
        from rustmapgen import generate
        seen, said = [], []

        def stop(recipe, *args, **kwargs):          # what the run would make, without the game
            seen.append(recipe['terrain'].get('land_share'))
            raise RuntimeError('stop here')
        self.addCleanup(setattr, recipes, 'compile', recipes.compile)
        recipes.compile = stop
        path = paths.data('wide.json')
        with open(path, 'w') as handle:
            json.dump(dict(recipes.defaults(), name='wide', terrain={'land_share': 0.62}), handle)
        with self.assertRaises(RuntimeError):
            generate.generate(path, seed=1, size=2000, say=lambda text, **_: said.append(text))
        self.assertEqual(seen, [0.5])
        self.assertIn('land 62 % brought to 50 %, the most for a map of 2,000', said)

    def test_a_template_the_run_would_refuse_is_refused_before_the_game_starts(self):
        from unittest import mock
        from rustmapgen import generate
        os.makedirs(paths.data('templates'), exist_ok=True)
        _write(paths.data('templates', 'big.map'), _world(800, [('Decor', 77, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))]))
        path = paths.data('with-template.json')
        with open(path, 'w') as handle:
            json.dump(dict(recipes.defaults(), name='with-template', templates=['big.map']), handle)
        with mock.patch.object(generate, 'TEMPLATE_WORLD', 10), mock.patch.object(recipes, 'compile') as compiled:
            with self.assertRaisesRegex(ValueError, 'more than 0 MB'):
                generate.generate(path, seed=1, size=2000, say=lambda *a, **k: None)
            compiled.assert_not_called()               # nothing of the run was begun

    def test_a_refused_load_test_leaves_no_copy_of_the_map(self):
        from rustmapgen import generate
        folder = paths.data('maps', 'test-map-2')
        os.makedirs(folder)
        _write(os.path.join(folder, 'final.map'), _world(800, []))
        with self.assertRaises(Exception):
            generate.load_test('test-map-2', say=lambda *a, **k: None)
        self.assertEqual(os.listdir(paths.data('jobs')), [])

    def test_a_panel_on_the_network_answers_its_own_names_and_addresses_only(self):
        from http.server import ThreadingHTTPServer
        handler = server.make_handler(server.Queue(), server.network_names(['panel.example']), addresses=True)
        web = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        self.addCleanup(web.server_close)
        self.addCleanup(web.shutdown)
        port = web.server_address[1]

        def status(host):
            request = urllib.request.Request(f'http://127.0.0.1:{port}/api/status', headers={'Host': host})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        for host in ('192.0.2.10:5350', '[2001:db8::1]:5350', 'panel.example:5350', 'localhost', socket.gethostname().lower()):
            self.assertEqual(status(host), 200, host)
        for host in ('evil.example:5350', 'evil.example'):
            self.assertEqual(status(host), 403, host)


POWERLINE = 'assets/bundled/prefabs/autospawn/decor/powerline/powerline_a.prefab'


def _three_d_map(path):
    """A small map with the Outpost and a power line pylon on it, and the prefab table that names them."""
    _write(path, _world(1000, [('Monument', mapfile.prefab_id(MONUMENT), (100.0, 5.0, -200.0), (0.0, 90.0, 0.0)),
                               ('Decor', mapfile.prefab_id(POWERLINE), (-50.0, 8.0, 30.0), (0.0, 0.0, 0.0))], res=65))
    os.makedirs(os.path.dirname(library.prefab_table()), exist_ok=True)
    with open(library.prefab_table(), 'w') as handle:
        json.dump({str(mapfile.prefab_id(p)): p for p in (MONUMENT, POWERLINE)}, handle)


class ThreeDTest(Isolated):
    """The 3D view: what a map gives it, the models' files, and what the panel serves."""

    def test_a_map_gives_the_3d_view_its_ground_monuments_and_scenery(self):
        from rustmapgen import bake3d
        _three_d_map(paths.data('m.map'))
        out = paths.data('m3d')
        meta = bake3d.bake(paths.data('m.map'), out, seed=7)
        self.assertEqual(sorted(os.listdir(out)), sorted(bake3d.FILES))
        self.assertEqual((meta['size'], meta['samples']), (1000.0, 65))
        with Image.open(os.path.join(out, '3d_heights.png')) as image:
            self.assertEqual(image.size, (65, 65))
        # Positions in viewer space: the game's z turned over, for the models and the scenery alike.
        self.assertEqual([(p['short'], p['p']) for p in meta['placed']], [('compound', [100.0, 5.0, 200.0])])
        self.assertEqual([(m['label'], m['z'], m['icon']) for m in meta['monuments']], [('Outpost', -200.0, None)])
        self.assertEqual(meta['scenery_kinds'], ['decor/powerline/powerline_a'])
        with open(os.path.join(out, '3d_scenery.bin'), 'rb') as handle:
            row = struct.unpack('<HH3f4f3f', handle.read())
        self.assertEqual((row[0], row[2:5]), (0, (-50.0, 8.0, -30.0)))
        self.assertNotIn('models', meta)                 # the panel names the models when it serves the file
        self.assertEqual(bake3d.bake(paths.data('m.map'), paths.data('again'), seed=7)['trees'], meta['trees'])

    def test_a_river_surface_is_never_blended_with_no_water(self):
        from rustmapgen import bake3d

        class World:
            size = 2.0

            @staticmethod
            def layer(name):
                grid = np.zeros((3, 3), dtype='<i2')
                grid[1, 1] = 16383
                return grid
        at = bake3d.surface(World, np.array([0.0, 0.5, -1.0]), np.array([0.0, 0.0, -1.0]))
        held = mapfile.TERRAIN_BASE + 16383 * bake3d.TO_METRES
        self.assertAlmostEqual(at[0], held, places=3)
        self.assertAlmostEqual(at[1], held, places=3)   # half way to a cell without water: the water's own height
        self.assertTrue(np.isnan(at[2]))

    def test_models_are_written_as_the_page_reads_them(self):
        from rustmapgen import models3d
        cube = (np.array([[x, y, z] for x in (0, 2) for y in (0, 3) for z in (0, -4)], dtype=np.float64),
                np.full((8, 3), 0.5, dtype=np.float32),
                np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6]], dtype=np.int64))
        target = paths.data('models')
        os.makedirs(target)
        near = models3d.thinned(cube, 100)
        far = models3d.thinned(cube, 4)
        self.assertLessEqual(len(far[2]), 4)
        box, listed = models3d.write(target, 'decor__thing', [('near', near), ('far', far)])
        self.assertEqual(box, [0.0, 0.0, -4.0, 2.0, 3.0, 0.0])
        self.assertEqual([level['file'] for level in listed], ['decor__thing.near.bin.gz', 'decor__thing.far.bin.gz'])
        with open(os.path.join(target, 'decor__thing.near.bin.gz'), 'rb') as handle:
            blob = gzip.decompress(handle.read())
        length = struct.unpack_from('<I', blob)[0]
        header = json.loads(blob[4:4 + length])
        self.assertEqual(header['levels'][0], {'name': 'near', 'vertices': 8, 'triangles': 8, 'index': 2})
        with open(os.path.join(target, 'index.json'), 'w') as handle:
            json.dump({'decor/thing': {'levels': listed, 'box': box}}, handle)
        models3d.bundle(target)
        with open(os.path.join(target, 'far.bin.gz'), 'rb') as handle:
            blob = gzip.decompress(handle.read())
        length = struct.unpack_from('<I', blob)[0]
        offset, size = json.loads(blob[4:4 + length])['decor/thing']
        with open(os.path.join(target, 'decor__thing.far.bin.gz'), 'rb') as handle:
            self.assertEqual(blob[4 + length + offset:4 + length + offset + size], gzip.decompress(handle.read()))

    def test_the_models_are_made_again_for_another_game_or_another_code(self):
        from rustmapgen import models3d
        self.assertIsNone(models3d.version())            # no game: nothing to make them from
        manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"buildid"\t\t"25902425"\n}\n')
        made = models3d.version()
        self.assertRegex(made, r'^25902425-[0-9a-f]{10}$')
        self.assertTrue(models3d.VERSION.match(made))
        self.assertIsNone(models3d.ready())
        os.makedirs(models3d.folder(made))
        with open(os.path.join(models3d.folder(made), 'done.json'), 'w') as handle:
            handle.write('{}')
        self.assertEqual(models3d.ready(), made)
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"buildid"\t\t"26000000"\n}\n')
        self.assertIsNone(models3d.ready())               # a new build of the game: made again

    def test_a_game_whose_files_give_few_monument_models_is_a_failure_tried_again(self):
        from unittest import mock
        from rustmapgen import gamefiles, models3d
        manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"buildid"\t\t"25902425"\n}\n')
        open(paths.game(runner.MARKER), 'w').close()      # a game folder RustMapGen made
        todo = [('monuments', 'compound', []), ('monuments', 'airfield_1', []), ('scenery', 'rock', [])]
        with mock.patch.object(models3d, 'ensure_packages'), mock.patch.object(gamefiles, 'Game'), \
                mock.patch.object(models3d, 'jobs', return_value=todo):
            with self.assertRaisesRegex(RuntimeError, 'a model for 0 of 2 monuments'):
                models3d.build(say=lambda *a, **k: None)
        self.assertIsNone(models3d.ready())                # not "made": the view would have no monuments
        self.assertIn('0 of 2 monuments', models3d.failed())
        self.assertEqual(server.three_d_state()['ready'], False)

    def test_the_models_are_made_by_themselves_and_a_failed_try_is_not_repeated(self):
        from unittest import mock
        from rustmapgen import models3d

        class Waiting:
            def __init__(self):
                self.jobs = []

            def busy(self, kind):
                return any(job['kind'] == kind for job in self.jobs)

            def add(self, jobs):
                self.jobs += jobs
                return len(jobs)
        queue = Waiting()
        server.queue_models(queue)
        self.assertEqual(queue.jobs, [])                   # no game: nothing to make them from
        manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"buildid"\t\t"25902425"\n}\n')
        server.queue_models(queue)
        server.queue_models(queue)
        self.assertEqual(queue.jobs, [{'kind': 'models-3d'}])   # once, when the panel starts without them
        with mock.patch.object(models3d, '_build', side_effect=runner.Busy('another map is being made on this computer')):
            with self.assertRaises(runner.Busy):
                models3d.build()
        self.assertIsNone(models3d.failed())               # a busy game is no failure: the models wait their turn
        said = []
        with mock.patch.object(models3d, 'ensure_packages', side_effect=RuntimeError('no wheel for this Python')):
            models3d.after_game_change(say=lambda *line, **_: said.append(line[0]))
        self.assertIn('no wheel for this Python', said[-1])   # said, and the install or update goes on
        self.assertEqual(models3d.failed(), 'no wheel for this Python')
        self.assertEqual(server.three_d_state(), {'ready': False, 'game': True, 'failed': 'no wheel for this Python'})
        queue.jobs.clear()
        server.queue_models(queue)
        self.assertEqual(queue.jobs, [])                   # not tried again by itself: Prepare 3D does
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"buildid"\t\t"26000000"\n}\n')
        self.assertIsNone(models3d.failed())               # another build of the game is tried again
        server.queue_models(queue)
        self.assertEqual(queue.jobs, [{'kind': 'models-3d'}])

    def test_the_page_loads_only_files_the_panel_serves(self):
        folder = paths.package('page3d')
        with open(os.path.join(folder, 'index.html'), encoding='utf-8') as handle:
            html = handle.read()
        mapped = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html).group(1))['imports']
        wanted = {target[len('/3d/'):] for target in mapped.values()} | {re.search(r'src="/3d/([^"]+)"', html).group(1)}
        for name in sorted(server.PAGE3D_FILES):
            with open(os.path.join(folder, name), encoding='utf-8') as handle:
                for spec in re.findall(r"^import\s*[^'\"]*['\"]([^'\"]+)['\"]", handle.read(), re.M):
                    if spec.startswith('.'):
                        wanted.add(os.path.normpath(os.path.join(os.path.dirname(name), spec)).replace(os.sep, '/'))
                    else:
                        self.assertIn(spec, mapped, name)
        self.assertEqual(wanted - server.PAGE3D_FILES, set())
        self.assertTrue(os.path.isfile(os.path.join(folder, 'viewer', 'vendor', 'three.LICENSE')))

    def test_nothing_made_from_the_game_is_in_the_code(self):
        # The models are made on each computer from its own copy of the game, into the data folder: never shipped.
        kept = {'.git', 'data', 'game', 'game-staging', 'steamcmd', '.venv', 'obj', 'build', '__pycache__'}
        for base, folders, files in os.walk(ROOT):
            folders[:] = [f for f in folders if f not in kept]
            self.assertNotIn('3d-models', folders, base)
            for name in files:
                self.assertFalse(name.endswith('.bin.gz') or name.startswith('3d_'), os.path.join(base, name))

class NextUpdateTest(Isolated):
    """The next update: open once a release declares it (its forced wipe, Steam branch and what changes) until the
    update is out, its own folder and ports, and a mod that did not load on it. "staging" in the code."""

    def setUp(self):
        super().setUp()
        self.declaration = os.path.join(self.folder.name, 'next-update.json')
        os.environ['RUSTMAPGEN_NEXT_UPDATE'] = self.declaration
        self.addCleanup(os.environ.pop, 'RUSTMAPGEN_NEXT_UPDATE', None)

    def declare(self, **fields):
        body = dict({'wipe': '2026-11-05', 'branch': 'release', 'checked': 'Sun 1 Nov', 'build': '26200000',
                     'changes': ['Monuments sit further apart.', 'Train tunnels no longer cross canyons.']}, **fields)
        with open(self.declaration, 'w') as handle:
            json.dump(body, handle)

    def at(self, text):
        import datetime
        return datetime.datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone.utc)

    def test_nothing_is_offered_until_a_release_declares_it(self):
        from rustmapgen import staging
        when = self.at('2026-11-02 08:00')
        self.assertIsNone(staging.declared(when))
        self.assertFalse(staging.is_open(when))
        self.assertIsNone(staging.notice(when))
        self.assertIn('come with an update of RustMapGen', staging.refusal(when))
        described = staging.describe(when)
        self.assertEqual((described['open'], described['changes'], described['key']), (False, [], None))
        # A declaration that is not whole is no declaration.
        for broken in ({'wipe': '2026-11-06'}, {'branch': 'beta'}, {'changes': 'one line'}, {'changes': []}):
            self.declare(**broken)
            self.assertIsNone(staging.declared(when), broken)
        os.environ.pop('RUSTMAPGEN_NEXT_UPDATE')
        self.assertIsNone(staging.declared(when))      # this copy ships none
        os.environ[staging.OVERRIDE] = '1'
        self.addCleanup(os.environ.pop, staging.OVERRIDE, None)
        self.assertIsNone(staging.refusal(when))       # for testing, on any day

    def test_a_declaration_opens_it_until_its_forced_wipe(self):
        from rustmapgen import staging
        self.declare()
        when = self.at('2026-11-01 18:00')
        self.assertTrue(staging.is_open(when))
        self.assertIsNone(staging.refusal(when))
        self.assertIn("in 4 days (Thu 5 Nov). Its maps are made on Steam's release branch: the RustMapGen authors checked on Sun 1 Nov",
                      staging.notice(when))
        described = staging.describe(when)
        self.assertEqual((described['branch'], described['build'], described['key'], len(described['changes'])),
                         ('release', '26200000', '2026-11-05/Sun 1 Nov/26200000', 2))
        self.assertEqual(staging.branch(), 'release')
        self.assertFalse(staging.needs_word(when))                          # the declaration is enough until the day
        self.assertTrue(staging.needs_word(self.at('2026-11-05 10:00')))    # that day Steam is asked first
        self.assertIn('is today', staging.notice(self.at('2026-11-05 10:00')))
        self.assertIsNone(staging.declared(self.at('2026-11-06 00:00')))    # over when the day ends
        self.declare(branch='staging')
        self.assertIn("Steam's staging branch", staging.notice(when))

    def test_it_closes_when_steam_shows_the_update_out_on_the_wipe_day(self):
        from rustmapgen import staging
        self.declare()
        self.assertIsNone(staging.released(self.at('2026-11-05 08:00')))
        staging.remember({'public': '1000', 'release': '1150'}, self.at('2026-11-04 22:00'))
        staging.remember({'public': '1000', 'release': '1150'}, self.at('2026-11-05 08:00'))
        self.assertIsNone(staging.released(self.at('2026-11-05 08:05')))
        self.assertTrue(staging.is_open(self.at('2026-11-05 08:05')))
        self.assertIn('until the update is out', staging.notice(self.at('2026-11-05 08:05')))
        # The release: the public branch holds the declared branch's build.
        staging.remember({'public': '1150', 'release': '1150'}, self.at('2026-11-05 19:04'))
        self.assertEqual(staging.released(self.at('2026-11-05 19:05'))['public'], '1150')
        self.assertFalse(staging.is_open(self.at('2026-11-05 19:05')))
        self.assertIn("update came out by 19:04 UTC (Steam's public build 1150)", staging.refusal(self.at('2026-11-05 19:05')))
        self.assertIsNone(staging.released(self.at('2026-11-05 19:03')))      # what Steam said later does not count before
        self.assertEqual(staging.describe(self.at('2026-11-05 19:05'))['released'], {'public': '1150', 'utc': '2026-11-05T19:04:00Z'})

    def test_a_public_branch_set_on_the_wipe_day_is_the_release_with_nothing_heard_before(self):
        from rustmapgen import staging
        self.declare()
        unix = lambda text: int(self.at(text).timestamp())     # noqa: E731
        # A hotfix the day before is not the release.
        staging.remember({'public': '1001', 'release': '1150', 'public_updated': unix('2026-11-04 15:00')}, self.at('2026-11-05 09:00'))
        self.assertIsNone(staging.released(self.at('2026-11-05 09:01')))
        self.assertTrue(staging.is_open(self.at('2026-11-05 09:01')))
        # A first look at Steam after the release, with the release branch given a hotfix already.
        staging.remember({'public': '1160', 'release': '1170', 'public_updated': unix('2026-11-05 19:02')}, self.at('2026-11-05 21:00'))
        out = staging.released(self.at('2026-11-05 21:01'))
        self.assertEqual((out['public'], out['since']), ('1160', 'at 19:02'))
        self.assertIn('came out at 19:02 UTC', staging.refusal(self.at('2026-11-05 21:01')))
        self.assertFalse(staging.is_open(self.at('2026-11-05 21:01')))
        self.assertIsNone(staging.released(self.at('2026-11-05 19:01')))      # Steam's word is not believed before its time

    def test_steams_answer_gives_each_branch_its_build_and_when_it_was_set(self):
        from unittest import mock
        from rustmapgen import game, staging
        branch = lambda name, build, when: f'\t\t\t"{name}"\n\t\t\t{{\n\t\t\t\t"buildid"\t\t"{build}"\n\t\t\t\t"timeupdated"\t\t"{when}"\n\t\t\t}}\n'  # noqa: E731
        answer = ('"258550"\n{\n\t"depots"\n\t{\n\t\t"258551"\n\t\t{\n\t\t\t"manifests"\n\t\t\t{\n\t\t\t\t"public"\n\t\t\t\t{\n'
                  '\t\t\t\t\t"gid"\t\t"123"\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t\t"branches"\n\t\t{\n'
                  + branch('public', 25902425, 1791884653) + branch('release', 26104496, 1792407840)
                  + branch('staging', 26070150, 1792469805) + '\t\t}\n\t}\n}\n')
        with mock.patch.object(game, 'ensure_steamcmd', return_value='steamcmd'), mock.patch.object(game, '_steamcmd', return_value=(0, answer, '')):
            found = game.steam_builds()
        self.assertEqual(found, {'public': '25902425', 'public_updated': 1791884653, 'release': '26104496', 'release_updated': 1792407840,
                                 'staging': '26070150', 'staging_updated': 1792469805})
        kept = staging.seen()[-1]
        self.assertEqual((kept['public'], kept['public_updated'], kept['release'], kept['release_updated']),
                         ('25902425', 1791884653, '26104496', 1792407840))

    def test_on_the_wipe_day_work_on_it_asks_steam_first(self):
        from unittest import mock
        from rustmapgen import __main__ as cli, game, staging
        self.declare()
        self.assertTrue(staging.needs_word(self.at('2026-11-05 10:00')))      # nothing heard
        staging.remember({'public': '1000', 'release': '1150'}, self.at('2026-11-05 09:45'))
        self.assertFalse(staging.needs_word(self.at('2026-11-05 10:00')))
        self.assertTrue(staging.needs_word(self.at('2026-11-05 10:16')))
        self.assertFalse(staging.needs_word(self.at('2026-11-04 10:16')))     # the day before: the declaration is enough
        with mock.patch.object(staging, 'needs_word', return_value=True), \
                mock.patch.object(game, 'steam_builds', side_effect=RuntimeError('steamcmd ended with 5')):
            with self.assertRaises(SystemExit) as caught:
                cli.main(['generate', '--recipe', 'small', '--game', 'next'])
        self.assertIn('Steam did not answer', str(caught.exception.code))

    def test_a_game_already_at_steams_newest_build_is_left_as_it_is(self):
        from unittest import mock
        from rustmapgen import game
        runner.claim()
        manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"StateFlags"\t\t"4"\n\t"buildid"\t\t"200"\n}\n')
        open(runner.executable(), 'w').close()
        quiet = lambda *a, **k: None                    # noqa: E731
        with mock.patch.object(game, 'steam_builds', return_value={'public': '200'}), mock.patch.object(game, 'update_game') as update:
            self.assertEqual(game.bring_to_newest('public', quiet), '200')
            update.assert_not_called()
        with mock.patch.object(game, 'steam_builds', return_value={'public': '201'}), mock.patch.object(game, 'update_game') as update:
            game.bring_to_newest('public', quiet)
            update.assert_called_once_with('public', quiet)

    def test_the_next_updates_game_has_a_folder_and_ports_of_its_own(self):
        from rustmapgen import staging
        public = runner._command([], 'log.txt', dict(paths.DEFAULT_SETTINGS, isolate_network=False))[0]
        saved = os.environ['RUSTMAPGEN_GAME']
        self.addCleanup(os.environ.__setitem__, 'RUSTMAPGEN_GAME', saved)
        self.assertFalse(staging.active())
        staging.use()
        self.assertTrue(staging.active())
        self.assertEqual(paths.game(), os.path.join(self.folder.name, 'game-next'))
        ported = runner._command([], 'log.txt', dict(paths.DEFAULT_SETTINGS, isolate_network=False))[0]
        port = lambda argv: int(argv[argv.index('+server.port') + 1])     # noqa: E731
        self.assertEqual(port(ported), port(public) + staging.PORT_OFFSET)

    def test_a_mod_that_did_not_load_is_not_tried_again_on_that_build(self):
        manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
        runner.claim()
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"StateFlags"\t\t"4"\n\t"buildid"\t\t"300"\n\t"UserConfig"\n\t{\n\t\t"BetaKey"\t\t"release"\n\t}\n}\n')
        fake = os.path.join(self.folder.name, runner.MOD_FILE)
        with open(fake, 'wb') as handle:
            handle.write(b'the mod')
        self.addCleanup(setattr, runner, 'mod_source', runner.mod_source)
        runner.mod_source = lambda: fake
        self.assertIsNone(runner.mod_broken())
        runner._keep_mod_record(False)
        broken = runner.mod_broken()
        self.assertEqual((broken['build'], broken['branch']), ('300', 'release'))
        self.assertEqual(runner.describe_game()['mod_broken']['build'], '300')
        with open(paths.data('settings.json'), 'w') as handle:
            json.dump({'min_free_gb': 0, 'max_temp_c': 999}, handle)
        open(runner.executable(), 'w').close()
        with self.assertRaisesRegex(RuntimeError, 'does not work on release build 300'):
            runner.run(paths.data('jobs'), [], None, say=lambda *a, **k: None)
        with open(fake, 'wb') as handle:
            handle.write(b'a newer mod')                 # a newer RustMapGen brings another mod: it is tried
        self.assertIsNone(runner.mod_broken())
        runner._keep_mod_record(False)
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"StateFlags"\t\t"4"\n\t"buildid"\t\t"301"\n}\n')
        self.assertIsNone(runner.mod_broken())            # and so is another build of the game

    def test_the_command_line_keeps_its_days(self):
        import contextlib
        from unittest import mock
        from rustmapgen import __main__ as cli, staging
        with mock.patch.object(staging, 'needs_word', return_value=False), \
                mock.patch.object(staging, 'refusal', return_value='maps for the next update are made from Sat 31 Oct'):
            for argv in (['generate', '--recipe', 'small', '--game', 'next'], ['update', '--game', 'next'],
                         ['load-test', 'some-map', '--game', 'next'], ['install', '--game', 'next']):
                with self.assertRaises(SystemExit) as caught:
                    cli.main(argv)
                self.assertIn('Sat 31 Oct', str(caught.exception.code), argv)
        with mock.patch.object(staging, 'needs_word', return_value=False), mock.patch.object(staging, 'refusal', return_value=None):
            with self.assertRaises(SystemExit) as caught:
                cli.main(['generate', '--recipe', 'small', '--game', 'next'])
            self.assertIn('run "update --game next" first', str(caught.exception.code))
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown), self.assertRaises(SystemExit):
            cli.main(['--help'])
        self.assertIn('remove-next', shown.getvalue())
        self.assertNotIn('staging', shown.getvalue())

    def test_the_next_updates_game_is_never_the_public_one_whatever_the_names(self):
        from rustmapgen import __main__ as cli, game, staging
        self.addCleanup(setattr, staging, '_public', None)
        public = paths.game()
        runner.claim()                                  # the public game: a folder RustMapGen made, with its marker
        keep = os.path.join(public, 'RustDedicated')
        open(keep, 'w').close()
        alias = os.path.join(self.folder.name, 'alias')
        os.symlink(public, alias)
        for named in (alias, os.path.join(public, 'inside'), self.folder.name):    # the same folder, one inside, one around
            os.environ['RUSTMAPGEN_GAME_NEXT'] = named
            for argv in (['remove-next'], ['generate', '--recipe', 'small', '--game', 'next'], ['update', '--game', 'next']):
                with self.assertRaises(SystemExit) as caught:
                    cli.main(argv)
                self.assertIn('would share a folder', str(caught.exception.code), (named, argv))
        self.assertTrue(os.path.exists(keep))
        self.assertFalse(staging.same_place(public, os.path.join(self.folder.name, 'game-next')))
        # Where it is deleted, checked again: reached with the public game behind the name, nothing of it goes.
        staging._public = public
        os.environ['RUSTMAPGEN_GAME_NEXT'] = os.environ['RUSTMAPGEN_GAME'] = alias
        with self.assertRaisesRegex(RuntimeError, 'not only the next update'):
            game.remove_staging(say=lambda *a, **k: None)
        self.assertTrue(os.path.exists(keep))

    def test_a_relative_folder_setting_is_made_whole_for_the_jobs_the_panel_starts(self):
        script = 'import os; from rustmapgen import paths; print(os.environ["RUSTMAPGEN_GAME_NEXT"])'
        out = subprocess.run([sys.executable, '-c', script], cwd=self.folder.name, capture_output=True, text=True,
                             env=dict(os.environ, RUSTMAPGEN_GAME_NEXT='next-here', PYTHONPATH=ROOT))
        self.assertEqual(out.stdout.strip(), os.path.join(os.path.abspath(self.folder.name), 'next-here'), out.stderr)

    def test_each_job_on_the_next_update_starts_from_steams_newest_build_or_not_at_all(self):
        from unittest import mock
        from rustmapgen import __main__ as cli, game, generate as make, staging
        self.addCleanup(setattr, staging, '_public', None)
        saved = os.environ['RUSTMAPGEN_GAME']
        self.addCleanup(os.environ.__setitem__, 'RUSTMAPGEN_GAME', saved)
        self.declare()
        done = []
        # A map of the next update load tested on the public game: the public game is brought to Steam's newest first.
        name = 'small-2000-4242-20261021-224813-b6bf9d'
        os.makedirs(paths.data('maps', name))
        with open(paths.data('maps', name, 'map.json'), 'w') as handle:
            json.dump({'name': name, 'run': {'game_branch': 'release'}}, handle)
        runner.claim()
        with mock.patch.object(game, 'bring_to_newest', side_effect=lambda branch, *a, **k: done.append(('newest', branch))), \
                mock.patch.object(make, 'load_test', side_effect=lambda *a, **k: done.append('load test')):
            cli.main(['load-test', name])
        self.assertEqual(done, [('newest', 'public'), 'load test'])
        # On the next update's game, before each map and each load test; when that fails, nothing is made.
        os.makedirs(staging.folder())
        open(os.path.join(staging.folder(), runner.MARKER), 'w').close()
        open(runner.executable(staging.folder()), 'w').close()
        done.clear()
        with mock.patch.object(staging, 'needs_word', return_value=False), \
                mock.patch.object(game, 'bring_to_newest', side_effect=lambda branch, *a, **k: done.append(('newest', branch))), \
                mock.patch.object(make, 'generate', side_effect=lambda *a, **k: done.append('map')):
            cli.main(['generate', '--recipe', 'small', '--game', 'next', '--count', '2'])
        self.assertEqual(done, [('newest', 'release'), 'map', ('newest', 'release'), 'map'])
        os.environ['RUSTMAPGEN_GAME'] = saved           # each command is a process of its own: this one switched to that game
        done.clear()
        with mock.patch.object(staging, 'needs_word', return_value=False), \
                mock.patch.object(game, 'bring_to_newest', side_effect=RuntimeError('steamcmd did not install the game')), \
                mock.patch.object(make, 'generate', side_effect=lambda *a, **k: done.append('map')), \
                mock.patch.object(make, 'load_test', side_effect=lambda *a, **k: done.append('load test')):
            with self.assertRaises(SystemExit) as caught:
                cli.main(['generate', '--recipe', 'small', '--game', 'next'])
            self.assertIn('1 of 1 maps were not made', str(caught.exception.code))
            os.environ['RUSTMAPGEN_GAME'] = saved
            with self.assertRaisesRegex(RuntimeError, 'did not install'):
                cli.main(['load-test', name, '--game', 'next'])
            os.environ['RUSTMAPGEN_GAME'] = saved
        self.assertEqual(done, [])

    def test_a_batch_on_the_next_update_stops_when_it_closes(self):
        from unittest import mock
        from rustmapgen import __main__ as cli, game, generate as make, staging
        self.addCleanup(setattr, staging, '_public', None)
        saved = os.environ['RUSTMAPGEN_GAME']
        self.addCleanup(os.environ.__setitem__, 'RUSTMAPGEN_GAME', saved)
        self.declare()
        os.makedirs(staging.folder())
        open(os.path.join(staging.folder(), runner.MARKER), 'w').close()
        open(runner.executable(staging.folder()), 'w').close()
        done = []
        closed = iter([None, None, None, None, "the forced wipe's update came out at 17:02 UTC"])
        with mock.patch.object(staging, 'needs_word', return_value=False), \
                mock.patch.object(staging, 'refusal', side_effect=lambda *a, **k: next(closed)), \
                mock.patch.object(game, 'bring_to_newest', side_effect=lambda branch, *a, **k: done.append('newest')), \
                mock.patch.object(make, 'generate', side_effect=lambda *a, **k: done.append('map')):
            with self.assertRaises(SystemExit) as caught:
                cli.main(['generate', '--recipe', 'small', '--game', 'next', '--count', '3'])
        self.assertEqual(done, ['newest', 'map', 'newest', 'map'])       # the third map is not started
        self.assertIn('1 of 3 maps were not made', str(caught.exception.code))
        # The refresh itself can learn that the update is out (or take the day past its end): asked again after it.
        os.environ['RUSTMAPGEN_GAME'] = saved           # each command is a process of its own
        done.clear()
        closed = iter([None, "the forced wipe's update came out at 17:02 UTC"])
        with mock.patch.object(staging, 'needs_word', return_value=False), \
                mock.patch.object(staging, 'refusal', side_effect=lambda *a, **k: next(closed)), \
                mock.patch.object(game, 'bring_to_newest', side_effect=lambda branch, *a, **k: done.append('newest')), \
                mock.patch.object(make, 'generate', side_effect=lambda *a, **k: done.append('map')):
            with self.assertRaises(SystemExit) as caught:
                cli.main(['generate', '--recipe', 'small', '--game', 'next'])
        self.assertEqual(done, ['newest'])
        self.assertIn('1 of 1 maps were not made', str(caught.exception.code))

    def test_the_two_release_tests_agree_and_a_damaged_or_future_answer_decides_nothing(self):
        from unittest import mock
        from rustmapgen import game, staging
        self.declare()
        unix = lambda text: int(self.at(text).timestamp())     # noqa: E731
        runner.claim()
        manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, 'w') as handle:
            handle.write(f'"AppState"\n{{\n\t"StateFlags"\t\t"4"\n\t"buildid"\t\t"100"\n\t"LastUpdated"\t\t"{unix("2026-10-20 12:00")}"\n}}\n')
        quiet = lambda *a, **k: None                    # noqa: E731
        # Steam's answer without a time for the public branch, holding the release branch's build that day: out for both.
        staging.remember({'public': '200', 'release': '200'}, self.at('2026-11-05 19:58'))
        self.assertTrue(staging.released(self.at('2026-11-05 20:00')))
        with mock.patch.object(game, 'update_game', return_value='200') as update:
            game.catch_up(quiet, self.at('2026-11-05 20:00'))
            update.assert_called_once_with('public', quiet)
        # A public branch said to be set later than now is not believed.
        os.unlink(paths.data(staging.SEEN))
        os.environ.pop('RUSTMAPGEN_NEXT_UPDATE')
        staging.remember({'public': '200', 'public_updated': unix('2026-11-05 21:00')}, self.at('2026-11-05 19:58'))
        with mock.patch.object(game, 'update_game') as update:
            game.catch_up(quiet, self.at('2026-11-05 20:00'))
            update.assert_not_called()
        # An answer stamped later than now (a clock set back) is not fresh, and a damaged line is left out.
        os.environ['RUSTMAPGEN_NEXT_UPDATE'] = self.declaration
        self.assertTrue(staging.needs_word(self.at('2026-11-05 19:50')))
        with open(paths.data(staging.SEEN)) as handle:
            rows = json.load(handle)
        rows.insert(0, {'utc': 'yesterday', 'public': '1'})
        with open(paths.data(staging.SEEN), 'w') as handle:
            json.dump(rows, handle)
        self.assertEqual([row['utc'] for row in staging.seen()], ['2026-11-05T19:58:00Z'])
        self.assertIsNone(staging.released(self.at('2026-11-05 20:00')))

    def test_an_install_ends_with_lines_a_person_reads(self):
        import contextlib
        from unittest import mock
        from rustmapgen import __main__ as cli, game, models3d
        record = {'game': '/x/game', 'installed': True, 'build': '25902425', 'branch': 'public', 'data': '/x/data', 'mod_broken': None,
                  'mod_used': 'the one that came with the code, for game build 25902425 (/x/mod/prebuilt/RustMapGen.dll)'}
        shown = io.StringIO()
        with mock.patch.object(game, 'install', return_value=record), mock.patch.object(models3d, 'after_game_change'), \
                contextlib.redirect_stdout(shown):
            cli.main(['install'])
        self.assertEqual(shown.getvalue().strip().splitlines(), [
            'In place:', '  game: build 25902425, public branch, in /x/game',
            '  mod: the one that came with the code, for game build 25902425', '  maps and settings: /x/data'])

    def test_steam_without_the_branch_stops_the_work_and_a_day_gone_by_says_so(self):
        from unittest import mock
        from rustmapgen import game, staging
        quiet = lambda *a, **k: None                    # noqa: E731
        with mock.patch.object(game, 'steam_builds', return_value={'public': '200'}), \
                mock.patch.object(game, 'update_game') as update:
            with self.assertRaisesRegex(RuntimeError, 'Steam has no release branch'):
                game.bring_to_newest('release', quiet)
            update.assert_not_called()                 # no steamcmd run that could only fail and leave the game "updating"
        self.declare()
        self.assertIn('the forced wipe of Thu 5 Nov is over', staging.refusal(self.at('2026-11-06 09:00')))
        os.environ.pop('RUSTMAPGEN_NEXT_UPDATE')
        self.assertIn('come with an update of RustMapGen', staging.refusal(self.at('2026-11-06 09:00')))

    def test_a_load_test_and_the_update_before_it_go_in_together_or_not_at_all(self):
        from unittest import mock
        queue = server.Queue()
        queue.process = mock.Mock(poll=lambda: None)    # a job "running": nothing more is started meanwhile
        with queue.lock:
            queue.jobs = [{'kind': 'models-3d'}] * (server.QUEUE_LIMIT - 1)
        self.assertEqual(queue.add([{'kind': 'update'}, {'kind': 'load-test'}], whole=True), 0)
        self.assertEqual(len(queue.jobs), server.QUEUE_LIMIT - 1)
        self.assertEqual(queue.add([{'kind': 'update'}, {'kind': 'generate'}]), 1)     # a batch takes what fits

    def test_a_fresh_answer_of_steam_is_used_again_before_the_next_job(self):
        from unittest import mock
        from rustmapgen import game, staging
        runner.claim()
        manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, 'w') as handle:
            handle.write('"AppState"\n{\n\t"StateFlags"\t\t"4"\n\t"buildid"\t\t"300"\n\t"UserConfig"\n\t{\n\t\t"BetaKey"\t\t"release"\n\t}\n}\n')
        open(runner.executable(), 'w').close()
        staging.remember({'public': '200', 'release': '300'})
        quiet = lambda *a, **k: None                    # noqa: E731
        with mock.patch.object(game, 'steam_builds') as asked, mock.patch.object(game, 'update_game') as update:
            self.assertEqual(game.bring_to_newest('release', quiet), '300')
            asked.assert_not_called()
            update.assert_not_called()

    def test_a_job_of_the_next_update_runs_its_command_on_its_game(self):
        self.assertEqual(server.job_arguments({'kind': 'update', 'game': 'staging'}), ['update', '--game', 'next'])
        self.assertEqual(server.job_arguments({'kind': 'update'}), ['update'])
        self.assertEqual(server.job_arguments({'kind': 'update', 'forced_wipe': True}), ['update', '--forced-wipe'])
        self.assertEqual(server.job_arguments({'kind': 'generate', 'recipe_file': 'r.json', 'game': 'staging', 'seed': 7}),
                         ['generate', '--recipe', 'r.json', '--note', '', '--game', 'next', '--seed', '7'])
        self.assertEqual(server.job_arguments({'kind': 'load-test', 'name': 'm', 'game': 'public'}), ['load-test', 'm'])
        self.assertEqual(server.job_arguments({'kind': 'load-test', 'name': 'm', 'game': 'staging'}), ['load-test', 'm', '--game', 'next'])
        self.assertEqual(server.job_arguments({'kind': 'remove-staging'}), ['remove-next'])

class ForcedWipeTest(Isolated):
    """From the forced wipe on, maps are made on its update: a game last updated before that day is brought to it first,
    once it is out, and a mid-month update is never fetched so."""

    def at(self, text):
        import datetime
        return datetime.datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone.utc)

    def unix(self, text):
        return int(self.at(text).timestamp())

    def game_at(self, build, updated):
        runner.claim()
        manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
        os.makedirs(os.path.dirname(manifest), exist_ok=True)
        with open(manifest, 'w') as handle:
            handle.write(f'"AppState"\n{{\n\t"StateFlags"\t\t"4"\n\t"buildid"\t\t"{build}"\n'
                         f'\t"LastUpdated"\t\t"{self.unix(updated)}"\n}}\n')

    def test_the_latest_forced_wipe_is_the_first_thursday_once_it_has_come(self):
        from rustmapgen import staging
        self.assertEqual(str(staging.last_wipe(self.at('2026-11-05 00:00'))), '2026-11-05')
        self.assertEqual(str(staging.last_wipe(self.at('2026-11-04 23:59'))), '2026-10-01')
        self.assertEqual(str(staging.last_wipe(self.at('2027-01-06 12:00'))), '2026-12-03')
        self.assertEqual(str(staging.last_wipe(self.at('2027-01-07 12:00'))), '2027-01-07')
        self.assertEqual(staging.wipe_start(self.at('2026-11-20 08:00')), self.at('2026-11-05 00:00'))

    def test_a_game_older_than_the_forced_wipe_is_brought_to_its_update_once_it_is_out(self):
        from unittest import mock
        from rustmapgen import game
        said = []
        say = lambda text, **k: said.append(text)       # noqa: E731
        self.assertFalse(game.behind_forced_wipe(self.at('2026-11-05 09:00')))     # no game: nothing to bring
        self.game_at('1000', '2026-10-20 12:00')
        self.assertEqual(runner.game_updated(), self.unix('2026-10-20 12:00'))
        self.assertFalse(game.behind_forced_wipe(self.at('2026-11-04 23:00')))     # before the wipe it is the game
        self.assertTrue(game.behind_forced_wipe(self.at('2026-11-05 09:00')))
        # The morning of the wipe: Steam's public build is still a mid-month one, and it is not fetched.
        with mock.patch.object(game, 'steam_builds', return_value={'public': '1001', 'public_updated': self.unix('2026-10-22 17:00')}), \
                mock.patch.object(game, 'update_game') as update:
            self.assertEqual(game.catch_up(say, self.at('2026-11-05 09:00')), '1000')
            update.assert_not_called()
        self.assertIn('not out yet', said[-1])
        # Steam cannot be asked: the game stays as it is, and nothing marks it as being updated.
        with mock.patch.object(game, 'steam_builds', side_effect=RuntimeError('steamcmd ended with 5')), \
                mock.patch.object(game, 'update_game') as update:
            self.assertEqual(game.catch_up(say, self.at('2026-11-05 18:00')), '1000')
            update.assert_not_called()
        self.assertIn('could not be asked', said[-1])
        self.assertFalse(os.path.exists(paths.game(runner.UPDATING)))
        # Out: Steam's public branch was set on the day of the wipe.
        with mock.patch.object(game, 'steam_builds', return_value={'public': '1100', 'public_updated': self.unix('2026-11-05 17:02')}), \
                mock.patch.object(game, 'update_game', return_value='1100') as update:
            self.assertEqual(game.catch_up(say, self.at('2026-11-05 18:00')), '1100')
            update.assert_called_once_with('public', say)
        self.assertIn('brought build 1100', said[-1])
        # Updated since that day began: Steam is not even asked, so a hotfix later in the month is left to the person.
        self.game_at('1100', '2026-11-05 18:01')
        with mock.patch.object(game, 'steam_builds') as asked, mock.patch.object(game, 'update_game') as update:
            self.assertEqual(game.catch_up(say, self.at('2026-11-20 10:00')), '1100')
            asked.assert_not_called()
            update.assert_not_called()

    def test_a_fresh_answer_of_steam_is_used_again(self):
        from unittest import mock
        from rustmapgen import game, staging
        quiet = lambda *a, **k: None                    # noqa: E731
        self.game_at('1000', '2026-10-20 12:00')
        staging.remember({'public': '1000', 'public_updated': self.unix('2026-10-01 17:00')}, self.at('2026-11-05 09:50'))
        with mock.patch.object(game, 'steam_builds') as asked, mock.patch.object(game, 'update_game') as update:
            game.catch_up(quiet, self.at('2026-11-05 10:00'))
            asked.assert_not_called()
            update.assert_not_called()
        with mock.patch.object(game, 'steam_builds', return_value={'public': '1000', 'public_updated': self.unix('2026-10-01 17:00')}) as asked:
            game.catch_up(quiet, self.at('2026-11-05 10:30'))
            asked.assert_called_once()

    def test_the_command_line_catches_up_before_its_maps(self):
        from unittest import mock
        from rustmapgen import __main__ as cli, game, generate as make, models3d
        order = []
        with mock.patch.object(game, 'behind_forced_wipe', return_value=True), \
                mock.patch.object(game, 'catch_up', side_effect=lambda *a, **k: order.append('catch up')), \
                mock.patch.object(make, 'generate', side_effect=lambda *a, **k: order.append('map')):
            cli.main(['generate', '--recipe', 'small', '--count', '2'])
        self.assertEqual(order, ['catch up', 'map', 'catch up', 'map'])
        with mock.patch.object(game, 'behind_forced_wipe', return_value=False), mock.patch.object(game, 'catch_up') as caught_up, \
                mock.patch.object(make, 'generate'):
            cli.main(['generate', '--recipe', 'small'])
            caught_up.assert_not_called()
        with mock.patch.object(game, 'catch_up') as caught_up, mock.patch.object(game, 'bring_to_newest') as newest, \
                mock.patch.object(models3d, 'after_game_change'):
            cli.main(['update', '--forced-wipe'])
            caught_up.assert_called_once_with()
            newest.assert_not_called()


if __name__ == '__main__':
    unittest.main()
