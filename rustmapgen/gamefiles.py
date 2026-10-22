"""The game's own files, read where they are: the prefabs of the dedicated server in game/Bundles/shared.

The 3D view (models3d.py) takes the shapes of the monuments and of the scenery from here, on this
computer, from the copy of the game RustMapGen installed. Nothing read here is kept in RustMapGen's own
files or sent anywhere: what is made of it stays in the data folder.

Bundles are read in place: a UnityFS bundle stored without compression (the game's are) is mapped into
memory, and the files inside it are parsed by UnityPy only when something points into them.
"""
import bisect
import glob
import math
import mmap
import os
import struct
import sys

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def trs(position, rotation, scale):
    x, y, z, w = rotation
    sx, sy, sz = scale
    return ((1 - 2 * (y * y + z * z)) * sx, 2 * (x * y - z * w) * sy, 2 * (x * z + y * w) * sz, position[0],
            2 * (x * y + z * w) * sx, (1 - 2 * (x * x + z * z)) * sy, 2 * (y * z - x * w) * sz, position[1],
            2 * (x * z - y * w) * sx, 2 * (y * z + x * w) * sy, (1 - 2 * (x * x + y * y)) * sz, position[2])


def mat_mul(a, b):
    out = []
    for row in range(0, 12, 4):
        a0, a1, a2, a3 = a[row:row + 4]
        out += [a0 * b[0] + a1 * b[4] + a2 * b[8], a0 * b[1] + a1 * b[5] + a2 * b[9],
                a0 * b[2] + a1 * b[6] + a2 * b[10], a0 * b[3] + a1 * b[7] + a2 * b[11] + a3]
    return tuple(out)


def move(m, points):
    return [(m[0] * x + m[1] * y + m[2] * z + m[3], m[4] * x + m[5] * y + m[6] * z + m[7],
             m[8] * x + m[9] * y + m[10] * z + m[11]) for x, y, z in points]


def vec(d):
    return (d['x'], d['y'], d['z'])


def grow(box, points):
    """[x0, y0, z0, x1, y1, z1] around the points (and the box, when there is one)."""
    for p in points:
        if box is None:
            box = [p[0], p[1], p[2], p[0], p[1], p[2]]
        else:
            for axis in range(3):
                if p[axis] < box[axis]:
                    box[axis] = p[axis]
                if p[axis] > box[axis + 3]:
                    box[axis + 3] = p[axis]
    return box


def corners(center, extent):
    return [(center[0] + sx * extent[0], center[1] + sy * extent[1], center[2] + sz * extent[2])
            for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]


class Bundle:
    """A UnityFS bundle read in place: its directory, and byte ranges of the files inside it."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as handle:
            head = handle.read(4096)
            if not head.startswith(b'UnityFS\0'):
                raise ValueError('not a UnityFS bundle')
            at = 8
            version = struct.unpack_from('>I', head, at)[0]
            at += 4
            for _ in range(2):
                at = head.index(b'\0', at) + 1
            size, packed, plain, flags = struct.unpack_from('>qIII', head, at)
            if size != os.path.getsize(path):
                raise ValueError(f'{os.path.getsize(path)} bytes on disk, {size} in its header (still being written?)')
            at += 20
            if version >= 7:
                at = (at + 15) // 16 * 16
            if flags & 0x80:
                handle.seek(size - packed)
            else:
                handle.seek(at)
                at += packed
            info = self.unpack(handle.read(packed), plain, flags & 0x3F)
            if flags & 0x200:
                at = (at + 15) // 16 * 16
        self.blocks = []        # (start in the unpacked stream, unpacked size, start in the file, packed size, flags)
        plain_at, file_at = 0, at
        pos = 20
        for _ in range(struct.unpack_from('>i', info, 16)[0]):
            unpacked, stored, kind = struct.unpack_from('>IIH', info, pos)
            pos += 10
            self.blocks.append((plain_at, unpacked, file_at, stored, kind))
            plain_at += unpacked
            file_at += stored
        self.nodes = {}         # name of a file in the bundle -> (offset in the unpacked stream, size)
        count = struct.unpack_from('>i', info, pos)[0]
        pos += 4
        for _ in range(count):
            offset, length = struct.unpack_from('>qq', info, pos)
            end = info.index(b'\0', pos + 20)
            self.nodes[info[pos + 20:end].decode()] = (offset, length)
            pos = end + 1
        self.starts = [block[0] for block in self.blocks]
        self.start = at
        self.stored = all(block[4] & 0x3F == 0 for block in self.blocks)
        self.mapped = None

    def view(self, name):
        """One file of the bundle as memory: a window on the bundle when it is stored as it is."""
        if not self.stored:
            return self.read(name)
        if self.mapped is None:
            with open(self.path, 'rb') as handle:
                self.mapped = memoryview(mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ))
        offset, size = self.nodes[name]
        return self.mapped[self.start + offset:self.start + offset + size]

    @staticmethod
    def unpack(data, plain, method):
        if method == 0:
            return data
        if method in (2, 3):
            import lz4.block
            return lz4.block.decompress(data, uncompressed_size=plain)
        raise ValueError(f'bundle compression {method} is not handled')

    def read(self, name, offset=0, length=None):
        """Bytes of one file of the bundle, touching only the blocks they lie in."""
        start, size = self.nodes[name]
        if length is None:
            length = size - offset
        begin, end = start + offset, start + offset + length
        out = bytearray()
        i = bisect.bisect_right(self.starts, begin) - 1
        with open(self.path, 'rb') as handle:
            while i < len(self.blocks) and self.blocks[i][0] < end:
                plain_at, unpacked, file_at, stored, kind = self.blocks[i]
                first, last = max(0, begin - plain_at), min(unpacked, end - plain_at)
                if kind & 0x3F == 0:
                    handle.seek(file_at + first)
                    out += handle.read(last - first)
                else:
                    handle.seek(file_at)
                    out += self.unpack(handle.read(stored), unpacked, kind & 0x3F)[first:last]
                i += 1
        return bytes(out)


class Resource:
    """A resource file of a bundle (.resS: mesh data) for UnityPy to read ranges from."""

    def __init__(self, bundle, name):
        self.bundle, self.name, self.Position = bundle, name, 0

    def read_bytes(self, size):
        return self.bundle.read(self.name, self.Position, size)


class Game:
    """The bundles of the game's folder, and its prefabs by name (a prefab's root is named by its full path).

    content.bundle and assetscenes.bundle are parsed at once; another bundle of the folder is parsed when
    something points into it.
    """

    def __init__(self, folder):
        import UnityPy
        self.env = UnityPy.Environment()
        self.where = {}         # name of a file inside a bundle -> its Bundle
        self.files = {}         # the parsed files, by name
        self.briefs = {}
        self.decoded = {}
        bundles = sorted(glob.glob(os.path.join(folder, '*.bundle')))
        if not bundles:
            raise FileNotFoundError(f'no game files in {folder}')
        for path in bundles:
            try:
                bundle = Bundle(path)
            except (ValueError, OSError) as error:
                print(f'{path} skipped: {error}', file=sys.stderr)
                continue
            for name in bundle.nodes:
                self.where[name] = bundle
                if name.endswith(('.resS', '.resource')):
                    self.env.register_cab(name, Resource(bundle, name))
        for first in ('content.bundle', 'assetscenes.bundle'):
            for name, bundle in list(self.where.items()):
                if os.path.basename(bundle.path) == first:
                    self.load(name)
        self.roots = {}         # name of a root object -> its Transforms
        for part in list(self.files.values()):
            for obj in part.objects.values():
                if obj.type.name != 'Transform':
                    continue
                tree = obj.read_typetree()
                if tree['m_Father']['m_PathID'] != 0:
                    continue
                game_object = obj.assets_file.files.get(tree['m_GameObject']['m_PathID'])
                if game_object is not None:
                    self.roots.setdefault(game_object.read_typetree()['m_Name'], []).append(obj)

    def load(self, name):
        """The file of that name, parsed on first use; None for a resource or a file not here."""
        if name not in self.files:
            bundle = self.where.get(name)
            if bundle is None or name.endswith(('.resS', '.resource')):
                return None
            self.files[name] = self.env.load_file(bundle.view(name), name=name)
        return self.files[name]

    def deref(self, owner, pointer):
        if not pointer or not pointer.get('m_PathID'):
            return None
        files = owner.assets_file
        if pointer['m_FileID'] != 0:
            files = self.load(files.externals[pointer['m_FileID'] - 1].path.split('/')[-1])
            if files is None:
                return None
        return files.files.get(pointer['m_PathID'])

    def walk(self, transform, matrix=IDENTITY, path='', active=True, is_root=True):
        """Every object below a Transform: its place in the root's space, its layer and its components by
        type (scripts left out)."""
        tree = transform.read_typetree()
        files = transform.assets_file.files
        game_object = files[tree['m_GameObject']['m_PathID']].read_typetree()
        if not is_root:
            # The root of a prefab is stored switched off, and its own transform is where the entity stands.
            path = path + '/' + game_object['m_Name']
            q = tree['m_LocalRotation']
            matrix = mat_mul(matrix, trs(vec(tree['m_LocalPosition']), (q['x'], q['y'], q['z'], q['w']), vec(tree['m_LocalScale'])))
            active = active and bool(game_object.get('m_IsActive', True))
        parts = {}
        for entry in game_object['m_Component']:
            pointer = entry['component'] if 'component' in entry else entry
            component = files.get(pointer['m_PathID'])
            if component is not None and component.type.name != 'MonoBehaviour':
                parts.setdefault(component.type.name, []).append(component)
        yield {'path': path, 'name': game_object['m_Name'], 'active': active, 'layer': game_object['m_Layer'],
               'matrix': matrix, 'parts': parts, 'root': is_root}
        for child in tree['m_Children']:
            child_object = files.get(child['m_PathID'])
            if child_object is not None:
                yield from self.walk(child_object, matrix, path, active, False)

    def brief(self, mesh):
        """Name, size and triangle count of a mesh, without decoding it."""
        key = (mesh.assets_file.name, mesh.path_id)
        if key not in self.briefs:
            tree = mesh.read_typetree()
            box = tree['m_LocalAABB']
            self.briefs[key] = {'name': tree['m_Name'], 'tris': sum(s['indexCount'] for s in tree['m_SubMeshes']) // 3,
                                'center': vec(box['m_Center']), 'extent': vec(box['m_Extent'])}
        return self.briefs[key]

    def decode(self, mesh):
        """Positions and one triangle list per submesh, as the game stores them."""
        key = (mesh.assets_file.name, mesh.path_id)
        if key in self.decoded:
            return self.decoded[key]
        from UnityPy.helpers.MeshHelper import MeshHandler
        source = mesh.read()
        handler = MeshHandler(source)
        handler.process()
        positions = [tuple(p[:3]) for p in handler.m_Vertices or []]
        index = handler.m_IndexBuffer or []
        wide = 1 if handler.m_Use16BitIndices else 2
        subs = []
        for sub in source.m_SubMeshes:
            triangles = []
            if int(sub.topology) == 0:
                first = sub.firstByte // 2 // wide
                base = getattr(sub, 'baseVertex', 0) or 0
                for i in range(first, first + sub.indexCount - 2, 3):
                    triangles.append((index[i] + base, index[i + 1] + base, index[i + 2] + base))
            subs.append(triangles)
        self.decoded[key] = {'positions': positions, 'subs': subs}
        return self.decoded[key]


# ---- pieces: a mesh or a made-up shape at a place in a prefab ----

def piece(game, mesh, matrix):
    info = game.brief(mesh)
    return {'mesh': mesh, 'matrix': matrix, 'tris': info['tris'], 'name': info['name'],
            'box': grow(None, move(matrix, corners(info['center'], info['extent'])))}


def made_piece(shape, matrix, name):
    positions, triangles = shape
    return {'data': {'positions': positions, 'subs': [triangles]}, 'matrix': matrix,
            'tris': len(triangles), 'name': name, 'box': grow(None, move(matrix, positions))}


def geometry(game, part):
    return part['data'] if 'data' in part else game.decode(part['mesh'])


def box_shape(center, size):
    """A collider box as triangles."""
    half = (size[0] / 2, size[1] / 2, size[2] / 2)
    positions, triangles = [], []
    for axis in range(3):
        u, v = [(1, 2), (2, 0), (0, 1)][axis]
        for sign in (-1, 1):
            n = len(positions)
            for su, sv in ((-1, -1), (-1, 1), (1, 1), (1, -1)):
                p = [0.0, 0.0, 0.0]
                p[axis], p[u], p[v] = sign * half[axis], su * half[u], sv * half[v]
                positions.append((p[0] + center[0], p[1] + center[1], p[2] + center[2]))
            triangles += [(n, n + 1, n + 2), (n, n + 2, n + 3)]
    return outward(positions, triangles, center)


def round_shape(center, radius, half, axis, rings):
    """A capsule (half > 0) or a sphere as a coarse mesh along one axis."""
    u, v = [(1, 2), (2, 0), (0, 1)][axis]
    sides = 10
    levels = []
    for i in range(rings + 1):
        angle = -math.pi / 2 + math.pi * i / rings
        levels.append((math.sin(angle) * radius + (half if i * 2 > rings else -half), math.cos(angle) * radius))
    if half > 0 and rings % 2 == 0:
        middle = rings // 2
        levels[middle:middle + 1] = [(-half, radius), (half, radius)]
    positions, triangles = [], []
    for along, r in levels:
        for s in range(sides):
            p = [0.0, 0.0, 0.0]
            p[axis], p[u], p[v] = along, math.cos(2 * math.pi * s / sides) * r, math.sin(2 * math.pi * s / sides) * r
            positions.append((p[0] + center[0], p[1] + center[1], p[2] + center[2]))
    for i in range(len(levels) - 1):
        for s in range(sides):
            a, b = i * sides + s, i * sides + (s + 1) % sides
            triangles += [(a, b, a + sides), (b, b + sides, a + sides)]
    return outward(positions, [t for t in triangles if len({positions[i] for i in t}) == 3], center)


def outward(positions, triangles, middle):
    """Turn every triangle of a convex shape so that cross(b - a, c - a) points away from its middle,
    which is how the game's own meshes are wound."""
    out = []
    for a, b, c in triangles:
        pa, pb, pc = positions[a], positions[b], positions[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        away = (pa[0] + pb[0] + pc[0] - 3 * middle[0]) * (uy * vz - uz * vy) + \
            (pa[1] + pb[1] + pc[1] - 3 * middle[1]) * (uz * vx - ux * vz) + \
            (pa[2] + pb[2] + pc[2] - 3 * middle[2]) * (ux * vy - uy * vx)
        out.append((a, b, c) if away >= 0 else (a, c, b))
    return positions, out
