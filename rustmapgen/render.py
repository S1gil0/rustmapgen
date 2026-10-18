"""Draw a map file as the game draws its own map (the one players open with G).

The colours, the order the ground layers are mixed in, the sun and the sea are those of the
game's `MapImageRenderer`. Two things differ, on purpose: rivers and lakes are tinted (the game
leaves them to the ground texture), and the sea around the square is narrower: as wide as the
offshore monuments need, where the game draws 1,000 m.

Every map goes through this same function, so two maps can be compared by eye.
"""
import os

import numpy as np
from PIL import Image

START = (0.28627452, 23 / 85, 0.24705884)
WATER = (0.16941601, 0.31755757, 0.36200002)
OFFSHORE = (0.04090196, 0.22060032, 14 / 51)
# (index of the ground layer in the splat map, colour), in the order the game blends them.
GROUND = ((7, (0.25, 37 / 152, 0.22039475)),          # gravel
          (6, (7 / 51, 0.2784314, 0.2761563)),         # stones
          (3, (0.4, 0.39379844, 0.37519377)),          # rock
          (0, (0.6, 0.47959462, 0.33)),                # dirt
          (4, (0.35486364, 0.37, 0.2035)),             # grass
          (5, (0.24843751, 0.3, 9 / 128)),             # forest
          (2, (0.7, 0.65968585, 0.5277487)),           # sand
          (1, (0.86274517, 0.9294118, 0.94117653)))    # snow
SUN = np.array((0.95, 2.87, 2.37)) / np.linalg.norm((0.95, 2.87, 2.37))
SUN_POWER, CONTRAST, BRIGHTNESS, MAX_DEPTH = 0.65, 0.94, 1.05, 50.0
# Sea drawn around the square, in metres on each side. The game puts the offshore monuments (the oil
# rigs) 100 to 500 m out from it (PlaceMonumentsOffshore), so a rig at the far limit is whole on the picture.
MARGIN = 600


def _sample(layer, pixels, size, margin):
    """A square layer brought to `pixels` a side by picking the nearest cell, over the square and
    `margin` metres around it. Outside the square the edge cells go on, as in the game's own map."""
    cells = layer.shape[-1]
    metres = (np.arange(pixels) + 0.5) * (size + 2.0 * margin) / pixels - margin
    index = np.clip(np.floor(metres / size * cells), 0, cells - 1).astype(np.intp)
    return layer[..., index[:, None], index[None, :]]


def render(world, pixels=2048, margin=MARGIN):
    """The map as an RGB image, north up, with `margin` metres of sea around the square."""
    size = float(world.size)
    height = _sample(world.metres('height'), pixels, size, margin)
    colour = np.empty((pixels, pixels, 3), dtype=np.float32)
    colour[:] = START
    splat = world.layer('splat')
    if splat is not None:
        for index, tint in GROUND:
            if index < splat.shape[0]:
                weight = (_sample(splat[index], pixels, size, margin).astype(np.float32) / 255.0)[..., None]
                colour += (np.array(tint, dtype=np.float32) - colour) * weight

    metres = (size + 2.0 * margin) / pixels
    dz, dx = np.gradient(height, metres)
    scale = 1.0 / np.sqrt(dx * dx + dz * dz + 1.0)
    light = np.maximum((-dx * SUN[0] + SUN[1] - dz * SUN[2]) * scale, 0.0)
    lit = colour + (light[..., None] - 0.5) * SUN_POWER * colour
    lit = (lit - 0.5) * CONTRAST + 0.5

    water = world.layer('water')
    if water is not None:
        surface = _sample(world.metres('water'), pixels, size, margin)
        inland = (surface > height + 0.1) & (height > -0.5)
        lit[inland] += (np.array(WATER, dtype=np.float32) - lit[inland]) * 0.75

    depth = -height
    sea = depth > 0.0
    shallow = np.clip(0.5 + depth / 5.0, 0.0, 1.0)[..., None]
    deep = np.clip(depth / MAX_DEPTH, 0.0, 1.0)[..., None]
    wet = colour + (np.array(WATER, dtype=np.float32) - colour) * shallow
    wet = wet + (np.array(OFFSHORE, dtype=np.float32) - wet) * deep
    out = np.where(sea[..., None], wet, lit) * BRIGHTNESS
    image = (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(image[::-1], 'RGB')          # row 0 of a layer is the south edge


# ---- a picture to share ---------------------------------------------------------------------------
# The panel draws rails and monument names over the terrain picture; for a picture to post
# elsewhere they are drawn into it, in the panel's own colours.
RAIL_UNDER, RAIL_OVER = (20, 17, 12), (244, 241, 232)
FONTS = ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 'DejaVuSans-Bold.ttf', 'arialbd.ttf', 'Arial Bold.ttf')
# Which monuments get their name first when two would overlap, and which are small places, with a smaller dot
# (app.js has the same lists).
RANK = ['xlarge', 'large', 'medium', 'harbor', 'fishing_village', 'offshore', 'military_bases', 'arctic_bases',
        'roadside', 'small', 'jungle_ruins', 'lighthouse', 'cave', 'ice_lakes', 'swamp', 'tiny', 'underwater_lab']
MINOR = {'tiny', 'cave', 'ice_lakes', 'underwater_lab', 'swamp', 'lighthouse', 'jungle_ruins'}
HIDDEN = {'maintainables'}
SAFE = {'compound', 'bandit_town', 'fishing_village_a', 'fishing_village_b', 'fishing_village_c', 'stables_a', 'stables_b'}


def _dashes(points, on, off):
    """The stretches of a line that a dash pattern draws."""
    out, phase, drawing, current = [], 0.0, True, []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        done = 0.0
        if drawing and not current:
            current = [(x0, y0)]
        while length - done > 1e-6:
            step = min((on if drawing else off) - phase, length - done)
            done += step
            phase += step
            point = (x0 + (x1 - x0) * done / length, y0 + (y1 - y0) * done / length)
            if drawing:
                current.append(point)
            if phase >= (on if drawing else off) - 1e-6:
                if drawing and len(current) > 1:
                    out.append(current)
                drawing, phase = not drawing, 0.0
                current = [point] if drawing else []
    if drawing and len(current) > 1:
        out.append(current)
    return out


def with_rails(base, size, rails, margin=0):
    """The terrain picture with the rails drawn as on the panel: a dark bed, a light dashed line."""
    from PIL import ImageDraw
    image = base.copy()
    draw = ImageDraw.Draw(image)
    scale = image.width / 900.0                 # the panel's stroke widths are for a stage about 900 px wide
    side = size + 2.0 * margin
    half = side / 2.0
    paths = []
    for flat in rails or []:
        points = [((flat[i] + half) / side * image.width, (half - flat[i + 1]) / side * image.height) for i in range(0, len(flat) - 1, 2)]
        if len(points) > 1:
            paths.append(points)
    for points in paths:
        draw.line(points, fill=RAIL_UNDER, width=max(3, round(4.2 * scale)), joint='curve')
    for points in paths:
        for dash in _dashes(points, 7 * scale, 5 * scale):
            draw.line(dash, fill=RAIL_OVER, width=max(2, round(2 * scale)))
    return image


def name_places(width, size, monuments, margin, measure):
    """Where each monument's dot and name go on a picture `width` pixels wide: [(monument, x, y, radius,
    small, box)], the more important first. measure(label, small) gives the text's (left, top, right, bottom).
    Every dot gets its name: under it as on the panel, then over it, to its right and its left, then the
    corners, a step further out when all of those are taken, and with no room at all under it all the same."""
    scale = width / 900.0
    side = size + 2.0 * margin
    half = side / 2.0
    rank = {folder: index for index, folder in enumerate(RANK)}
    shown = sorted((m for m in monuments if m.get('folder') not in HIDDEN), key=lambda m: (rank.get(m.get('folder'), len(RANK)), m['label'], m['x']))
    taken, dots = [], []

    def inside(box):
        return box[0] >= 2 and box[1] >= 2 and box[2] <= width - 2 and box[3] <= width - 2

    def free(box):
        return inside(box) and not any(box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3] for other in taken)

    for m in shown:
        x, y = (m['x'] + half) / side * width, (half - m['z']) / side * width
        small = m.get('folder') in MINOR
        radius = (3 if small else 4.5) * scale
        taken.append((x - radius, y - radius, x + radius, y + radius))
        dots.append((m, x, y, radius, small))
    pad = 4 * scale
    out = []
    for m, x, y, radius, small in dots:
        left, top, right, bottom = measure(m['label'], small)
        w, h = right - left + 2 * pad, bottom - top + pad
        spots = []
        for gap in (7 * scale, 16 * scale, 28 * scale):
            corner = gap * 0.7
            spots += [(x - w / 2, y + gap), (x - w / 2, y - gap - h), (x + gap, y - h / 2), (x - gap - w, y - h / 2),
                      (x + corner, y + corner), (x - corner - w, y + corner), (x + corner, y - corner - h), (x - corner - w, y - corner - h)]
        boxes = [(bx, by, bx + w, by + h) for bx, by in spots]
        box = next((b for b in boxes if free(b)), None) or next((b for b in boxes if inside(b)), None)
        if box is None:
            bx, by = min(max(x - w / 2, 2), width - 2 - w), min(max(y + 7 * scale, 2), width - 2 - h)
            box = (bx, by, bx + w, by + h)
        taken.append(box)
        out.append((m, x, y, radius, small, box))
    return out


def with_names(base, size, monuments, margin=0):
    """A picture with a dot on every monument and its name beside it. Every dot is named, the small
    places in a smaller letter: the panel shows only the names it has room for at its zoom."""
    from PIL import ImageDraw
    image = base.convert('RGBA')
    layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    scale = image.width / 900.0
    fonts = {False: _font(round(11 * scale)), True: _font(round(9.5 * scale))}
    pad = 4 * scale
    places = name_places(image.width, size, monuments, margin, lambda label, small: draw.textbbox((0, 0), label, font=fonts[small]))
    for m, x, y, radius, small, box in places:
        left, top = draw.textbbox((0, 0), m['label'], font=fonts[small])[:2]
        draw.rounded_rectangle(box, radius=3 * scale, fill=(8, 10, 12, 190))
        draw.text((box[0] + pad - left, box[1] + pad / 2 - top), m['label'], font=fonts[small], fill=(255, 255, 255, 255))
    for m, x, y, radius, small, box in places:
        fill = (53, 208, 106, 255) if m.get('prefab') in SAFE else (255, 255, 255, 235 if not small else 200)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=(8, 10, 12, 255), width=max(1, round(1.5 * scale)))
    return Image.alpha_composite(image, layer).convert('RGB')


def _font(size):
    from PIL import ImageFont
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:          # Pillow before 10.1 has one size only
        return ImageFont.load_default()


def save(world, folder, pixels=2048, thumb=480):
    """terrain.webp (with the sea around the square, MARGIN) and thumb.webp (the square alone) in
    `folder`. Returns their names."""
    image = render(world, pixels)
    image.save(f'{folder}/terrain.webp', 'WEBP', quality=86, method=4)
    edge = pixels * MARGIN / (float(world.size) + 2.0 * MARGIN)
    square = image.crop((round(edge), round(edge), round(pixels - edge), round(pixels - edge)))
    square.resize((thumb, thumb), Image.LANCZOS).save(f'{folder}/thumb.webp', 'WEBP', quality=82, method=4)
    return {'terrain': 'terrain.webp', 'thumb': 'thumb.webp'}


def labelled(folder, entry):
    """map_labels.webp: the terrain with the rails and every monument's name, to share."""
    base = Image.open(f'{folder}/terrain.webp').convert('RGB')
    margin = entry.get('margin_m', 0)          # maps drawn before the sea around the square have none
    railed = with_rails(base, entry['size'], (entry.get('lines') or {}).get('Rail'), margin)
    with_names(railed, entry['size'], entry['facts']['monuments'], margin).save(f'{folder}/map_labels.webp', 'WEBP', quality=86, method=4)
    return 'map_labels.webp'
