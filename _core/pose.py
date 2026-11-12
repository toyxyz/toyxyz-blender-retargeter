"""Calibrated skeletons and rotation math."""

from dataclasses import dataclass
import math

import numpy as np


UP = np.array([0., 1., 0.])
FORWARD = np.array([0., 0., 1.])
SIDES = ('Left', 'Right')


def unit(v, fallback):
    length = np.linalg.norm(v)
    return v / length if length > 1e-9 else fallback.copy()


def bone_frame(direction):
    y = unit(direction, UP)
    hint = FORWARD if abs(y @ FORWARD) < .9 else UP
    x = unit(np.cross(y, hint), np.array([1., 0., 0.]))
    return np.column_stack((x, y, np.cross(x, y)))


def swing(before, after):
    u = unit(before, UP)
    v = unit(after, UP)
    cosine = min(max(u @ v, -1.), 1.)
    if cosine < -1 + 1e-8:
        axis = bone_frame(u)[:, 0]
        return 2 * np.outer(axis, axis) - np.eye(3)
    ux, uy, uz = u
    vx, vy, vz = v
    x, y, z = uy*vz - uz*vy, uz*vx - ux*vz, ux*vy - uy*vx
    cross = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    return np.eye(3) + cross + cross @ cross / (1. + cosine)


def rotation_fraction(rotation, weight):
    angle = math.acos(min(max((np.trace(rotation)-1.)/2., -1.), 1.))
    if angle < 1e-6:
        return np.eye(3)
    if math.pi-angle < 1e-5:
        _, vectors = np.linalg.eigh((rotation+rotation.T)/2.)
        axis = vectors[:, -1]
    else:
        axis = np.array([rotation[2, 1]-rotation[1, 2], rotation[0, 2]-rotation[2, 0],
                         rotation[1, 0]-rotation[0, 1]]) / (2.*math.sin(angle))
    x, y, z = axis
    cross = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    theta = angle*weight
    return np.eye(3) + math.sin(theta)*cross + (1.-math.cos(theta))*(cross @ cross)


def smoothstep(value):
    value = min(max(value, 0.), 1.) if np.isscalar(value) else np.clip(value, 0., 1.)
    return value*value*(3.-2.*value)


@dataclass
class Pose:
    positions: np.ndarray
    rotations: np.ndarray


class Skeleton:
    def __init__(self, names, parents, rest, floor, heel_back):
        self.names = tuple(names)
        self.parents = tuple(parents)
        self.rest = np.asarray(rest, dtype=float)
        self.floor = floor
        self.offsets = self.rest.copy()
        self.offsets[1:] -= self.rest[np.asarray(self.parents[1:])]
        self.legs = [tuple(self.names.index(side + part) for part in ('Leg', 'Shin', 'Foot', 'ToeBase', 'ToeEnd'))
                     for side in SIDES]
        self.soles = []
        self.lengths = []
        for _, knee, ankle, toe, _ in self.legs:
            height = floor - self.rest[ankle, 1]
            ball = self.rest[toe] - self.rest[ankle]
            ball[1] = height
            self.soles.append(np.array([[0., height, -heel_back], ball]))
            self.lengths.append((np.linalg.norm(self.offsets[knee]), np.linalg.norm(self.offsets[ankle])))

    def sole(self, pose, side):
        ankle = self.legs[side][2]
        return pose.positions[ankle] + self.soles[side] @ pose.rotations[ankle].T


def reach_interval(a, b):
    low = math.sqrt(a*a + b*b + 2*a*b*math.cos(math.radians(155)))
    return low, a + b - 1e-6
