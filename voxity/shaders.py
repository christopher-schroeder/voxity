"""GLSL sources."""

import numpy as np

from . import voxel


def voxel_value_glsl():
    """The per-cell brightness hash, generated from the palette in voxel.py.

    This is the editor's look in one function, and the reason it is generated
    rather than written out: `value_for_cell` and this shader must agree
    exactly, or a merged quad stops matching the voxels under it. Including it
    is what gives a shader the mosaic. It hashes the model-cell position the
    mesher put in the vertex (see mesh.py), so it needs no cell size and no
    normal, and one program serves any model at any scale.
    """
    n = len(voxel.VALUE_POOL)
    # `& mask` reproduces Python's `% len(VALUE_POOL)` only for a power-of-two
    # length.
    if n & (n - 1):
        raise ValueError('VALUE_POOL length must be a power of two')
    mult = ', '.join('%.8f' % (v / (voxel.N_VALS - 1)) for v in voxel.VALUE_POOL)
    px, py, pz = voxel.HASH_PRIMES
    mix = '\n'.join(
        f'    h ^= h >> {shift}u;' + (f'\n    h *= {mul:#010x}u;' if mul else '')
        for shift, mul in voxel.HASH_MIX)
    return f"""
const float VOXEL_MULT[{n}] = float[{n}]({mult});

// `uint` rather than `int`: the wrap this depends on is defined for unsigned
// and undefined for signed, and `uint(int)` is the same two's-complement
// reinterpretation Python's `& 0xFFFFFFFF` performs, so negative cells agree.
uint cell_hash(ivec3 c) {{
    uint h = uint(c.x) * {px:#010x}u
           ^ uint(c.y) * {py:#010x}u
           ^ uint(c.z) * {pz:#010x}u;
{mix}
    return h;
}}

// `cell` is the fragment's position in the **model's own cells**, already
// nudged half a cell inside the surface by the mesher (see mesh.py), so this
// needs neither the face normal nor a cell size — and the pattern turns with
// the model instead of staying nailed to the world axes.
float voxel_value(vec3 cell) {{
    return VOXEL_MULT[cell_hash(ivec3(floor(cell))) & {n - 1}u];
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
"""

# --- post processing --------------------------------------------------------
#
# Every geometry shader here hands over **linear light**, and POST_FS is the
# only place a picture is made of it. That is not tidiness: ambient occlusion
# has to multiply the light *before* the curve compresses it, or the crevices it
# darkens come back out grey. It is also what lets the sky be graded like the
# ground it meets, instead of the two being tone mapped apart and meeting at a
# seam on the horizon.


def ssao_kernel_glsl(n=16, seed=5):
    """A hemisphere of sample offsets, generated rather than written out.

    The shader has no random number generator, so the kernel is a constant
    table; generating it keeps the count in one place and lets the falloff be
    stated as arithmetic instead of as 16 hand-typed triples. Samples are
    pulled towards the origin (the squared term), because what reads as ambient
    occlusion is contact darkening a few centimetres wide, not a smooth dome.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        v = rng.normal(size=3)
        v[2] = abs(v[2]) + 0.15          # keep it in the hemisphere, off the plane
        v /= np.linalg.norm(v)
        v *= 0.25 + 0.75 * ((i + 1) / n) ** 2
        rows.append(f'vec3({v[0]:.5f}, {v[1]:.5f}, {v[2]:.5f})')
    return (f'const int AO_SAMPLES = {n};\n'
            f'const vec3 AO_KERNEL[{n}] = vec3[{n}](\n    '
            + ',\n    '.join(rows) + ');\n')


# A fullscreen triangle. Three vertices rather than a quad's six, so there is no
# diagonal seam where the two halves meet and derivatives stay well defined.
POST_VS = """#version 330 core
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos * 0.5 + 0.5;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

AO_FS = """#version 330 core
in vec2 v_uv;
out float f_ao;
uniform sampler2D u_depth;
uniform mat4 u_proj;
uniform mat4 u_inv_proj;
uniform float u_radius;
uniform float u_bias;
uniform float u_strength;
""" + ssao_kernel_glsl() + """
vec3 view_at(vec2 uv) {
    float d = texture(u_depth, uv).r;
    vec4 p = u_inv_proj * vec4(uv * 2.0 - 1.0, d * 2.0 - 1.0, 1.0);
    return p.xyz / p.w;
}

void main() {
    float d = texture(u_depth, v_uv).r;
    if (d >= 0.99999) {           // sky: nothing there to be occluded by
        f_ao = 1.0;
        return;
    }
    vec3 P = view_at(v_uv);
    // The normal comes from the depth buffer's own derivatives rather than
    // from a normal buffer: the layout has no room for one, and the only place
    // this is wrong is across a depth discontinuity, where the range check
    // below throws the sample away anyway.
    vec3 dx = dFdx(P), dy = dFdy(P);
    vec3 N = normalize(cross(dx, dy));
    if (dot(N, normalize(-P)) < 0.0) N = -N;   // face the camera, not just +z

    // A surface seen edge-on moves many metres across one pixel, and the
    // constant bias below is then far too small to keep it from occluding
    // itself — whole roofs come out black. Growing the bias with how fast depth
    // is changing here is what keeps them lit.
    float slope = max(abs(dx.z), abs(dy.z));
    float bias = u_bias + 2.0 * slope;

    // Interleaved gradient noise: a different kernel rotation per pixel, in a
    // 4x4 pattern the blur in POST_FS is sized to average out exactly.
    float ang = 6.2831853 * fract(52.9829189 *
                fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))));
    vec3 rnd = vec3(cos(ang), sin(ang), 0.0);
    vec3 T = normalize(rnd - N * dot(rnd, N));
    mat3 tbn = mat3(T, cross(N, T), N);

    float occ = 0.0;
    for (int i = 0; i < AO_SAMPLES; ++i) {
        vec3 sp = P + tbn * AO_KERNEL[i] * u_radius;
        vec4 cp = u_proj * vec4(sp, 1.0);
        vec2 suv = cp.xy / cp.w * 0.5 + 0.5;
        if (any(lessThan(suv, vec2(0.0))) || any(greaterThan(suv, vec2(1.0))))
            continue;
        // view space looks down -z, so a *larger* z is nearer the camera
        float sz = view_at(suv).z;
        float range = smoothstep(0.0, 1.0, u_radius / max(abs(P.z - sz), 1e-4));
        occ += (sz >= sp.z + bias ? 1.0 : 0.0) * range;
    }
    f_ao = clamp(1.0 - u_strength * occ / float(AO_SAMPLES), 0.0, 1.0);
}
"""

POST_FS = """#version 330 core
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D u_colour;
uniform sampler2D u_ao;
uniform vec2 u_ao_texel;
uniform vec3 u_ao_tint;
uniform float u_ao_power;
uniform float u_exposure;
uniform float u_contrast;
uniform float u_saturation;
uniform vec3 u_shadow_tint;
uniform vec3 u_light_tint;
uniform float u_vignette;

// Narkowicz's fit to the ACES filmic curve. The Reinhard it replaced
// (x / (x + 0.85)) put a sunlit wall and a wall in shadow within a factor of
// 1.6 of each other on screen when the light on them differed by seven — the
// whole scene came out looking like an overcast day whatever the sun was doing.
vec3 filmic(vec3 x) {
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14),
                 0.0, 1.0);
}

void main() {
    // The colour buffer is supersampled and read with LINEAR, so this fetch is
    // the downsample: it antialiases the voxel mosaic and the value hash too,
    // which multisampling never could — both are computed inside a triangle,
    // where MSAA has exactly one sample to work with.
    vec3 col = texture(u_colour, v_uv).rgb;

    // 3x3 of the half-resolution AO, which is where the kernel's per-pixel
    // rotation is meant to average back out
    float ao = 0.0;
    for (int y = -1; y <= 1; ++y)
        for (int x = -1; x <= 1; ++x)
            ao += texture(u_ao, v_uv + vec2(x, y) * u_ao_texel).r;
    ao /= 9.0;
    // The power deepens what is already dark without touching what is open:
    // linear AO reads as a smudge, and a crevice in the reference this is aimed
    // at is nearly black. Occlusion fades towards a warm tint rather than grey,
    // because what lights a crevice is whatever is around it, and around it is
    // brick.
    col *= mix(u_ao_tint, vec3(1.0), pow(ao, u_ao_power));

    col = filmic(col * u_exposure);

    float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
    col = mix(vec3(l), col, u_saturation);
    col = clamp((col - 0.5) * u_contrast + 0.5, 0.0, 1.0);
    col *= mix(u_shadow_tint, u_light_tint, l);   // split tone, cool to warm

    // smoothstep's edges must ascend — GLSL leaves edge0 > edge1 undefined, and
    // on this driver it silently returns 1 everywhere, i.e. no vignette at all
    vec2 q = v_uv - 0.5;
    float r = length(q * vec2(1.0, 0.92));
    col *= mix(1.0, 1.0 - u_vignette, smoothstep(0.20, 0.72, r));

    f_color = vec4(pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2)), 1.0);
}
"""

SCENE_VS = """#version 330 core
in vec3 in_pos;
in vec3 in_norm;
in vec3 in_col;
in float in_mat;
in vec3 in_cell;
uniform mat4 u_vp;
uniform mat4 u_light_vp;
out vec3 v_world;
out vec3 v_norm;
out vec3 v_col;
out float v_mat;
out vec3 v_cell;
out vec4 v_light;
void main() {
    v_world = in_pos;
    v_norm = in_norm;
    v_col = in_col;
    v_mat = in_mat;
    v_cell = in_cell;
    v_light = u_light_vp * vec4(in_pos, 1.0);
    gl_Position = u_vp * vec4(in_pos, 1.0);
}
"""

SCENE_FS = """#version 330 core
in vec3 v_world;
in vec3 v_norm;
in vec3 v_col;
in float v_mat;
in vec3 v_cell;
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
        albedo *= voxel_value(v_cell);
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

    // linear light, not a picture: exposure, the curve and the grade all
    // happen in POST_FS, after ambient occlusion has multiplied this
    f_color = vec4(apply_fog(col, v_world), 1.0);
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
in vec3 in_cell;
uniform mat4 u_vp;
out vec3 v_norm;
out vec3 v_col;
out vec3 v_cell;
void main() {
    v_norm = in_norm;
    v_col = in_col;
    v_cell = in_cell;
    gl_Position = u_vp * vec4(in_pos, 1.0);
}
"""

VOXEL_FS = """#version 330 core
in vec3 v_norm;
in vec3 v_col;
in vec3 v_cell;
out vec4 f_color;
uniform vec3 u_light;
uniform float u_ambient;
uniform float u_diffuse;
""" + voxel_value_glsl() + """
void main() {
    vec3 n = normalize(v_norm);
    float br = u_ambient + u_diffuse * max(0.0, dot(n, u_light));
    f_color = vec4(v_col * br * voxel_value(v_cell), 1.0);
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
    vec3 leaf = vec3(0.30, 0.36, 0.13) * in_tint;   // olive, not grass
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
    // linear light, not a picture: exposure, the curve and the grade all
    // happen in POST_FS, after ambient occlusion has multiplied this
    f_color = vec4(apply_fog(col, v_world), 1.0);
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
void main() {
    vec3 d = normalize(v_dir);
    float h = clamp(d.y, -1.0, 1.0);
    // scaled well under the ambient the ground gets: this is the only surface
    // with nothing in front of it, so at parity it blows out and the top of the
    // frame turns into a cream wall
    vec3 zenith = pow(u_sky_col, vec3(2.2)) * 2.6;
    vec3 horizon = pow(u_fog_col, vec3(2.2)) * 0.85;
    vec3 col = mix(horizon, zenith, pow(clamp(h, 0.0, 1.0), 0.55));
    col = mix(col, horizon * 0.75, clamp(-h * 4.0, 0.0, 1.0));

    float sd = max(dot(d, u_sun), 0.0);
    col += pow(u_sun_col, vec3(2.2)) * (pow(sd, 2200.0) * 12.0 + pow(sd, 12.0) * 0.22);

    f_color = vec4(col, 1.0);      // linear, like the geometry around it
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
