"""GLSL sources."""

from . import voxel


def voxel_value_glsl():
    """The per-cell brightness hash, generated from the palette in voxel.py.

    This is the editor's look in one function, and the reason it is generated
    rather than written out: `value_for_cell` and this shader must agree
    exactly, or a merged quad stops matching the voxels under it. Including it
    is what gives a shader the mosaic; `u_voxel_cell` says how big a cell is in
    world units, so the same code serves 1 m editor cubes and half-metre bricks
    on a city building.
    """
    n = len(voxel.VALUE_POOL)
    # `& mask` reproduces Python's `n % len(VALUE_POOL)` only for a power-of-two
    # length; the low bits of a two's-complement int are the floored modulo, so
    # it agrees even for negative coordinates and even when GLSL's 32-bit
    # multiply wraps where Python's does not.
    if n & (n - 1):
        raise ValueError('VALUE_POOL length must be a power of two')
    mult = ', '.join('%.8f' % (v / (voxel.N_VALS - 1)) for v in voxel.VALUE_POOL)
    px, py, pz = voxel.HASH_PRIMES
    return f"""
uniform float u_voxel_cell;    // edge length of one voxel in world units
const float VOXEL_MULT[{n}] = float[{n}]({mult});

float voxel_value(vec3 world, vec3 normal) {{
    // Step half a cell back along the normal first, so a fragment exactly on a
    // face floors into the cell that owns it rather than its neighbour.
    vec3 cell = floor((world - 0.5 * normal * u_voxel_cell) / u_voxel_cell);
    int X = int(cell.x) * {px};
    int Y = int(cell.y) * {py};
    int Z = int(cell.z) * {pz};
    return VOXEL_MULT[(X ^ Y ^ Z) & {n - 1}];
}}
"""


COMMON_LIGHTING = """
uniform vec3 u_sun;            // direction towards the sun
uniform vec3 u_sun_col;
uniform vec3 u_sky_col;
uniform vec3 u_bounce_col;
uniform vec3 u_fog_col;
uniform vec3 u_eye;
uniform float u_fog_density;
uniform float u_time;
uniform float u_exposure;
uniform float u_shadow_strength;
uniform sampler2D u_shadow;
uniform vec2 u_shadow_texel;

float shadow_lookup(vec4 lightpos, float ndl) {
    vec3 p = lightpos.xyz / lightpos.w * 0.5 + 0.5;
    if (p.z > 1.0 || min(p.x, p.y) < 0.0 || max(p.x, p.y) > 1.0) return 1.0;
    float bias = 0.0009 + 0.0022 * (1.0 - ndl);
    float sum = 0.0;
    for (int y = -1; y <= 1; ++y) {
        for (int x = -1; x <= 1; ++x) {
            float d = texture(u_shadow, p.xy + vec2(x, y) * u_shadow_texel).r;
            sum += step(p.z - bias, d);
        }
    }
    // fade the shadow out near the edge of the map
    vec2 e = min(p.xy, 1.0 - p.xy);
    float edge = smoothstep(0.0, 0.06, min(e.x, e.y));
    return mix(1.0, sum / 9.0, edge);
}

vec3 apply_fog(vec3 col, vec3 world) {
    float d = length(world - u_eye) * u_fog_density;
    float f = 1.0 - exp(-d * d);
    return mix(col, u_fog_col, clamp(f, 0.0, 1.0));
}

vec3 tonemap(vec3 col) {
    col *= u_exposure;
    col = col / (col + vec3(0.85)) * 1.42;
    return pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2));
}
"""

SCENE_VS = """#version 330 core
in vec3 in_pos;
in vec3 in_norm;
in vec3 in_col;
in float in_mat;
uniform mat4 u_vp;
uniform mat4 u_light_vp;
out vec3 v_world;
out vec3 v_norm;
out vec3 v_col;
out float v_mat;
out vec4 v_light;
void main() {
    v_world = in_pos;
    v_norm = in_norm;
    v_col = in_col;
    v_mat = in_mat;
    v_light = u_light_vp * vec4(in_pos, 1.0);
    gl_Position = u_vp * vec4(in_pos, 1.0);
}
"""

SCENE_FS = """#version 330 core
in vec3 v_world;
in vec3 v_norm;
in vec3 v_col;
in float v_mat;
in vec4 v_light;
out vec4 f_color;
""" + COMMON_LIGHTING + voxel_value_glsl() + """
void main() {
    vec3 n = normalize(v_norm);
    vec3 albedo = max(v_col, vec3(0.0));
    float gloss = 0.0;

    // Material switch, highest first. Every material needs a branch of its own:
    // fall through and it renders matte.
    if (v_mat > 1.5) {                       // voxel model, from the editor
        // multiplied into the albedo *before* the sRGB decode below, because
        // that is where the editor applies it (VOXEL_FS never decodes). Do it
        // after and the same hash comes out visibly flatter here than there.
        albedo *= voxel_value(v_world, n);
    } else if (v_mat > 0.5) {                // water
        vec2 w = v_world.xz;
        float t = u_time * 0.35;
        n = normalize(n + vec3(
            0.045 * sin(w.x * 0.83 + t * 1.7) + 0.028 * sin(w.y * 0.51 - t),
            0.0,
            0.045 * cos(w.y * 0.77 - t * 1.3) + 0.028 * cos(w.x * 0.44 + t)));
        gloss = 1.0;
    }

    vec3 base = pow(albedo, vec3(2.2));
    float ndl = max(dot(n, u_sun), 0.0);
    float sh = mix(1.0, shadow_lookup(v_light, ndl), u_shadow_strength);
    vec3 ambient = mix(u_bounce_col, u_sky_col, clamp(n.y * 0.5 + 0.5, 0.0, 1.0));
    vec3 col = base * (ambient + u_sun_col * ndl * sh);

    if (gloss > 0.0) {
        vec3 v = normalize(u_eye - v_world);
        vec3 h = normalize(v + u_sun);
        col += u_sun_col * pow(max(dot(n, h), 0.0), 190.0) * 1.6 * sh;
        float fres = pow(1.0 - max(dot(n, v), 0.0), 4.0);
        col = mix(col, u_sky_col * 2.2, fres * 0.45);
    }

    f_color = vec4(tonemap(apply_fog(col, v_world)), 1.0);
}
"""

# --- voxel editor -----------------------------------------------------------

# The editor lights voxels its own way: one fixed direction, flat, no shadows,
# no fog and no tone mapping, so a hue on screen is the hue in the palette. The
# city runs the same models through SCENE_FS instead and lights them with its
# sun; `voxel_value` is the only thing both paths share, and it is the part you
# actually recognise as the voxel look.
VOXEL_VS = """#version 330 core
in vec3 in_pos;
in vec3 in_norm;
in vec3 in_col;
uniform mat4 u_vp;
out vec3 v_world;
out vec3 v_norm;
out vec3 v_col;
void main() {
    v_world = in_pos;
    v_norm = in_norm;
    v_col = in_col;
    gl_Position = u_vp * vec4(in_pos, 1.0);
}
"""

VOXEL_FS = """#version 330 core
in vec3 v_world;
in vec3 v_norm;
in vec3 v_col;
out vec4 f_color;
uniform vec3 u_light;
uniform float u_ambient;
uniform float u_diffuse;
""" + voxel_value_glsl() + """
void main() {
    vec3 n = normalize(v_norm);
    float br = u_ambient + u_diffuse * max(0.0, dot(n, u_light));
    f_color = vec4(v_col * br * voxel_value(v_world, n), 1.0);
}
"""

# Plain coloured lines in world space: the editor's floor grid and the hover /
# placement boxes.
LINE_VS = """#version 330 core
in vec3 in_pos;
in vec3 in_col;
uniform mat4 u_vp;
out vec3 v_col;
void main() {
    v_col = in_col;
    gl_Position = u_vp * vec4(in_pos, 1.0);
}
"""

LINE_FS = """#version 330 core
in vec3 v_col;
out vec4 f_color;
void main() { f_color = vec4(v_col, 1.0); }
"""

# The editor's triangle overlay: the scene layout with everything but the
# position ignored, drawn in one flat colour. LINE_VS cannot do this — it takes
# its colour per vertex, and a wireframe in the model's own hues is invisible
# against the model it is drawn over.
WIRE_VS = """#version 330 core
in vec3 in_pos;
uniform mat4 u_vp;
void main() { gl_Position = u_vp * vec4(in_pos, 1.0); }
"""

WIRE_FS = """#version 330 core
uniform vec3 u_col;
out vec4 f_color;
void main() { f_color = vec4(u_col, 1.0); }
"""

# Flat 2D shapes for the UI, in pixels with y growing downward — the same
# coordinates pygame reports for the mouse, so hit tests and drawing agree.
UI_VS = """#version 330 core
in vec2 in_pos;
in vec4 in_col;
uniform vec2 u_screen;
out vec4 v_col;
void main() {
    v_col = in_col;
    gl_Position = vec4(2.0 * in_pos.x / u_screen.x - 1.0,
                       1.0 - 2.0 * in_pos.y / u_screen.y, 0.0, 1.0);
}
"""

UI_FS = """#version 330 core
in vec4 v_col;
out vec4 f_color;
void main() { f_color = v_col; }
"""

DEPTH_VS = """#version 330 core
in vec3 in_pos;
uniform mat4 u_light_vp;
void main() { gl_Position = u_light_vp * vec4(in_pos, 1.0); }
"""

DEPTH_FS = """#version 330 core
void main() {}
"""

TREE_VS = """#version 330 core
in vec3 in_pos;
in vec3 in_norm;
in float in_part;
in vec2 in_offset;
in float in_height;
in float in_radius;
in float in_tint;
uniform mat4 u_vp;
uniform mat4 u_light_vp;
out vec3 v_world;
out vec3 v_norm;
out vec3 v_col;
out vec4 v_light;

vec3 place() {
    return vec3(in_pos.x * in_radius + in_offset.x,
                in_pos.y * in_height,
                in_pos.z * in_radius + in_offset.y);
}

void main() {
    vec3 p = place();
    vec3 trunk = vec3(0.30, 0.23, 0.17);
    vec3 leaf = vec3(0.20, 0.40, 0.17) * in_tint;
    v_col = mix(leaf, trunk, in_part);
    v_world = p;
    v_norm = normalize(in_norm * vec3(1.0, 1.0, 1.0));
    v_light = u_light_vp * vec4(p, 1.0);
    gl_Position = u_vp * vec4(p, 1.0);
}
"""

TREE_FS = """#version 330 core
in vec3 v_world;
in vec3 v_norm;
in vec3 v_col;
in vec4 v_light;
out vec4 f_color;
""" + COMMON_LIGHTING + """
void main() {
    vec3 n = normalize(v_norm);
    vec3 base = pow(v_col, vec3(2.2));
    float ndl = max(dot(n, u_sun), 0.0);
    float sh = mix(1.0, shadow_lookup(v_light, ndl), u_shadow_strength);
    // wrapped diffuse keeps foliage from going flat black
    float wrap = max(dot(n, u_sun) * 0.6 + 0.4, 0.0);
    vec3 ambient = mix(u_bounce_col, u_sky_col, clamp(n.y * 0.5 + 0.5, 0.0, 1.0));
    vec3 col = base * (ambient + u_sun_col * mix(ndl, wrap, 0.55) * sh);
    f_color = vec4(tonemap(apply_fog(col, v_world)), 1.0);
}
"""

TREE_DEPTH_VS = """#version 330 core
in vec3 in_pos;
in vec2 in_offset;
in float in_height;
in float in_radius;
uniform mat4 u_light_vp;
void main() {
    gl_Position = u_light_vp * vec4(in_pos.x * in_radius + in_offset.x,
                                    in_pos.y * in_height,
                                    in_pos.z * in_radius + in_offset.y, 1.0);
}
"""

SKY_VS = """#version 330 core
in vec2 in_pos;
uniform mat4 u_inv_vp;
uniform vec3 u_eye;
out vec3 v_dir;
void main() {
    vec4 far = u_inv_vp * vec4(in_pos, 1.0, 1.0);
    v_dir = far.xyz / far.w - u_eye;
    gl_Position = vec4(in_pos, 1.0, 1.0);
}
"""

SKY_FS = """#version 330 core
in vec3 v_dir;
out vec4 f_color;
uniform vec3 u_sun;
uniform vec3 u_sun_col;
uniform vec3 u_sky_col;
uniform vec3 u_fog_col;
uniform float u_exposure;
void main() {
    vec3 d = normalize(v_dir);
    float h = clamp(d.y, -1.0, 1.0);
    vec3 zenith = pow(u_sky_col, vec3(2.2)) * 1.6;
    vec3 horizon = pow(u_fog_col, vec3(2.2)) * 1.25;
    vec3 col = mix(horizon, zenith, pow(clamp(h, 0.0, 1.0), 0.55));
    col = mix(col, horizon * 0.75, clamp(-h * 4.0, 0.0, 1.0));

    float sd = max(dot(d, u_sun), 0.0);
    col += pow(u_sun_col, vec3(2.2)) * (pow(sd, 2200.0) * 12.0 + pow(sd, 12.0) * 0.22);

    col *= u_exposure;
    col = col / (col + vec3(0.85)) * 1.42;
    f_color = vec4(pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2)), 1.0);
}
"""

# --- overview map ----------------------------------------------------------

# Baking the map: flat colour, no lighting, orthographic from straight above.
# `u_xform` is (scale_x, scale_y, offset_x, offset_y) taking metres to NDC; the
# y scale is negative because world z runs south and the map wants north up.
# The layer becomes depth, so features can be streamed to the GPU in whatever
# order osmium hands them over and still stack correctly.
MAP_VS = """#version 330 core
in vec2 in_pos;
in vec3 in_col;
in float in_layer;
uniform vec4 u_xform;
uniform float u_layers;
out vec3 v_col;
void main() {
    v_col = in_col;
    float z = 1.0 - 2.0 * (in_layer + 1.0) / u_layers;
    gl_Position = vec4(in_pos * u_xform.xy + u_xform.zw, z, 1.0);
}
"""

MAP_FS = """#version 330 core
in vec3 v_col;
out vec4 f_color;
void main() { f_color = vec4(v_col, 1.0); }
"""

# Viewing the baked map: a textured quad with pan/zoom, plus the region grid
# and the selected cell drawn in the same pass — screen-space distance tests
# are cheaper than a second VAO and keep every line exactly one pixel band
# wide at any zoom, however many thousand cells the grid has.
MAPVIEW_VS = """#version 330 core
in vec2 in_pos;
uniform vec4 u_view;          // (scale_x, scale_y, offset_x, offset_y)
out vec2 v_uv;
void main() {
    v_uv = in_pos * u_view.xy + u_view.zw;
    // in_pos.y = 0 is the *north* edge of v_uv, and north belongs at the top
    // of the window — so flip into clip space, where +1 is up. Without this
    // the map is upside down and the selection square chases the cursor in
    // the wrong direction.
    gl_Position = vec4(in_pos.x * 2.0 - 1.0, 1.0 - in_pos.y * 2.0, 0.0, 1.0);
}
"""

MAPVIEW_FS = """#version 330 core
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D u_tex;
uniform vec4 u_sel;           // selected cell in uv space (x0, y0, x1, y1)
uniform vec2 u_border;        // border half-thickness in uv
uniform vec3 u_edge_col;
uniform vec4 u_grid;          // (cell_u, cell_v, origin_u, origin_v)
uniform vec2 u_grid_n;        // cells across and down
uniform float u_grid_alpha;   // 0 when cells are too small to be worth drawing
void main() {
    vec3 col;
    if (min(v_uv.x, v_uv.y) < 0.0 || max(v_uv.x, v_uv.y) > 1.0) {
        col = vec3(0.16, 0.17, 0.19);          // outside the baked map
    } else {
        // v_uv has v=0 at north, which is how the picker reasons about the
        // map; the texture itself is stored bottom-up, so flip on lookup
        col = texture(u_tex, vec2(v_uv.x, 1.0 - v_uv.y)).rgb;
    }

    // the region grid: distance to the nearest cell boundary, per axis
    vec2 g = (v_uv - u_grid.zw) / u_grid.xy;
    if (u_grid_alpha > 0.0 && all(greaterThanEqual(g, vec2(0.0)))
                           && all(lessThanEqual(g, u_grid_n))) {
        vec2 f = abs(fract(g) - 0.5);
        vec2 d = (0.5 - f) * u_grid.xy;        // uv distance to a gridline
        if (d.x < u_border.x || d.y < u_border.y)
            col = mix(col, vec3(0.20, 0.21, 0.24), u_grid_alpha);
    }

    bool inside = v_uv.x > u_sel.x && v_uv.x < u_sel.z &&
                  v_uv.y > u_sel.y && v_uv.y < u_sel.w;
    // distance to the nearest selection edge, per axis
    vec2 dx = vec2(abs(v_uv.x - u_sel.x), abs(v_uv.x - u_sel.z));
    vec2 dy = vec2(abs(v_uv.y - u_sel.y), abs(v_uv.y - u_sel.w));
    bool on_v = min(dx.x, dx.y) < u_border.x &&
                v_uv.y > u_sel.y - u_border.y && v_uv.y < u_sel.w + u_border.y;
    bool on_h = min(dy.x, dy.y) < u_border.y &&
                v_uv.x > u_sel.x - u_border.x && v_uv.x < u_sel.z + u_border.x;

    if (on_v || on_h) {
        col = u_edge_col;
    } else if (!inside) {
        col = mix(col, vec3(0.10, 0.11, 0.13), 0.28);   // dim what you can't play
    }
    f_color = vec4(col, 1.0);
}
"""

OVERLAY_VS = """#version 330 core
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

OVERLAY_FS = """#version 330 core
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D u_tex;
void main() {
    vec4 c = texture(u_tex, v_uv);
    if (c.a < 0.004) discard;
    f_color = c;
}
"""
