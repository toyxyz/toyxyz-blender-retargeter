"""Root travel, floor clearance, and frame-local leg reach."""

import math

import numpy as np

from .pose import FORWARD, UP, Pose, bone_frame, reach_interval, rotation_fraction, smoothstep, swing, unit
from .reach_geometry import aim_direction, aim_midpoint, projected, rest_bend


def knee_plane(old_upper, old_lower, direction):
    old_direction = unit(old_upper + old_lower, -UP)
    source_bend = old_upper - old_direction * (old_upper @ old_direction)
    magnitude = np.linalg.norm(source_bend)
    hint = FORWARD - old_direction * (FORWARD @ old_direction)
    bend = swing(old_direction, direction) @ unit(source_bend if magnitude > .007 else hint, bone_frame(old_direction)[:, 2])
    return bend


def place_leg(pose, skeleton, side, ankle_goal, ankle_rotation):
    upper, lower, ankle, toe, tip = skeleton.legs[side]
    p, g = pose.positions, pose.rotations
    a, b = skeleton.lengths[side]
    old_upper, old_lower = p[lower] - p[upper], p[ankle] - p[lower]
    desired = ankle_goal - p[upper]
    direction = unit(desired, unit(old_upper + old_lower, -UP))
    low, high = reach_interval(a, b)
    distance = min(max(np.linalg.norm(desired), low), high)
    bend = knee_plane(old_upper, old_lower, direction)
    along = (a*a - b*b + distance*distance) / (2*distance)
    first = along * direction + math.sqrt(max(0., a*a - along*along)) * bend
    second = distance * direction - first
    toe_local = g[ankle].T @ g[toe]
    tip_local = g[toe].T @ g[tip]
    g[upper] = swing(old_upper, first) @ g[upper]
    g[lower] = swing(old_lower, second) @ g[lower]
    g[ankle] = ankle_rotation
    p[lower], p[ankle] = p[upper] + first, p[upper] + distance * direction
    p[toe] = p[ankle] + g[ankle] @ skeleton.offsets[toe]
    g[toe] = g[ankle] @ toe_local
    p[tip] = p[toe] + g[toe] @ skeleton.offsets[tip]
    g[tip] = g[toe] @ tip_local


def travel_span(skeleton):
    hips, ankles = np.array(skeleton.legs)[:, (0, 2)].T
    hip_width = np.linalg.norm(skeleton.rest[hips[0]]-skeleton.rest[hips[1]])
    neutral_leg_span = np.linalg.norm(skeleton.rest[ankles]-skeleton.rest[hips], axis=1).mean()
    return float(neutral_leg_span + hip_width)


def positive_lift(depth, width):
    if width > 0. and -width < depth < width:
        return (depth + width)**2 / (4. * width)
    return max(0., depth)


def soften_extension(origin, ankle, fk_ankle, lengths, strength):
    """Leave room for FK knee flexion as an ankle goal approaches full extension."""
    vector = ankle - origin
    distance = float(np.linalg.norm(vector))
    a, b = lengths
    fk_radius = float(np.linalg.norm(fk_ankle - origin))
    if distance <= fk_radius or distance <= 1e-9:
        return ankle
    fk_angle = math.acos(max(-1., min(1., (fk_radius**2 - a*a - b*b) / (2.*a*b))))
    limit = math.sqrt(a*a + b*b + 2.*a*b*math.cos(fk_angle*strength))
    width = max(0., limit - fk_radius) * strength
    if distance <= limit - width:
        return ankle
    radius = limit - width * math.exp(-(distance - limit + width) / width) if width > 1e-9 else limit
    # On upright legs, adjust height first so the projected foot path is retained.
    radial = vector * (radius / distance)
    horizontal = float(vector[0]**2 + vector[2]**2)
    if horizontal >= radius*radius:
        return origin + radial
    vertical = vector.copy()
    vertical[1] = math.copysign(math.sqrt(radius*radius - horizontal), vector[1])
    preference = smoothstep(abs(vertical[1]) / (.24 * (a+b)))
    return origin + radial + preference*(vertical - radial)


class GuidedLegReach:
    def __init__(self, source, target, *, gain=None):
        self.target = target
        self.source = source
        self.source_hips = [leg[0] for leg in source.legs]
        self.source_ankles = [leg[2] for leg in source.legs]
        self.target_ankles = [leg[2] for leg in target.legs]
        feet = {joint for leg in target.legs for joint in leg[2:]}
        self.foot_children = []
        for joint, parent in enumerate(target.parents):
            if joint not in feet and parent in feet:
                self.foot_children.append((joint, parent))
                feet.add(joint)
        self.gain = gain if gain is not None else travel_span(target) / travel_span(source)
        self.src_root = source.rest[0]
        self.root_offset = target.rest[0] - self.gain * self.src_root

        src_hips = source.rest[[leg[0] for leg in source.legs]]
        dst_hips = target.rest[[leg[0] for leg in target.legs]]
        src_center, dst_center = src_hips.mean(axis=0), dst_hips.mean(axis=0)
        # Carry a portion of the differing pelvis-to-socket offset through hip pitch and roll.
        self.hip_pivot = .65 * (self.gain * (src_center-self.src_root) - (dst_center-target.rest[0]))
        width_offset = (dst_hips-dst_center)[:, 0] - self.gain * (src_hips-src_center)[:, 0]
        ankle_height = self.gain * np.asarray(source.soles)[..., 1].mean() - np.asarray(target.soles)[..., 1].mean()
        self.foot_offset = np.column_stack((width_offset, np.full(2, ankle_height), np.zeros(2)))
        self.leg_scale = np.sum(target.lengths, axis=1) / np.sum(source.lengths, axis=1)
        self.leg_span_offset = (target.rest[self.target_ankles] - dst_hips
                                - self.leg_scale[:, None] * (source.rest[self.source_ankles] - src_hips))

    def targets(self, source, planar_travel=None, *, hip_offset=0.):
        root = self.gain * source.positions[0] + self.root_offset
        root += source.rotations[0] @ self.hip_pivot - self.hip_pivot
        feet = self.gain * source.positions[self.source_ankles]
        feet += (source.rotations[self.source_ankles] @ self.foot_offset[..., None])[..., 0]
        if planar_travel is not None:
            displacement = (planar_travel-self.gain) * (source.positions[0, ::2]-self.src_root[::2])
            root[::2] += displacement
            feet[:, ::2] += displacement
        root[1] += hip_offset
        return root, feet

    def floor_goal(self, side, ankle, rotation, *, stiffness, markers, softness):
        if markers is None:
            markers = self.target.soles[side]
        points = markers @ rotation.T
        heights = ankle[1] + points[:, 1] - self.target.floor
        depth = -heights.min()
        if depth <= -softness:
            return ankle, rotation
        if stiffness >= 1.:
            return ankle + positive_lift(depth, softness) * UP, rotation

        # Pivot near the lower support point and roll only when the other point is also near the floor.
        weight = smoothstep(depth/(.015 + 3.*softness)) * (1.-smoothstep((heights.max()-.020)/.055))
        weight *= 1.-stiffness
        corrected = rotation_fraction(swing(rotation @ UP, UP), weight) @ rotation
        heel_weight = smoothstep(.5 + (heights[1]-heights[0])/.016)
        support = heel_weight*markers[0] + (1.-heel_weight)*markers[1]
        ankle = ankle + (rotation-corrected) @ support
        clearance = ankle[1] + (markers @ corrected.T)[:, 1].min() - self.target.floor
        return ankle + positive_lift(-clearance, softness) * UP, corrected

    def ankle_bend_goal(self, source, side, origin, position, knee_bend):
        if knee_bend > 0.:
            source_ankle, source_hip = self.source_ankles[side], self.source_hips[side]
            relative = source.positions[source_ankle] - source.positions[source_hip]
            local_goal = origin + self.leg_scale[side]*relative + self.leg_span_offset[side]
            clearance = self.source.sole(source, side)[:, 1].min() - self.source.floor
            free = smoothstep((clearance - .015) / (.14 * sum(self.source.lengths[side])))
            position = position + knee_bend*free*(local_goal - position)
        return position

    def solve_ankle(self, source, pose, side, ankle, position, weight, rotation_weight, *,
                    floor_contact, foot_stiffness, markers, knee_extension, knee_bend):
        rotation = pose.rotations[ankle].copy()
        ankle_fk = pose.positions[ankle]
        hip, shin, *_ = self.target.legs[side]
        origin = pose.positions[hip]
        lengths = self.target.lengths[side]
        if weight > 0.:
            position = self.ankle_bend_goal(source, side, origin, position, knee_bend)
        if weight < 1.:
            position = ankle_fk + weight * (position - ankle_fk)
        if weight > 0. and knee_extension > 0.:
            position = soften_extension(origin, position, ankle_fk, lengths, knee_extension)
        if rotation_weight < 1. and np.linalg.norm(position - ankle_fk) > 1e-9:
            preview = Pose(pose.positions.copy(), pose.rotations.copy())
            place_leg(preview, self.target, side, position, rotation)
            inherited = preview.rotations[shin] @ pose.rotations[shin].T @ rotation
            rotation = rotation_fraction(rotation @ inherited.T, rotation_weight) @ inherited
        before_floor = rotation
        if floor_contact:
            position, rotation = self.floor_goal(side, position, rotation,
                                                 stiffness=foot_stiffness,
                                                 markers=markers,
                                                 softness=.020 * max(knee_extension, knee_bend) * sum(lengths))
        if weight == 0. and np.array_equal(rotation, before_floor) and np.linalg.norm(position - ankle_fk) <= 1e-9:
            return
        place_leg(pose, self.target, side, position, rotation)

    def solve(self, source, fk, *, floor_contact=False, planar_travel=None, hip_offset=0., foot_stiffness=0.,
              ankle_position_reach=(1., 1.), ankle_rotation_reach=(1., 1.), contact_soles=None,
              knee_extension=0., knee_bend=0.):
        root, goals = self.targets(source, planar_travel, hip_offset=hip_offset)
        pose = Pose(fk.positions + (root-fk.positions[0]), fk.rotations.copy())
        for side, ankle in enumerate(self.target_ankles):
            self.solve_ankle(
                source, pose, side, ankle, goals[side], ankle_position_reach[side],
                ankle_rotation_reach[side], floor_contact=floor_contact, foot_stiffness=foot_stiffness,
                markers=None if contact_soles is None else contact_soles[side],
                knee_extension=knee_extension, knee_bend=knee_bend)
        for joint, parent in self.foot_children:
            rotation = pose.rotations[parent] @ fk.rotations[parent].T
            pose.positions[joint] = pose.positions[parent] + rotation @ (fk.positions[joint]-fk.positions[parent])
            pose.rotations[joint] = rotation @ fk.rotations[joint]
        return pose


def aim_swing(before, target, hinge):
    after = unit(target, before)
    middle = aim_midpoint(before, after, np.cross(hinge, before))
    if middle is None:
        return swing(before, after)
    return swing(middle, after) @ swing(before, middle)


def hinge_rotation(axis, angle):
    x, y, z = axis
    cross = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    return np.eye(3) + math.sin(angle)*cross + (1.-math.cos(angle))*(cross @ cross)


class StagedLegReach(GuidedLegReach):
    def __init__(self, source, target, *, gain):
        super().__init__(source, target, gain=gain)
        self.hinges = []
        self.rest_thighs = []
        for leg in target.legs:
            hip, knee, ankle = leg[:3]
            line = unit(target.rest[ankle] - target.rest[hip], -UP)
            self.hinges.append(unit(np.cross(line, rest_bend(target, leg[:3], leg=True)), bone_frame(line)[:, 0]))
            self.rest_thighs.append(unit(target.rest[knee] - target.rest[hip], line))

    def bend_axis(self, pose, side, upper, lower):
        axis = unit(upper + lower, unit(upper, -UP))
        body = pose.rotations[0]
        body_hinge = body @ self.hinges[side]
        # Transport the calibrated axis using the thigh direction. Axial FK
        # twist has little leverage over a nearly collinear geometric plane.
        reference = aim_swing(body @ self.rest_thighs[side], upper, body_hinge) @ body_hinge
        area = np.cross(lower, upper)
        geometric = unit(area, reference)
        confidence = smoothstep((np.linalg.norm(area)/(np.linalg.norm(upper)*np.linalg.norm(lower))
                                 - math.sin(math.radians(2.))) /
                                (math.sin(math.radians(20.)) - math.sin(math.radians(2.))))
        return axis, aim_direction(reference, geometric, confidence, axis)

    def place(self, pose, side, goal, weight, extension, *, guard_fold=True):
        hip, knee, ankle, *_ = self.target.legs[side]
        origin = pose.positions[hip]
        upper = pose.positions[knee] - origin
        lower = pose.positions[ankle] - pose.positions[knee]
        fk_vector = upper + lower
        if weight <= 0. or np.linalg.norm(goal - pose.positions[ankle]) <= 1e-9:
            return
        axis, hinge = self.bend_axis(pose, side, upper, lower)
        lengths = self.target.lengths[side]
        if extension > 0.:
            goal = soften_extension(origin, goal, pose.positions[ankle], lengths, extension)
        goal_vector = goal - origin
        goal_radius = np.linalg.norm(goal_vector)
        if guard_fold:
            span = sum(lengths)
            fk_radius = np.linalg.norm(fk_vector)
            confidence = smoothstep((goal_radius/span - .045)/.20)
            folded = 1. - smoothstep((fk_radius/span - .10)/.18)
            direction = aim_direction(axis, unit(goal_vector, axis), confidence*(1. - .65*folded),
                                      np.cross(hinge, axis))
            goal_radius += folded*(1. - confidence)*(fk_radius - goal_radius)
            goal_vector = goal_radius*direction

        # The first pass steers the full chain toward the weighted ankle.
        aim = fk_vector + weight*(goal_vector - fk_vector)
        # Opposed endpoints can cancel their Cartesian blend near 50%.
        confidence = smoothstep(np.linalg.norm(aim)/(.08*sum(lengths)))
        aim = aim_direction(axis, unit(aim, axis), confidence, np.cross(hinge, axis))
        first = aim_swing(axis, aim, hinge)
        u, v, normal = first @ upper, first @ lower, first @ hinge

        # The knee changes the projected end distance in its hinge plane.
        p, q = projected(u, normal), projected(v, normal)
        a, b = np.linalg.norm(p), np.linalg.norm(q)
        current = np.linalg.norm(p+q)
        height = (u+v) @ normal
        planar_goal = math.sqrt(max(0., goal_radius*goal_radius - height*height))
        radius = np.clip(current + weight*(planar_goal-current), abs(a-b), a+b)
        existing = math.atan2(normal @ np.cross(p, q), p @ q)
        cosine = (radius*radius - a*a - b*b)/max(2.*a*b, 1e-14)
        flexion = existing if a*b < 1e-10 else -math.acos(float(np.clip(cosine, -1., 1.)))
        knee_turn = hinge_rotation(normal, flexion-existing)
        v = knee_turn @ v

        # The second aim compensates for the endpoint change caused by flexion.
        flexed = u + v
        second = aim_swing(unit(flexed, first @ axis), aim, normal)
        pose.positions[knee] = origin + second @ u
        pose.positions[ankle] = origin + second @ flexed
        pose.rotations[hip] = second @ first @ pose.rotations[hip]
        pose.rotations[knee] = second @ knee_turn @ first @ pose.rotations[knee]

    def solve_ankle(self, source, pose, side, ankle, position, weight, rotation_weight, *,
                    floor_contact, foot_stiffness, markers, knee_extension, knee_bend):
        if weight == 0.:
            super().solve_ankle(source, pose, side, ankle, position, weight, rotation_weight,
                                floor_contact=floor_contact, foot_stiffness=foot_stiffness, markers=markers,
                                knee_extension=knee_extension, knee_bend=knee_bend)
            return

        hip, knee, _, toe, tip = self.target.legs[side]
        p, g = pose.positions, pose.rotations
        original = g[ankle].copy()
        original_knee = g[knee].copy()
        toe_local, tip_local = original.T @ g[toe], g[toe].T @ g[tip]
        goal = self.ankle_bend_goal(source, side, p[hip], position, knee_bend)
        self.place(pose, side, goal, weight, knee_extension)
        inherited = g[knee] @ original_knee.T @ original
        rotation = original if rotation_weight == 1. else (
            rotation_fraction(original @ inherited.T, rotation_weight) @ inherited)
        if floor_contact:
            adjusted, rotation = self.floor_goal(
                side, p[ankle], rotation, stiffness=foot_stiffness,
                markers=markers, softness=.020*max(knee_extension, knee_bend)*sum(self.target.lengths[side]))
            self.place(pose, side, adjusted, 1., 0., guard_fold=False)
        g[ankle] = rotation
        p[toe] = p[ankle] + rotation @ self.target.offsets[toe]
        g[toe] = rotation @ toe_local
        p[tip] = p[toe] + g[toe] @ self.target.offsets[tip]
        g[tip] = g[toe] @ tip_local
