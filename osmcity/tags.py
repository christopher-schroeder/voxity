"""OSM tag interpretation: what to keep, how wide, how tall, what colour."""

import re

LEVEL_HEIGHT = 3.2
DEFAULT_BUILDING_HEIGHT = 9.0

# highway value -> (width in metres, layer, rgb)
# layer orders the flat ground ribbons; higher wins z-fighting.
ROADS = {
    'motorway':      (14.0, 6, (0.26, 0.25, 0.27)),
    'motorway_link': (7.0,  6, (0.26, 0.25, 0.27)),
    'trunk':         (12.0, 6, (0.27, 0.26, 0.27)),
    'trunk_link':    (6.5,  6, (0.27, 0.26, 0.27)),
    'primary':       (11.0, 5, (0.29, 0.27, 0.28)),
    'primary_link':  (6.0,  5, (0.29, 0.27, 0.28)),
    'secondary':     (9.5,  5, (0.28, 0.27, 0.28)),
    'secondary_link': (5.5, 5, (0.28, 0.27, 0.28)),
    'tertiary':      (8.0,  4, (0.27, 0.27, 0.27)),
    'tertiary_link': (5.0,  4, (0.27, 0.27, 0.27)),
    'unclassified':  (6.0,  3, (0.26, 0.25, 0.26)),
    'residential':   (6.5,  3, (0.26, 0.25, 0.26)),
    'living_street': (5.5,  3, (0.27, 0.26, 0.27)),
    'service':       (3.6,  2, (0.25, 0.24, 0.25)),
    'pedestrian':    (5.0,  2, (0.31, 0.29, 0.28)),
    'track':         (3.0,  1, (0.25, 0.22, 0.19)),
    'footway':       (1.8,  1, (0.29, 0.25, 0.23)),
    'path':          (1.6,  1, (0.27, 0.24, 0.22)),
    'cycleway':      (2.0,  1, (0.24, 0.20, 0.20)),
    'steps':         (1.8,  1, (0.30, 0.27, 0.26)),
    'bridleway':     (1.8,  1, (0.26, 0.24, 0.20)),
}

RAILS = {
    'rail':          (3.2, (0.25, 0.24, 0.25)),
    'light_rail':    (2.8, (0.25, 0.24, 0.25)),
    'subway':        (2.8, (0.23, 0.22, 0.23)),
    'tram':          (2.4, (0.27, 0.26, 0.27)),
    'narrow_gauge':  (2.4, (0.25, 0.24, 0.25)),
    'monorail':      (2.4, (0.26, 0.25, 0.26)),
    'disused':       (2.6, (0.28, 0.27, 0.25)),
}

WATERWAYS = {
    'river': 26.0, 'canal': 16.0, 'stream': 4.0,
    'ditch': 2.5, 'drain': 2.5, 'fairway': 40.0,
}

# surface class -> (layer, rgb).  Layer 0 is the base ground plane.
SURFACES = {
    'water':       (3, (0.20, 0.35, 0.47)),
    'wood':        (2, (0.24, 0.38, 0.21)),
    'grass':       (2, (0.38, 0.51, 0.26)),
    'scrub':       (2, (0.34, 0.44, 0.25)),
    'farmland':    (1, (0.55, 0.53, 0.32)),
    'sand':        (2, (0.72, 0.66, 0.47)),
    'rock':        (2, (0.48, 0.47, 0.45)),
    'wetland':     (2, (0.32, 0.42, 0.34)),
    'pitch':       (3, (0.30, 0.50, 0.30)),
    'cemetery':    (2, (0.31, 0.42, 0.27)),
    'residential': (1, (0.29, 0.31, 0.26)),
    'commercial':  (1, (0.31, 0.30, 0.29)),
    'industrial':  (1, (0.29, 0.29, 0.29)),
    'retail':      (1, (0.31, 0.29, 0.28)),
    'railway':     (1, (0.30, 0.29, 0.29)),
    'parking':     (3, (0.28, 0.27, 0.28)),
    'paved':       (3, (0.33, 0.32, 0.31)),
    'construction': (1, (0.40, 0.37, 0.30)),
}

_SURFACE_RULES = (
    ('natural', {'water': 'water', 'bay': 'water', 'strait': 'water',
                 'wetland': 'wetland', 'wood': 'wood', 'scrub': 'scrub',
                 'heath': 'scrub', 'grassland': 'grass', 'sand': 'sand',
                 'beach': 'sand', 'shingle': 'sand', 'bare_rock': 'rock',
                 'scree': 'rock'}),
    ('waterway', {'riverbank': 'water', 'dock': 'water'}),
    ('landuse', {'reservoir': 'water', 'basin': 'water', 'forest': 'wood',
                 'grass': 'grass', 'meadow': 'grass', 'village_green': 'grass',
                 'orchard': 'wood', 'vineyard': 'grass', 'allotments': 'grass',
                 'recreation_ground': 'grass', 'cemetery': 'cemetery',
                 'farmland': 'farmland', 'farmyard': 'farmland',
                 'residential': 'residential', 'commercial': 'commercial',
                 'industrial': 'industrial', 'retail': 'retail',
                 'railway': 'railway', 'construction': 'construction',
                 'brownfield': 'construction', 'greenfield': 'grass',
                 'quarry': 'rock'}),
    ('leisure', {'park': 'grass', 'garden': 'grass', 'golf_course': 'grass',
                 'pitch': 'pitch', 'playground': 'sand', 'common': 'grass',
                 'nature_reserve': 'wood', 'track': 'pitch',
                 'swimming_pool': 'water', 'marina': 'water'}),
    ('amenity', {'parking': 'parking', 'grave_yard': 'cemetery',
                 'school': 'paved', 'university': 'paved'}),
    ('man_made', {'pier': 'paved', 'bridge': 'paved'}),
    ('area:highway', {'pedestrian': 'paved', 'footway': 'paved',
                      'residential': 'paved', 'service': 'paved'}),
    ('highway', {'pedestrian': 'paved', 'footway': 'paved', 'platform': 'paved',
                 'services': 'paved', 'rest_area': 'paved'}),
    ('aeroway', {'apron': 'paved', 'runway': 'paved', 'taxiway': 'paved'}),
)

# building tag -> roughly how tall it wants to be when unmapped
BUILDING_DEFAULT_HEIGHT = {
    'garage': 3.0, 'garages': 3.0, 'shed': 3.0, 'hut': 3.0, 'carport': 2.8,
    'roof': 3.5, 'greenhouse': 4.0, 'kiosk': 3.5, 'bungalow': 4.5,
    'house': 7.5, 'detached': 8.0, 'semidetached_house': 8.5, 'terrace': 10.0,
    'residential': 12.0, 'apartments': 15.0, 'commercial': 14.0,
    'retail': 10.0, 'office': 20.0, 'industrial': 10.0, 'warehouse': 10.0,
    'church': 18.0, 'cathedral': 30.0, 'chapel': 10.0, 'hospital': 18.0,
    'school': 11.0, 'university': 16.0, 'train_station': 14.0, 'hotel': 20.0,
    'civic': 14.0, 'public': 14.0, 'stadium': 22.0, 'hangar': 14.0,
}

# Only the visually distinctive types get a fixed colour; everything else
# draws from WALL_PALETTE so a street isn't one flat tone.
BUILDING_COLOURS = {
    'church': (0.70, 0.63, 0.53), 'cathedral': (0.70, 0.63, 0.53),
    'chapel': (0.70, 0.63, 0.53),
    'industrial': (0.53, 0.54, 0.57), 'warehouse': (0.55, 0.54, 0.55),
    'hangar': (0.55, 0.55, 0.58), 'shed': (0.50, 0.47, 0.44),
    'garage': (0.56, 0.54, 0.52), 'garages': (0.56, 0.54, 0.52),
    'greenhouse': (0.72, 0.78, 0.76), 'roof': (0.58, 0.56, 0.54),
    'construction': (0.62, 0.60, 0.52), 'carport': (0.56, 0.54, 0.52),
}
DEFAULT_BUILDING_COLOUR = (0.70, 0.65, 0.60)

ROOF_COLOURS = {
    'red': (0.55, 0.24, 0.18), 'brown': (0.35, 0.26, 0.20),
    'grey': (0.38, 0.38, 0.40), 'gray': (0.38, 0.38, 0.40),
    'black': (0.16, 0.16, 0.18), 'green': (0.24, 0.36, 0.26),
    'blue': (0.24, 0.32, 0.45), 'white': (0.82, 0.82, 0.80),
    'copper': (0.35, 0.55, 0.48), 'silver': (0.66, 0.68, 0.70),
    'orange': (0.68, 0.38, 0.18), 'yellow': (0.72, 0.66, 0.32),
}
DEFAULT_ROOF_COLOUR = (0.42, 0.36, 0.34)

# Hamburg is a brick city: red and dark clinker, plus render and plaster.
WALL_PALETTE = (
    (0.55, 0.30, 0.24),      # red brick
    (0.45, 0.26, 0.23),      # dark clinker
    (0.66, 0.42, 0.33),      # light brick
    (0.76, 0.72, 0.66),      # pale render
    (0.68, 0.65, 0.61),      # grey render
    (0.72, 0.67, 0.54),      # sand plaster
    (0.60, 0.62, 0.63),      # concrete
)
WALL_WEIGHTS = (0.17, 0.09, 0.11, 0.22, 0.19, 0.13, 0.09)

ROOF_PALETTE = (
    (0.26, 0.26, 0.28),      # slate
    (0.19, 0.19, 0.21),      # dark bitumen
    (0.44, 0.22, 0.16),      # clay tile
    (0.34, 0.22, 0.18),      # brown tile
    (0.38, 0.38, 0.40),      # zinc
    (0.30, 0.33, 0.32),      # weathered
)
ROOF_WEIGHTS = (0.30, 0.22, 0.16, 0.10, 0.14, 0.08)

_NUM = re.compile(r'^\s*(-?\d+(?:[.,]\d+)?)')
_HEX = re.compile(r'^#?([0-9a-fA-F]{6})$')


def parse_length(value):
    """Parse an OSM length ('12', '12 m', "40'", '12,5')."""
    if not value:
        return None
    m = _NUM.match(value)
    if not m:
        return None
    n = float(m.group(1).replace(',', '.'))
    tail = value[m.end():].strip().lower()
    if tail.startswith("'") or tail.startswith('ft'):
        n *= 0.3048
    return n


def parse_colour(value):
    if not value:
        return None
    v = value.strip().lower()
    if v in ROOF_COLOURS:
        return ROOF_COLOURS[v]
    m = _HEX.match(v)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return None


def surface_class(tags):
    """Classify an area feature, or None if we don't draw it."""
    for key, mapping in _SURFACE_RULES:
        v = tags.get(key)
        if v is not None:
            cls = mapping.get(v)
            if cls:
                return cls
    return None


def building_height(tags, oid=0):
    """Return (height, min_height) in metres for a building."""
    h = parse_length(tags.get('height') or tags.get('building:height'))
    if h is None:
        levels = parse_length(tags.get('building:levels'))
        if levels is not None:
            roof_levels = parse_length(tags.get('roof:levels')) or 0.0
            h = (levels + roof_levels) * LEVEL_HEIGHT + 1.0
    if h is None:
        # nothing mapped: guess from the type and wobble it so whole
        # neighbourhoods don't come out as one flat slab
        kind = tags.get('building') or tags.get('building:part') or 'yes'
        h = BUILDING_DEFAULT_HEIGHT.get(kind, DEFAULT_BUILDING_HEIGHT)
        h *= 0.78 + 0.44 * _hash01(oid, 4)
    base = parse_length(tags.get('min_height'))
    if base is None:
        lv = parse_length(tags.get('building:min_level'))
        base = lv * LEVEL_HEIGHT if lv is not None else 0.0
    h = max(2.0, min(h, 400.0))
    return h, max(0.0, min(base, h - 1.0))


def _hash01(seed, salt=0):
    h = ((seed ^ (salt * 0x9E3779B1)) * 2654435761) & 0xFFFFFFFF
    h ^= h >> 15
    return ((h * 2246822519) & 0xFFFFFFFF) / 4294967295.0


def _pick(palette, weights, r):
    total = 0.0
    for colour, w in zip(palette, weights):
        total += w
        if r < total:
            return colour
    return palette[-1]


def building_colours(tags, oid=0):
    """Wall and roof colour. Untagged buildings get a stable random pick from
    a north-German palette: brick, plaster and render walls under slate roofs."""
    kind = tags.get('building') or tags.get('building:part') or 'yes'
    wall = (parse_colour(tags.get('building:colour'))
            or parse_colour(tags.get('colour'))
            or BUILDING_COLOURS.get(kind))
    if wall is None:
        wall = _pick(WALL_PALETTE, WALL_WEIGHTS, _hash01(oid, 1))
    roof = parse_colour(tags.get('roof:colour'))
    if roof is None:
        roof = _pick(ROOF_PALETTE, ROOF_WEIGHTS, _hash01(oid, 2))
    return wall, roof


def roof_shape(tags, oid=0):
    """'flat' or 'gabled'. Honour roof:shape when it is mapped."""
    shape = tags.get('roof:shape')
    if shape in ('flat', 'dome', 'skillion'):
        return 'flat'
    if shape in ('gabled', 'hipped', 'pyramidal', 'half-hipped', 'gambrel',
                 'round', 'mansard', 'saltbox'):
        return 'gabled'
    kind = tags.get('building') or ''
    if kind in ('house', 'detached', 'semidetached_house', 'terrace', 'hut',
                'bungalow', 'farm', 'chapel', 'shed', 'residential'):
        return 'gabled' if _hash01(oid, 3) < 0.85 else 'flat'
    if kind in ('apartments', 'yes', ''):
        return 'gabled' if _hash01(oid, 3) < 0.35 else 'flat'
    return 'flat'


def is_building(tags):
    v = tags.get('building')
    if v and v != 'no':
        return True
    v = tags.get('building:part')
    return bool(v and v != 'no')


def is_underground(tags):
    if tags.get('tunnel') in ('yes', 'building_passage', 'culvert', 'covered'):
        return True
    if tags.get('location') == 'underground':
        return True
    try:
        return int(tags.get('layer', '0')) < 0
    except ValueError:
        return False


def structure_height(tags):
    """Extra elevation for bridges, so they read as crossings from above."""
    if tags.get('bridge') in (None, 'no'):
        return 0.0
    try:
        layer = max(1, int(tags.get('layer', '1')))
    except ValueError:
        layer = 1
    return min(layer, 3) * 4.5
