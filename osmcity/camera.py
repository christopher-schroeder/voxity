"""Matrix helpers and a free-flying camera."""

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
