"""Matrix helpers and the two cameras: fly (city) and orbit (editor)."""

import math

import numpy as np
import pygame


def to_gl(m):
    """Row-major maths matrix -> column-major buffer for GLSL."""
    return np.ascontiguousarray(m.T, dtype='f4').tobytes()


def perspective(fovy, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy) * 0.5)
    m = np.zeros((4, 4), dtype=np.float64)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2.0 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def ortho(l, r, b, t, n, f):
    m = np.eye(4, dtype=np.float64)
    m[0, 0] = 2.0 / (r - l)
    m[1, 1] = 2.0 / (t - b)
    m[2, 2] = -2.0 / (f - n)
    m[0, 3] = -(r + l) / (r - l)
    m[1, 3] = -(t + b) / (t - b)
    m[2, 3] = -(f + n) / (f - n)
    return m


def look_at(eye, target, up=(0.0, 1.0, 0.0)):
    eye = np.asarray(eye, dtype=np.float64)
    f = np.asarray(target, dtype=np.float64) - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, np.asarray(up, dtype=np.float64))
    ns = np.linalg.norm(s)
    if ns < 1e-9:
        s = np.array([1.0, 0.0, 0.0])
    else:
        s /= ns
    u = np.cross(s, f)
    m = np.eye(4, dtype=np.float64)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[0, 3] = -s.dot(eye)
    m[1, 3] = -u.dot(eye)
    m[2, 3] = f.dot(eye)
    return m


def unproject(inv_vp, x, y, z):
    """NDC point back to world space through an already-inverted view-projection."""
    p = inv_vp @ np.array([x, y, z, 1.0])
    return p[:3] / p[3]


def screen_ray(inv_vp, pos, size):
    """World-space (origin, direction) ray through a screen pixel.

    `pos` is in pygame's window coordinates — origin top-left, y down.
    """
    x = 2.0 * pos[0] / max(size[0], 1) - 1.0
    y = 1.0 - 2.0 * pos[1] / max(size[1], 1)
    near = unproject(inv_vp, x, y, -1.0)
    far = unproject(inv_vp, x, y, 1.0)
    d = far - near
    n = np.linalg.norm(d)
    return near, (d / n if n > 1e-12 else d)


class Camera:
    """Yaw/pitch fly camera. Yaw 0 looks north (-z), +y is up."""

    def __init__(self, position, yaw=0.0, pitch=-25.0, fov=62.0):
        self.pos = np.array(position, dtype=np.float64)
        self.yaw = yaw
        self.pitch = pitch
        self.fov = fov
        self.near = 1.0
        self.far = 9000.0
        self.speed = 90.0            # metres / second
        self.sensitivity = 0.14

    @property
    def forward(self):
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        return np.array([sy * cp, sp, -cy * cp])

    @property
    def right(self):
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        return np.array([cy, 0.0, sy])

    def view(self):
        return look_at(self.pos, self.pos + self.forward)

    def projection(self, aspect):
        return perspective(self.fov, aspect, self.near, self.far)

    def look(self, dx, dy):
        self.yaw = (self.yaw + dx * self.sensitivity) % 360.0
        self.pitch = max(-89.5, min(89.5, self.pitch - dy * self.sensitivity))

    def update(self, dt, keys):
        move = np.zeros(3)
        fwd = self.forward
        flat = np.array([fwd[0], 0.0, fwd[2]])
        n = np.linalg.norm(flat)
        flat = flat / n if n > 1e-9 else self.right

        if keys[pygame.K_w] or keys[pygame.K_UP]:
            move += fwd
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            move -= fwd
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            move -= self.right
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            move += self.right
        if keys[pygame.K_e] or keys[pygame.K_SPACE]:
            move += (0.0, 1.0, 0.0)
        if keys[pygame.K_q] or keys[pygame.K_LCTRL]:
            move -= (0.0, 1.0, 0.0)

        norm = np.linalg.norm(move)
        if norm > 1e-9:
            boost = 4.0 if keys[pygame.K_LSHIFT] else 1.0
            slow = 0.25 if keys[pygame.K_LALT] else 1.0
            # move faster when high up, where the ground crawls past
            altitude = max(1.0, 0.4 + self.pos[1] / 90.0)
            self.pos += move / norm * self.speed * boost * slow * altitude * dt
        self.pos[1] = max(1.5, self.pos[1])


class OrbitCamera:
    """Turntable camera for the editor: orbit, pan and zoom around a target.

    Same `view()` / `projection()` interface as the fly Camera, so both go
    through the same `look_at` and `perspective` helpers. The eye position is
    the closed form of the editor's original translate/rotate/rotate/translate
    modelview, which is why yaw and pitch still turn the way they always did.
    """

    def __init__(self, target=(0.0, 0.5, 0.0), yaw=45.0, pitch=30.0,
                 distance=22.0, fov=60.0):
        self.target = np.array(target, dtype=np.float64)
        self.yaw = yaw
        self.pitch = pitch
        self.distance = distance
        self.fov = fov
        self.near = 0.1
        self.far = 500.0

    @property
    def pos(self):
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        return self.target + self.distance * np.array([-cp * sy, sp, cp * cy])

    def view(self):
        return look_at(self.pos, self.target)

    def projection(self, aspect):
        return perspective(self.fov, aspect, self.near, self.far)

    def _basis(self):
        """World-space right/up vectors of the camera, for panning."""
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        return np.array([cy, 0.0, sy]), np.array([sy * sp, cp, -cy * sp])

    def orbit(self, dx, dy):
        self.yaw += dx * 0.3
        self.pitch = max(-89.0, min(89.0, self.pitch + dy * 0.3))

    def pan(self, dx, dy):
        right, up = self._basis()
        f = self.distance * 0.0015
        self.target = self.target - right * dx * f + up * dy * f

    def zoom(self, notches):
        self.distance = max(2.0, min(120.0, self.distance * (0.9 ** notches)))
