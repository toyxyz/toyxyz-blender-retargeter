"""FK-guided and rest-calibrated wrist placement."""

import numpy as np

from .pose import UP, bone_frame, smoothstep, swing, unit
from .reach_geometry import aim_direction, projected, transported_pole


def softened_goal(origin, goal, bounds):
    vector = goal - origin
    distance = np.linalg.norm(vector)
    low, high = bounds
    margin = .012 * high
    if low + margin <= distance <= high - margin:
        return goal

    def positive(value):
        if value <= -margin:
            return 0.
        if value >= margin:
            return value
        return (value + margin)**2 / (4. * margin)

    bounded = low + positive(distance - low) - positive(distance - high)
    return origin + bounded * unit(vector, -UP)


def reachable_distance(distance, fk_distance, first, second):
    low = abs(first - second) + 1e-7
    high = first + second - 1e-7
    # Preserve FK throughout the interior. Ease into the physical limit only
    # when the goal moves away from the FK distance toward that limit.
    width = .025 * high
    lower = min(width, max(0., fk_distance - low) * .9)
    upper = min(width, max(0., high - fk_distance) * .9)
    if lower > 1e-8 and distance < low + lower:
        fraction = np.clip((distance - low + lower) / (2 * lower), 0., 1.)
        return low + lower * fraction**2
    if upper > 1e-8 and distance > high - upper:
        fraction = np.clip((high + upper - distance) / (2 * upper), 0., 1.)
        return high - upper * fraction**2
    return float(np.clip(distance, low, high))


def place_fk_wrist(pose, limb, offset):
    shoulder, elbow, wrist = limb.joints
    origin = pose.positions[shoulder]
    before = pose.positions[wrist] - origin
    old_first = pose.positions[elbow] - origin
    old_second = pose.positions[wrist] - pose.positions[elbow]
    axis = unit(before, unit(old_first, -UP))
    bend = old_first - axis * (old_first @ axis)
    bend_height = np.linalg.norm(bend)
    first, second = limb.lengths
    influence = smoothstep(bend_height / (.12 * min(first, second)))
    offset = offset * influence
    if bend_height < 1e-9 or np.linalg.norm(offset) < 1e-8:
        return

    requested = before + offset
    direction = unit(requested, axis)
    distance = reachable_distance(np.linalg.norm(requested), np.linalg.norm(before), first, second)
    pole = swing(axis, direction) @ (bend / bend_height)
    along = (first**2 - second**2 + distance**2) / (2 * distance)
    new_first = along * direction + np.sqrt(max(0., first**2 - along**2)) * pole
    new_second = distance * direction - new_first
    endpoint = origin + distance * direction
    end_shift = endpoint - pose.positions[wrist]
    pose.positions[elbow] = origin + new_first
    pose.positions[wrist] = endpoint
    pose.rotations[shoulder] = swing(old_first, new_first) @ pose.rotations[shoulder]
    pose.rotations[elbow] = swing(old_second, new_second) @ pose.rotations[elbow]
    for child in limb.children:
        pose.positions[child] += end_shift


def place_calibrated_wrist(pose, limb, goal, weight, rest_pole):
    if weight <= 0.:
        return False
    shoulder, elbow, wrist = limb.joints
    origin = pose.positions[shoulder]
    first, second = limb.lengths
    fk_vector = pose.positions[wrist] - origin
    goal_vector = goal - origin
    if np.linalg.norm(goal_vector - fk_vector) <= 1e-8:
        return False
    fk_radius = np.linalg.norm(fk_vector)
    goal_radius = np.linalg.norm(goal_vector)
    axis = unit(fk_vector, unit(goal_vector, np.array([1., 0., 0.])))
    elbow_offset = projected(pose.positions[elbow] - origin, axis)
    reference = pose.rotations[shoulder] @ rest_pole
    reference = unit(projected(reference, axis), bone_frame(axis)[:, 0])
    geometric = unit(elbow_offset, reference)
    near_straight = 1. - smoothstep((np.linalg.norm(elbow_offset)/first - .05)/.11)
    bends_further = smoothstep((fk_radius - goal_radius)/(.04 * (first + second)))
    pole = aim_direction(geometric, reference, near_straight*bends_further, np.cross(axis, geometric))
    pole = unit(projected(pole, axis), geometric)

    vector = fk_vector + weight*(goal_vector - fk_vector)
    distance = np.linalg.norm(vector)
    radius = float(np.clip(distance, *limb.bounds))
    # Within the unreachable inner radius, avoid flipping as opposing
    # targets cancel. Reachable wrist positions use the Cartesian blend.
    aim = unit(vector, axis)
    if distance < limb.bounds[0]:
        aim = aim_direction(axis, aim, smoothstep(distance / limb.bounds[0]), pole)

    wrist_goal = origin + radius*aim
    desired = wrist_goal - origin
    direction = unit(desired, unit(fk_vector, -UP))
    distance = np.clip(np.linalg.norm(desired), *limb.bounds)
    along = (first*first - second*second + distance*distance) / (2. * distance)
    bend = transported_pole(axis, aim, pole)
    old_first = pose.positions[elbow] - origin
    old_second = fk_vector - old_first
    new_first = along*direction + np.sqrt(max(0., first*first - along*along))*bend
    new_second = distance*direction - new_first
    new_wrist = origin + new_first + new_second
    shift = new_wrist - pose.positions[wrist]
    pose.positions[elbow], pose.positions[wrist] = origin + new_first, new_wrist
    pose.rotations[shoulder] = swing(old_first, new_first) @ pose.rotations[shoulder]
    pose.rotations[elbow] = swing(old_second, new_second) @ pose.rotations[elbow]
    for child in limb.children:
        pose.positions[child] += shift
    return True
