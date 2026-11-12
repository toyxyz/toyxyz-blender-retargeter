"""Reference bend directions and pole transport."""

import math

import numpy as np

from .pose import bone_frame, smoothstep, swing, unit


def projected(vector, axis):
    return vector - axis * (vector @ axis)


def blend_direction(first, second, weight):
    angle = math.acos(float(np.clip(first @ second, -1., 1.)))
    tangent = unit(projected(second, first), bone_frame(first)[:, 0])
    return first * math.cos(weight * angle) + tangent * math.sin(weight * angle)


def aim_midpoint(first, second, pole):
    dot = first @ second
    if dot > -.80:
        return None
    bend = unit(projected(pole, first), bone_frame(first)[:, 0])
    influence = .5 * smoothstep((-dot - .80) / .17)
    return unit(first + second + influence * bend, bend)


def aim_direction(first, second, weight, pole):
    midpoint = aim_midpoint(first, second, pole)
    if midpoint is None:
        return blend_direction(first, second, weight)
    if weight <= .5:
        return blend_direction(first, midpoint, 2.*weight)
    return blend_direction(midpoint, second, 2.*weight - 1.)


def rest_bend(skeleton, joints, *, leg=False):
    """Return a signed bend direction in calibrated rest coordinates."""
    names, rest = skeleton.names, skeleton.rest
    upper, elbow, wrist = rest[list(joints)]
    role = 'Leg' if leg else 'Arm'
    lateral = rest[names.index('Left' + role)] - rest[names.index('Right' + role)]
    lateral = unit(lateral, np.array([1., 0., 0.]))
    torso = rest[names.index('Head')] - rest[0]
    up = unit(projected(torso, lateral), np.array([0., 1., 0.]))
    facing = np.cross(lateral, up) if leg else np.cross(up, lateral)
    axis = unit(wrist - upper, lateral)
    fallback = unit(projected(facing, axis), unit(projected(-up, axis), bone_frame(axis)[:, 0]))
    observed = projected(elbow - upper, axis)
    confidence = smoothstep(np.linalg.norm(observed) / (.08 * np.linalg.norm(elbow - upper)))
    observed = unit(observed, fallback)
    return aim_direction(fallback, observed, confidence, up)


def transported_pole(axis, aim, pole):
    midpoint = aim_midpoint(axis, aim, pole)
    transform = swing(axis, aim) if midpoint is None else swing(midpoint, aim) @ swing(axis, midpoint)
    return unit(projected(transform @ pole, aim), bone_frame(aim)[:, 0])
