"""Put a monument template on every copy of its monument in a map.

A template is a small map made in a map editor: the game's own monument (the anchor) and, around
it, what you add to it (recyclers, vendors, decorations). Merging is a rigid
move: every other prefab of the template is placed where it stands relative to the anchor, on
each copy of that monument in the target map. The monument itself is left as generated.

A prefab the game does not know (an editor-only object) is dropped, as it could not be spawned.
One the game has only moved is not such a prefab: see MOVED.
"""
import math

import numpy as np

from .mapfile import MapFile, prefab_id

MONUMENT_FOLDER = '/autospawn/monument/'
# Folders of prefabs the game has moved since a template was made: (where they were, where they
# are). A prefab's id is made from its path (mapfile.prefab_id), so a template from before the move names ids the
# game no longer has, and they would be dropped as unknown. Other map tools place them all the
# same, and so does this one: an id of the old folder is the prefab of the same file name in the
# new one. The Hapis vending machines that many Outpost templates carry are such prefabs.
MOVED = (('assets/bundled/prefabs/hapis/', 'assets/bundled/prefabs/modding/hapis/'),)
CATEGORY = 'Decor'          # what a merged prefab is filed under, as map editors file them
# How close a prefab must stand to where the merge puts it to count as merged.
POSITION_TOLERANCE = 0.05
ROTATION_TOLERANCE_DEG = 0.1
SCALE_TOLERANCE = 0.001


def quat(euler):
    """Unity's Quaternion.Euler: degrees about z, then x, then y. Returns (x, y, z, w)."""
    x, y, z = (math.radians(a) * 0.5 for a in euler)
    qx = (math.sin(x), 0.0, 0.0, math.cos(x))
    qy = (0.0, math.sin(y), 0.0, math.cos(y))
    qz = (0.0, 0.0, math.sin(z), math.cos(z))
    return qmul(qy, qmul(qx, qz))


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qinv(q):
    return (-q[0], -q[1], -q[2], q[3])


def qrot(q, v):
    x, y, z, w = qmul(qmul(q, (v[0], v[1], v[2], 0.0)), qinv(q))
    return (x, y, z)


def euler(q):
    """Unity euler angles (degrees, 0 to 360) of a quaternion."""
    x, y, z, w = q
    r02, r22 = 2 * (x * z + y * w), 1 - 2 * (x * x + y * y)
    r10, r11, r12 = 2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)
    if abs(r12) > 0.999999:          # looking straight up or down: z is folded into y
        ex = math.copysign(90.0, -r12)
        ey = math.degrees(math.atan2(-2 * (x * z - y * w), 1 - 2 * (y * y + z * z)))
        ez = 0.0
    else:
        ex = math.degrees(math.asin(-r12))
        ey = math.degrees(math.atan2(r02, r22))
        ez = math.degrees(math.atan2(r10, r11))
    return tuple(round(a % 360.0, 4) % 360.0 for a in (ex, ey, ez))


def moved_ids(names):
    """{id a prefab had in the folder it was moved from: its id now}, for every prefab of the table in a moved folder."""
    out = {}
    for ident, path in names.items():
        for old, new in MOVED:
            if path.startswith(new):
                out[prefab_id(old + path[len(new):])] = int(ident)
    return out


def angle_between(a, b):
    """Degrees between two rotations."""
    dot = abs(sum(p * q for p, q in zip(a, b)))
    return math.degrees(2 * math.acos(min(1.0, dot)))


class Template:
    def __init__(self, path, names, skip=()):
        source = MapFile(path, paths=False)
        anchors = [i for i, ident in enumerate(source.id) if MONUMENT_FOLDER in names.get(int(ident), '')]
        if len(anchors) != 1:
            raise ValueError(f'{path}: {len(anchors)} monuments in the template, one expected')
        anchor = anchors[0]
        self.path = path
        self.anchor_id = int(source.id[anchor])
        self.anchor = names[self.anchor_id]
        origin = source.position[anchor]
        undo = qinv(quat(source.rotation[anchor]))
        self.rows = []          # (id, position and rotation relative to the anchor, scale)
        self.dropped = []       # ids the game has no prefab for
        self.moved = 0          # prefabs named by the id they had before the game moved them (MOVED)
        self.skipped = 0        # prefabs the recipe leaves out (a fragment of their path is in `skip`)
        renamed = moved_ids(names)
        for index, ident in enumerate(source.id):
            if index == anchor:
                continue
            ident = int(ident)
            if ident not in names and ident in renamed:
                ident = renamed[ident]
                self.moved += 1
            if ident not in names:
                self.dropped.append(ident)
                continue
            if any(fragment and fragment in names[ident] for fragment in skip):
                self.skipped += 1
                continue
            local = qrot(undo, source.position[index] - origin)
            self.rows.append((ident, local, qmul(undo, quat(source.rotation[index])), tuple(source.scale[index])))

    def placements(self, target):
        """Where each row goes on `target`: [(id, position, quaternion, scale)], one set per copy."""
        out = []
        for index in np.flatnonzero(target.id == self.anchor_id):
            base = target.position[index]
            turn = quat(target.rotation[index])
            for ident, local, rotation, scale in self.rows:
                moved = qrot(turn, local)
                out.append((ident, (base[0] + moved[0], base[1] + moved[1], base[2] + moved[2]),
                            qmul(turn, rotation), scale))
        return out

    def apply(self, target):
        """Add the template to every copy of its monument. Returns (copies, prefabs added)."""
        copies = int(np.count_nonzero(target.id == self.anchor_id))
        rows = self.placements(target)
        for ident, position, rotation, scale in rows:
            target.add_prefab(CATEGORY, ident, position, euler(rotation), scale)
        return copies, len(rows)

    def compare(self, target, tolerance=POSITION_TOLERANCE, rotation_tolerance=ROTATION_TOLERANCE_DEG,
                scale_tolerance=SCALE_TOLERANCE):
        """Is the template already on `target`, with every complete transform present once?"""
        return compare_all([self], target, tolerance, rotation_tolerance, scale_tolerance)[0]


def compare_all(templates, target, tolerance=POSITION_TOLERANCE, rotation_tolerance=ROTATION_TOLERANCE_DEG,
                scale_tolerance=SCALE_TOLERANCE):
    """Match all declared templates together: a target cannot satisfy two separate templates either.

    Augmenting paths avoid a greedy nearest match stealing another row's only valid candidate.
    """
    templates = list(templates)
    groups = [template.placements(target) for template in templates]
    rows = [row for group in groups for row in group]
    candidates, errors = [], []
    for ident, position, rotation, scale in rows:
        nearby, valid = {}, []
        if all(np.isfinite(values).all() for values in (position, rotation, scale)):
            same = np.flatnonzero(target.id == ident)
            distance = np.linalg.norm(target.position[same] - np.array(position), axis=1)
            for local in np.argsort(distance):
                index = int(same[local])
                if not distance[local] <= tolerance or not np.isfinite(target.rotation[index]).all() or not np.isfinite(target.scale[index]).all():
                    continue
                turn = angle_between(quat(target.rotation[index]), rotation)
                stretch = float(np.max(np.abs(target.scale[index] - np.array(scale))))
                nearby[index] = (float(distance[local]), turn, stretch)
                if turn <= rotation_tolerance and stretch <= scale_tolerance:
                    valid.append(index)
        candidates.append(valid)
        errors.append(nearby)

    matched = {}

    def assign(row, seen):
        for index in candidates[row]:
            if index in seen:
                continue
            seen.add(index)
            if index not in matched or assign(matched[index], seen):
                matched[index] = row
                return True
        return False

    for row in range(len(rows)):
        assign(row, set())
    by_row = {row: index for index, row in matched.items()}
    results, start = [], 0
    for template, group in zip(templates, groups):
        worst_m, worst_deg, worst_scale, categories = 0.0, 0.0, 0.0, {}
        found = 0
        for row in range(start, start + len(group)):
            nearby = errors[row]
            index = by_row.get(row)
            if index is not None:
                found += 1
                categories[target.category[index]] = categories.get(target.category[index], 0) + 1
            elif nearby:
                index = next(iter(nearby))       # show the transform error of the nearest rejected object
            if index is not None:
                distance, turn, stretch = nearby[index]
                worst_m, worst_deg, worst_scale = max(worst_m, distance), max(worst_deg, turn), max(worst_scale, stretch)
        results.append({'copies': int(np.count_nonzero(target.id == template.anchor_id)), 'expected': len(group),
                        'found': found, 'worst_m': round(worst_m, 4), 'worst_deg': round(worst_deg, 3),
                        'worst_scale': round(worst_scale, 6), 'ok': found == len(group), 'categories': categories})
        start += len(group)
    return results
