"""Calibrate humanoid skeletons and transfer each frame through FK and limb reach."""

import json
from dataclasses import dataclass

from mathutils import Matrix, Vector
import numpy as np

from ._core.arm_reach import place_calibrated_wrist, place_fk_wrist, softened_goal
from ._core.leg_reach import GuidedLegReach, StagedLegReach
from ._core.pose import Pose, Skeleton, UP, reach_interval, rotation_fraction, smoothstep
from ._core.reach_geometry import rest_bend
from .mapping import SIDES, ancestry, digit_parent_roles, mapped_names


def reference_matrices(profile, *, use_saved=True):
    rig = profile.rig
    stored = (json.loads(profile.reference_pose)
              if use_saved and profile.reference_rig == rig and profile.reference_pose else {})
    return {bone.name: Matrix(stored[bone.name]) if bone.name in stored else bone.matrix_local.copy()
            for bone in rig.data.bones}


def save_reference(profile, rig, matrices):
    profile.reference_pose = json.dumps({name: [list(row) for row in value] for name, value in matrices.items()})
    profile.reference_rig = rig


def evaluated_matrices(rig, depsgraph):
    return {bone.name: bone.matrix.copy() for bone in rig.evaluated_get(depsgraph).pose.bones}


def rotation(matrix):
    return np.asarray(matrix.to_quaternion().to_matrix(), dtype=float)


class RigModel:
    def __init__(self, profile, depsgraph, unit, paired=(), *, reference=None, world=None, controller_fk=False):
        rig = self.rig = profile.rig
        self.controller_fk = controller_fk
        fixed, spine, neck = mapped_names(profile, controller_fk=controller_fk)
        reference = reference_matrices(profile) if reference is None else reference
        world = rig.evaluated_get(depsgraph).matrix_world if world is None else world
        self.reference_world = world = world.copy()
        scales = np.asarray(world.to_scale())
        if world.to_3x3().determinant() <= 0 or min(scales) < 1e-7 or not np.allclose(scales, scales[0], rtol=1e-3):
            raise ValueError(f'{rig.name} needs positive, uniform object scale.')
        self.unit = unit
        self.names = []
        self.parents = []
        self.mapped = {}
        self.digits = {}
        self.references = []
        positions = []

        def head(name):
            return world @ reference[name].translation

        def tail(name):
            return world @ reference[name] @ Vector((0., rig.data.bones[name].length, 0.))

        def add(role, parent, bone='', position=None):
            j = len(self.names)
            self.names.append(role)
            self.parents.append(parent)
            positions.append(head(bone) if bone else position)
            self.references.append(rotation(world @ reference[bone]) if bone else np.eye(3))
            if bone:
                self.mapped[j] = bone
            return j

        add('Hips', -1, fixed['Hips'])
        self.spine = []
        parent = 0
        for i, bone in enumerate(spine, 1):
            role = 'Chest' if i == len(spine) and i > 1 else f'Spine{i}'
            parent = add(role, parent, bone)
            self.spine.append(parent)
        self.neck = []
        for i, bone in enumerate(neck, 1):
            parent = add(f'Neck{i}', parent, bone)
            self.neck.append(parent)
        self.head = add('Head', parent, fixed['Head'])
        add('HeadEnd', self.head, position=tail(fixed['Head']))
        ends = []
        for side in SIDES:
            arm = fixed[side + 'Arm']
            shoulder_base = fixed[side + 'Shoulder'] or arm
            chest = self.spine[-1] if controller_fk else next(
                j for j, bone in reversed(list(zip(self.spine, spine)))
                if ancestry(rig.data.bones, bone, shoulder_base))
            shoulder = add(side + 'Shoulder', chest, fixed[side + 'Shoulder'], position=head(arm))
            parent = add(side + 'Arm', shoulder, arm)
            for role in ('ForeArm', 'Hand'):
                parent = add(side + role, parent, fixed[side + role])
            parent = 0
            for role in ('Leg', 'Shin', 'Foot'):
                parent = add(side + role, parent, fixed[side + role])
            foot_name, toe_name = fixed[side + 'Foot'], fixed[side + 'ToeBase']
            toe = add(side + 'ToeBase', parent, toe_name, position=tail(foot_name))
            end = tail(toe_name) if toe_name else tail(foot_name)
            add(side + 'ToeEnd', toe, position=end)
            ends.extend((positions[parent], positions[toe], end))

        used = set(self.mapped.values())
        for role, parent, name in digit_parent_roles(fixed, profile, paired, controller_fk=controller_fk):
            if name in used:
                raise ValueError(f'{name} is assigned to more than one role.')
            joint = add(role, self.names.index(parent), name)
            self.digits[role] = joint
            used.add(name)

        up = np.array([0., 0., 1.])
        left = np.array(head(fixed['LeftLeg']) - head(fixed['RightLeg']))
        left[2] = 0.
        if np.linalg.norm(left) < 1e-6:
            left = np.array(head(fixed['LeftArm']) - head(fixed['RightArm']))
            left[2] = 0.
        if np.linalg.norm(left) < 1e-6:
            raise ValueError(f'{rig.name} reference pose needs a clear left and right direction.')
        left /= np.linalg.norm(left)
        self.axes = np.column_stack((left, up, np.cross(left, up)))
        self.origin = np.asarray(positions[0]).copy()
        sole_z = min(point.z for point in ends)
        floor_z = sole_z if profile.floor_mode == 'REFERENCE' else 0.
        self.origin[2] = floor_z + profile.floor_offset / unit
        rest = (np.asarray(positions) - self.origin) @ self.axes * unit
        heel = .35 * np.mean([np.linalg.norm(rest[self.names.index(side + 'ToeBase'), ::2] -
                                             rest[self.names.index(side + 'Foot'), ::2]) for side in SIDES])
        # The neutral foot shape and the scene's contact plane can be at different heights.
        self.skeleton = Skeleton(self.names, self.parents, rest, (sole_z - self.origin[2]) * unit, max(.012, heel))
        self.skeleton.floor = 0.
        if np.min(self.skeleton.lengths) < 1e-5:
            raise ValueError(f'{rig.name} has a zero-length thigh or shin in its reference pose.')
        self.reference_rotations = np.asarray(self.references)
        self.reference_scales = {j: reference[bone].to_scale() for j, bone in self.mapped.items()}
        self.mapped_indices = np.fromiter(self.mapped, dtype=int)
        self.inherited_indices = tuple(j for j in range(len(self.parents)) if j not in self.mapped)
        ankles = [leg[2] for leg in self.skeleton.legs]
        self.ankle_names = tuple(self.mapped[j] for j in ankles)
        self.marker_basis = self.axes.T @ self.reference_rotations[ankles]

    def automatic_markers(self):
        return np.asarray(self.skeleton.soles) @ self.marker_basis

    def contact_soles(self, offsets):
        return np.asarray(offsets).reshape(2, 2, 3) @ self.marker_basis.transpose(0, 2, 1)

    def read(self, depsgraph, samples=None):
        obj = self.rig.evaluated_get(depsgraph)
        world = obj.matrix_world
        bones = obj.pose.bones
        channels = {} if samples is None else samples.setdefault(self.rig, {})
        heads, orientations = [], []
        for name in self.mapped.values():
            sample = channels.get(name)
            if sample is None:
                matrix = world @ bones[name].matrix
                sample = channels[name] = (tuple(matrix.translation), rotation(matrix))
            head, orientation = sample
            heads.append(head)
            orientations.append(orientation)
        indices = self.mapped_indices
        positions = np.empty_like(self.skeleton.rest)
        rotations = np.empty((len(self.names), 3, 3))
        positions[indices] = (np.asarray(heads) - self.origin) @ self.axes * self.unit
        current = np.asarray(orientations)
        rotations[indices] = self.axes.T @ current @ self.reference_rotations[indices].transpose(0, 2, 1) @ self.axes
        for j in self.inherited_indices:
            parent = self.parents[j]
            rotations[j] = rotations[parent]
            positions[j] = positions[parent] + rotations[parent] @ self.skeleton.offsets[j]
        return Pose(positions, rotations)

    def write(self, helper, pose, target_world, *, root_offset):
        inv = np.asarray(target_world.inverted(), dtype=float)
        inv_rotation = rotation(target_world).T
        positions = pose.positions @ self.axes.T / self.unit + self.origin
        # Apply scene placement after solving so the entire pose moves together.
        positions += np.asarray(root_offset) / self.unit
        local_positions = positions @ inv[:3, :3].T + inv[:3, 3]
        local_rotations = inv_rotation @ self.axes @ pose.rotations @ self.axes.T @ self.reference_rotations
        bones = helper.pose.bones
        for j in self.mapped:
            matrix = Matrix.LocRotScale(Vector(local_positions[j]), Matrix(local_rotations[j]).to_quaternion(),
                                        self.reference_scales[j])
            bones[self.names[j]].matrix_basis = matrix


def chain_knots(model, chain):
    points = model.skeleton.rest[chain]
    distances = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    return distances / distances[-1] if distances[-1] > 1e-8 else np.linspace(0., 1., len(chain))


def chain_map(source, target, source_chain, target_chain):
    if len(source_chain) == len(target_chain):
        return [(j, i, i, 0.) for j, i in zip(target_chain, source_chain)]
    if len(source_chain) == 1:
        return [(j, source_chain[0], source_chain[0], 0.) for j in target_chain]
    if len(target_chain) == 1:
        i = source_chain[-1]
        return [(target_chain[0], i, i, 0.)]
    old = chain_knots(source, source_chain)
    new = chain_knots(target, target_chain)
    result = [(target_chain[0], source_chain[0], source_chain[0], 0.)]
    for j, knot in zip(target_chain[1:-1], new[1:-1]):
        left = min(max(0, np.searchsorted(old, knot, side='right') - 1), len(old) - 2)
        right = left + 1
        width = old[right] - old[left]
        weight = (knot - old[left]) / width if width > 1e-8 else 0.
        # Rotation at the endpoint belongs to that joint beyond the last segment.
        result.append((j, source_chain[left], source_chain[left if right == len(old) - 1 else right], weight))
    result.append((target_chain[-1], source_chain[-1], source_chain[-1], 0.))
    return result


def reach_weights(settings, part):
    prefixes = (f'{part}_left', f'{part}_right') if getattr(settings, f'{part}_separate_reach') else (part,) * 2
    return tuple((getattr(settings, f'{prefix}_position_reach') / 100.,
                  getattr(settings, f'{prefix}_rotation_reach') / 100.) for prefix in prefixes)


@dataclass(frozen=True)
class WristChain:
    source_wrist: int
    joints: tuple
    children: tuple
    lengths: tuple
    bounds: tuple


class Retarget:
    def __init__(self, source, target):
        self.source, self.target = source, target
        self.guided = GuidedLegReach(source.skeleton, target.skeleton)
        self.staged = None
        self.wrist_rest_poles = None
        self.leg_ratio = float(np.mean(np.sum(target.skeleton.lengths, axis=1) /
                                       np.sum(source.skeleton.lengths, axis=1)))
        axial = set(target.spine + target.neck + [target.head])
        axial.update(target.digits.values())
        transfer = [(j, source.names.index(name), source.names.index(name), 0.)
                    for j, name in enumerate(target.names) if j not in axial]
        transfer += chain_map(source, target, source.spine, target.spine)
        transfer += chain_map(source, target, [*(source.neck or [source.spine[-1]]), source.head],
                              [*(target.neck or [target.spine[-1]]), target.head])[-(len(target.neck) + 1):]
        direct = [(j, left) for j, left, right, _ in transfer if left == right]
        self.copy_target, self.copy_source = np.asarray(direct, dtype=np.intp).T
        self.blend = [(j, left, right, weight) for j, left, right, weight in transfer if left != right]
        self.fk_parents = np.asarray(target.parents[1:])
        self.digits = [(j, target.parents[j], source.digits[role], source.parents[source.digits[role]])
                       for role, j in target.digits.items()]
        self.wrists = []
        for side in SIDES:
            joints = tuple(target.names.index(side + part) for part in ('Arm', 'ForeArm', 'Hand'))
            _, elbow, wrist = joints
            descendants = {wrist}
            for index in range(wrist + 1, len(target.parents)):
                if target.parents[index] in descendants:
                    descendants.add(index)
            lengths = tuple(np.linalg.norm(target.skeleton.offsets[index]) for index in (elbow, wrist))
            chain = WristChain(source.names.index(side + 'Hand'), joints, tuple(sorted(descendants - {wrist})),
                               lengths, reach_interval(*lengths)) if min(lengths) >= 1e-5 else None
            self.wrists.append(chain)

    def raw_pose(self, source_pose, settings, *, direct=False):
        rotations = np.empty((len(self.target.names), 3, 3))
        rotations[self.copy_target] = source_pose.rotations[self.copy_source]
        for j, left, right, weight in self.blend:
            rotations[j] = Matrix(source_pose.rotations[left]).to_quaternion().slerp(
                Matrix(source_pose.rotations[right]).to_quaternion(), weight).normalized().to_matrix()
        for joint, parent, source_joint, source_parent in self.digits:
            local = source_pose.rotations[source_parent].T @ source_pose.rotations[source_joint]
            rotations[joint] = rotations[parent] @ local
        if direct:
            root = self.fk_root(source_pose)
        else:
            target_root = self.target.skeleton.rest[0]
            stride = (self.leg_ratio if settings.travel == 'PROPORTIONAL' else 1.) * settings.stride_scale
            delta = source_pose.positions[0] - self.source.skeleton.rest[0]
            root = target_root + delta * [stride, self.leg_ratio, stride] + UP * settings.hip_offset
            if settings.travel == 'IN_PLACE':
                root[::2] = target_root[::2]
        return self.pose_from_rotations(root, rotations)

    def fk_root(self, source):
        return self.target.skeleton.rest[0] + (source.positions[0] - self.source.skeleton.rest[0])

    def pose_from_rotations(self, root, rotations):
        target = self.target.skeleton
        positions = np.empty_like(target.rest)
        positions[0] = root
        offsets = (rotations[self.fk_parents] @ target.offsets[1:, :, None])[..., 0]
        for j, parent in enumerate(self.fk_parents):
            positions[j+1] = positions[parent] + offsets[j]
        return Pose(positions, rotations)

    def blend_solver(self, fk_root, fk, solved, strength):
        # Interpolate each joint relative to its parent and rebuild the pose
        # with calibrated offsets. Blending joint positions can shorten bones.
        rotations = fk.rotations.copy()
        corrected = solved.rotations.copy()
        parents = self.fk_parents
        rotations[1:] = fk.rotations[parents].transpose(0, 2, 1) @ fk.rotations[1:]
        corrected[1:] = solved.rotations[parents].transpose(0, 2, 1) @ solved.rotations[1:]
        changed = np.flatnonzero(np.any(np.abs(rotations - corrected) > 1e-9, axis=(1, 2)))
        for joint in changed:
            start = Matrix(rotations[joint]).to_quaternion()
            end = Matrix(corrected[joint]).to_quaternion()
            rotations[joint] = start.slerp(end, strength).normalized().to_matrix()
        for joint, parent in enumerate(parents, 1):
            rotations[joint] = rotations[parent] @ rotations[joint]
        root = fk_root + strength * (solved.positions[0] - fk_root)
        return self.pose_from_rotations(root, rotations)

    def solve(self, source, settings, _frame, _fps, *, contact_soles=None):
        strength = getattr(settings, 'solver_strength', 100.) / 100.
        if strength <= 0.:
            return self.raw_pose(source, settings, direct=True)
        raw = self.raw_pose(source, settings)
        planar = 0. if settings.travel == 'IN_PLACE' else (
            self.guided.gain if settings.travel == 'PROPORTIONAL' else 1.) * settings.stride_scale
        ankle_t, ankle_r = zip(*reach_weights(settings, 'foot'))
        legs = self.guided
        if getattr(settings, 'staged_ankle_reach', False) and any(ankle_t):
            if self.staged is None:
                self.staged = StagedLegReach(self.source.skeleton, self.target.skeleton, gain=self.guided.gain)
            legs = self.staged
        pose = legs.solve(source, raw, floor_contact=settings.floor_contact,
                          planar_travel=planar, hip_offset=settings.hip_offset,
                          foot_stiffness=settings.foot_contact_stiffness / 100.,
                          ankle_position_reach=ankle_t, ankle_rotation_reach=ankle_r,
                          contact_soles=contact_soles,
                          knee_extension=getattr(settings, 'knee_extension_damping', 0.) / 100.,
                          knee_bend=getattr(settings, 'knee_bend_preservation', 0.) / 100.)
        hands = reach_weights(settings, 'hand')
        if any(position > 0. for position, _ in hands):
            self.reach_wrists(source, raw, pose, hands, planar,
                              calibrated=getattr(settings, 'calibrated_wrist_reach', False))
        if strength >= 1.:
            return pose
        return self.blend_solver(self.fk_root(source), raw, pose, strength)

    def reach_wrists(self, source, raw, pose, weights, planar, *, calibrated):
        if calibrated:
            source_skeleton, target_skeleton = self.source.skeleton, self.target.skeleton
            if self.wrist_rest_poles is None:
                self.wrist_rest_poles = {limb.joints: rest_bend(target_skeleton, limb.joints)
                                        for limb in self.wrists if limb is not None}
            gain = self.guided.gain
            horizontal = self.guided.root_offset[::2] + (planar - gain) * (
                source.positions[0, ::2] - self.guided.src_root[::2])
            floor_offset = target_skeleton.floor - gain * source_skeleton.floor
            wrist_offset = np.array([horizontal[0], floor_offset, horizontal[1]])
        else:
            source_anchor = self.source.spine[-1]
            target_anchor = self.target.spine[-1]
            alignment = pose.rotations[target_anchor] @ source.rotations[source_anchor].T
        for limb, (position, rotation) in zip(self.wrists, weights):
            if limb is None or position == 0.:
                continue
            shoulder, elbow, wrist = limb.joints
            source_wrist = source.positions[limb.source_wrist]
            if calibrated:
                goal = gain * source_wrist + wrist_offset
            else:
                goal = pose.positions[target_anchor] + alignment @ (source_wrist - source.positions[source_anchor])
            origin = pose.positions[shoulder]
            # A target at the shoulder has no reliable direction for the bend plane.
            position *= smoothstep(np.linalg.norm(goal - origin) / (.02 * sum(limb.lengths)))
            goal = softened_goal(origin, goal, limb.bounds)
            if calibrated:
                changed = place_calibrated_wrist(pose, limb, goal, position,
                                                 self.wrist_rest_poles[limb.joints])
                if not changed:
                    continue
            else:
                place_fk_wrist(pose, limb, (goal - pose.positions[wrist]) * position)
            if rotation == 1.:
                continue
            original = raw.rotations[wrist]
            inherited = pose.rotations[elbow] @ raw.rotations[elbow].T @ original
            result = inherited if rotation == 0. else rotation_fraction(original @ inherited.T, rotation) @ inherited
            delta = result @ original.T
            for joint in limb.children:
                pose.positions[joint] = pose.positions[wrist] + delta @ (pose.positions[joint] - pose.positions[wrist])
                pose.rotations[joint] = delta @ pose.rotations[joint]
            pose.rotations[wrist] = result
