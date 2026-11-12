"""Write evaluated retarget poses to a native Blender Action."""

import math

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
import numpy as np

from . import arp_fk, floor_markers, reference, runtime
from .retarget import Retarget


OBJECT_CHANNELS = (
    'location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale',
    'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale',
)


def action_curves(action, slot):
    if action is None or slot is None:
        return
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type == 'KEYFRAME':
                bag = strip.channelbag(slot)
                if bag is not None:
                    yield from bag.fcurves


def active_nla(animation):
    return bool(animation and animation.use_nla and any(track.strips and not track.mute for track in animation.nla_tracks))


def source_keys(source):
    animation = source.animation_data
    if animation is None or active_nla(animation):
        return ()
    return sorted({float(point.co.x) for curve in action_curves(animation.action, animation.action_slot)
                   for point in curve.keyframe_points})


def source_range(scene, source):
    animation = source.animation_data
    if animation is not None and not active_nla(animation):
        ranges = [curve.range() for curve in action_curves(animation.action, animation.action_slot)
                  if curve.keyframe_points]
        if ranges:
            return math.floor(min(r[0] for r in ranges)), math.ceil(max(r[1] for r in ranges)), animation.action.name
    return scene.frame_start, scene.frame_end, None


def connected_for_bake(scene):
    if scene is None or reference.editor is not None or floor_markers.group_for(scene) is not None:
        return []
    return [active for active in runtime.sessions.values()
            if active.scene == scene and not active.failed and active.reference_editor is None]


def bake_order(sessions):
    """Bake downstream targets first so sampling still reads the live upstream pose."""
    upstream = {active.target.rig: active for active in sessions}

    def job(active):
        rig, chain = active.source.rig, []
        while rig in upstream:
            parent = upstream[rig]
            chain.append(parent)
            rig = parent.source.rig
        return active, rig, tuple(reversed(chain))

    return sorted(map(job, sessions), key=lambda item: len(item[2]), reverse=True)


def animated_object_channels(animation):
    if animation is None:
        return set()
    paths = {curve.data_path for curve in action_curves(animation.action, animation.action_slot)}
    if active_nla(animation):
        strips = [strip for track in animation.nla_tracks if not track.mute for strip in track.strips]
        while strips:
            strip = strips.pop()
            paths.update(curve.data_path for curve in action_curves(strip.action, strip.action_slot))
            strips.extend(strip.strips)
    return paths.intersection(OBJECT_CHANNELS)


def local_basis(bone):
    parent = bone.parent
    kwargs = {'parent_matrix': parent.matrix, 'parent_matrix_local': parent.bone.matrix_local} if parent else {}
    return bone.bone.convert_local_to_pose(bone.matrix, bone.bone.matrix_local, invert=True, **kwargs)


def rotation_channel(mode, quaternion, previous):
    if mode in {'QUATERNION', 'AXIS_ANGLE'}:
        if previous is not None:
            quaternion.make_compatible(previous)
        if mode == 'QUATERNION':
            return 'rotation_quaternion', tuple(quaternion), quaternion
        axis, angle = quaternion.to_axis_angle()
        return 'rotation_axis_angle', (angle, *axis), quaternion
    euler = quaternion.to_euler(mode) if previous is None else quaternion.to_euler(mode, previous)
    return 'rotation_euler', tuple(euler), euler


def write_action(name, target, samples, interpolation, handle, keep):
    action = bpy.data.actions.new(name)
    try:
        slot = action.slots.new(id_type='OBJECT', name=target.name)
        layer = action.layers.new('Retargeted Motion')
        bag = layer.strips.new(type='KEYFRAME').channelbag(slot, ensure=True)
        enums = bpy.types.Keyframe.bl_rna.properties
        interp_value = enums['interpolation'].enum_items[interpolation].value
        handle_value = enums['handle_left_type'].enum_items[handle].value
        for (path, group), frames in samples.items():
            values = [value for _, value in frames]
            for axis, components in enumerate(zip(*values)):
                curve = bag.fcurves.new(data_path=path, index=axis, group_name=group)
                points = curve.keyframe_points
                points.add(len(frames))
                points.foreach_set('co', [item for (frame, _), value in zip(frames, components) for item in (frame, value)])
                points.foreach_set('interpolation', [interp_value] * len(frames))
                if interpolation == 'BEZIER':
                    points.foreach_set('handle_left_type', [handle_value] * len(frames))
                    points.foreach_set('handle_right_type', [handle_value] * len(frames))
                curve.update()
        action.use_fake_user = keep
        return action, slot
    except Exception:
        bpy.data.actions.remove(action)
        raise


def set_frame(scene, frame):
    integer = math.floor(frame)
    scene.frame_set(integer, subframe=frame - integer)


def capture(context, active, target, frames, object_paths, check_bones, progress, upstream=()):
    scene = context.scene
    samples, expected, rotations = {}, {}, {}
    key_frames = set(frames)
    all_frames = sorted(key_frames.union(range(int(frames[0]), int(frames[-1]) + 1)))
    controller_fk = active.target.controller_fk
    if controller_fk:
        for name in arp_fk.SWITCHES:
            samples[(arp_fk.switch_path(target.pose.bones[name]), name)] = [(frames[0], (1.,))]
    chain = (*upstream, active)
    for index, frame in enumerate(all_frames):
        set_frame(scene, frame)
        context.view_layer.update()
        for connection in chain:
            if connection.failed:
                raise ValueError(connection.settings().status or 'The rig connection failed during baking.')
            connection.update(context.evaluated_depsgraph_get())
            context.view_layer.update()
        if frame in key_frames:
            evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
            expected[frame] = pose_matrices(evaluated, check_bones)
            bones = (evaluated.pose.bones[name] for name in active.target.mapped.values()) if controller_fk else evaluated.pose.bones
            for bone in bones:
                location, quaternion, scale = local_basis(bone).decompose()
                path, values, rotations[bone.name] = rotation_channel(
                    bone.rotation_mode, quaternion, rotations.get(bone.name))
                channels = [(path, values)]
                if not controller_fk or bone.name == active.target.mapped[0]:
                    channels.append(('location', tuple(location)))
                if not controller_fk:
                    channels.append(('scale', tuple(scale)))
                for channel, value in channels:
                    samples.setdefault((bone.path_from_id(channel), bone.name), []).append((frame, value))
            for channel in object_paths:
                samples.setdefault((channel, 'Object Transform'), []).append((frame, tuple(getattr(evaluated, channel))))
        progress(65. * (index + 1) / len(all_frames))
    return samples, expected


def pose_matrices(obj, names):
    return np.asarray([obj.matrix_world @ obj.pose.bones[name].matrix for name in names], dtype=float)


def bake_action(context, active, options, start, end, name, keys, progress, *, upstream=()):
    scene = context.scene
    target = active.target.rig
    if end < start:
        raise ValueError('End Frame must be at or after Start Frame.')
    animation = target.animation_data
    if animation is not None and animation.use_tweak_mode:
        raise ValueError('Exit NLA Tweak Mode on the target before baking.')
    if options.existing_keys:
        if not keys:
            raise ValueError('Existing Source Keys needs an active source Action without NLA layers.')
        frames = sorted({start, end, *(key for key in keys if start <= key <= end)})
    else:
        frames = sorted({end, *range(start, end + 1, options.step)})

    links = [constraint for _, constraint in active.links]
    owned = {constraint.as_pointer() for constraint in links}
    controller_fk = active.target.controller_fk
    controls = set(active.target.mapped.values())
    check_bones = [bone.name for bone in target.data.bones
                   if not controller_fk or bone.use_deform or bone.name in controls]
    constraints = [] if controller_fk else [constraint for bone in target.pose.bones for constraint in bone.constraints
                                            if constraint.as_pointer() not in owned and not constraint.mute]
    if constraints and not options.mute_constraints:
        raise ValueError('The target has active bone constraints. Enable Mute Target Bone Constraints to bake their evaluated pose.')
    original_frame = scene.frame_current + scene.frame_subframe
    created_animation = animation is None
    animation = target.animation_data_create()
    old_action, old_slot = animation.action, animation.action_slot
    old_nla, old_blend, old_influence = animation.use_nla, animation.action_blend_type, animation.action_influence
    old_solver = active.solver
    action = None
    changed = False
    committed = False
    fk_released = False
    try:
        active.solver = Retarget(active.source, active.target)
        active.previous = None
        samples, expected = capture(context, active, target, frames, animated_object_channels(animation), check_bones,
                                    progress, upstream)
        action, slot = write_action(name.strip() or f'{target.name} Retargeted',
                                    target, samples, options.interpolation, options.handles, options.keep_action)
        active.busy = True
        changed = True
        for constraint in (*links, *constraints):
            constraint.mute = True
        animation.action, animation.action_slot = action, slot
        if active_nla(animation):
            animation.use_nla = False
        animation.action_blend_type, animation.action_influence = 'REPLACE', 1.
        if controller_fk:
            arp_fk.release_fk(active.helper)
            fk_released = True
        for index, (frame, matrices) in enumerate(expected.items()):
            set_frame(scene, frame)
            context.view_layer.update()
            evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
            actual = pose_matrices(evaluated, check_bones)
            if not np.allclose(actual, matrices, atol=2e-4, rtol=2e-6):
                index_bone = int(np.argmax(np.max(np.abs(actual - matrices), axis=(1, 2))))
                name = check_bones[index_bone]
                raise ValueError(f'The baked pose differs on {name} at frame {frame:g}. Check target drivers and constraints.')
            progress(65. + 35. * (index + 1) / len(expected))
        if old_action is not None:
            old_action.use_fake_user = True
        runtime.stop(scene, active.key)
        committed = True
        return action, len(frames)
    finally:
        if not committed:
            if changed:
                animation.action, animation.action_slot = old_action, old_slot
                animation.use_nla = old_nla
                animation.action_blend_type, animation.action_influence = old_blend, old_influence
                for constraint in (*links, *constraints):
                    constraint.mute = False
                if fk_released:
                    arp_fk.force_fk(target, active.helper)
            if action is not None:
                bpy.data.actions.remove(action)
            if created_animation:
                target.animation_data_clear()
            active.solver = old_solver
            active.previous = None
            active.busy = False
        set_frame(scene, original_frame)
        context.view_layer.update()


class BakeOptions:
    step: IntProperty(name='Sample Step', description='Frames between samples. 1 keys every frame.', default=1, min=1)
    existing_keys: BoolProperty(name='Existing Source Keys', description='Key only at source Action keys and the range endpoints')
    interpolation: EnumProperty(name='Interpolation', description='How motion changes between baked keys', items=(
        ('LINEAR', 'Linear', 'Change at a constant rate between keys'),
        ('BEZIER', 'Bezier', 'Use editable curves between keys'),
        ('CONSTANT', 'Constant', 'Hold each key’s value until the next key'),
    ), default='LINEAR')
    handles: EnumProperty(name='Bezier Handles', description='How Blender sets the baked Bezier curve handles', items=(
        ('AUTO_CLAMPED', 'Auto Clamped', 'Smooth the curve without overshooting its keys'),
        ('AUTO', 'Auto', 'Smooth the curve with automatic handles'),
        ('VECTOR', 'Vector', 'Set handles to form straight segments'),
    ), default='AUTO_CLAMPED')
    keep_action: BoolProperty(name='Fake User', default=True,
                              description='Keep the new Action in the file when it is not assigned')
    mute_constraints: BoolProperty(name='Mute Target Bone Constraints',
                                    description='Keep existing target bone constraints muted so the baked pose can play directly')

    def draw_options(self, layout, has_keys, show_mute):
        row = layout.row()
        row.enabled = not self.existing_keys
        row.prop(self, 'step')
        row = layout.row()
        row.enabled = has_keys
        row.prop(self, 'existing_keys')
        layout.prop(self, 'interpolation')
        if self.interpolation == 'BEZIER':
            layout.prop(self, 'handles')
        layout.prop(self, 'keep_action')
        if show_mute:
            layout.prop(self, 'mute_constraints')


BAKE_ERRORS = (RuntimeError, ValueError, KeyError, np.linalg.LinAlgError)


class TRG_OT_bake_retarget(bpy.types.Operator, BakeOptions):
    """Bake the connected target's evaluated motion into a new Action"""

    bl_idname = 'toyxyz_retarget.bake'
    bl_label = 'Bake to Action'
    bl_options = {'REGISTER', 'UNDO'}

    action_name: StringProperty(name='Action Name', description='Name of the baked Action on the target rig')
    frame_start: IntProperty(name='Start Frame', description='First frame to bake', default=1)
    frame_end: IntProperty(name='End Frame', description='Last frame to bake, included in the range', default=250)

    @classmethod
    def poll(cls, context):
        active = runtime.session_for(context.scene)
        return (active is not None and not active.failed and active.scene == context.scene
                and active.reference_editor is None and floor_markers.group_for(context.scene) is None)

    def invoke(self, context, _event):
        active = runtime.session_for(context.scene)
        source, target = active.source.rig, active.target.rig
        self.frame_start, self.frame_end, label = source_range(context.scene, source)
        self._has_source_keys = label is not None
        self.action_name = f'{target.name} · {label or "Retargeted"}'
        return context.window_manager.invoke_props_dialog(self, width=410)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'action_name')
        row = layout.row(align=True)
        row.prop(self, 'frame_start')
        row.prop(self, 'frame_end')
        active = runtime.session_for(context.scene)
        self.draw_options(layout, getattr(self, '_has_source_keys', False),
                          active is not None and not active.target.controller_fk)
        layout.label(text='Activates the new Action and disconnects the rigs.', icon='INFO')

    def execute(self, context):
        active = runtime.session_for(context.scene)
        keys = source_keys(active.source.rig) if self.existing_keys else ()
        window = context.window_manager
        window.progress_begin(0., 100.)
        try:
            action, count = bake_action(context, active, self, self.frame_start, self.frame_end, self.action_name,
                                        keys, window.progress_update)
        except BAKE_ERRORS as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            window.progress_end()
        message = f'Baked {count} frames to {action.name}.'
        active.settings().status = message
        self.report({'INFO'}, message)
        return {'FINISHED'}


class TRG_OT_bake_all(bpy.types.Operator, BakeOptions):
    """Bake every connected actor to its own Action over its source motion range"""

    bl_idname = 'toyxyz_retarget.bake_all'
    bl_label = 'Bake All'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(connected_for_bake(context.scene))

    def invoke(self, context, _event):
        scene = context.scene
        sources = {source for _, source, _ in bake_order(connected_for_bake(scene))}
        self._has_source_keys = all(source_range(scene, source)[2] is not None for source in sources)
        if not self._has_source_keys:
            self.existing_keys = False
        return context.window_manager.invoke_props_dialog(self, width=410)

    def draw(self, context):
        sessions = connected_for_bake(context.scene)
        self.layout.label(text=f'Connected actors: {len(sessions)}')
        self.layout.label(text='Uses each source motion range, or the scene range.')
        self.draw_options(self.layout, getattr(self, '_has_source_keys', False),
                          any(not active.target.controller_fk for active in sessions))
        self.layout.label(text='Disconnects each actor after a successful bake.', icon='INFO')

    def execute(self, context):
        scene = context.scene
        jobs = bake_order(connected_for_bake(scene))
        window = context.window_manager
        window.progress_begin(0., 100.)
        completed, failures = 0, []
        blocked = set()
        ranges = {}
        try:
            for index, (active, source, upstream) in enumerate(jobs):
                target = active.target.rig
                try:
                    if target in blocked:
                        raise ValueError('A dependent actor could not be baked.')
                    if source not in ranges:
                        ranges[source] = (*source_range(scene, source), source_keys(source) if self.existing_keys else ())
                    start, end, label, keys = ranges[source]
                    name = f'{target.name} · {label or "Retargeted"}'
                    action, count = bake_action(context, active, self, start, end, name, keys,
                                                lambda value: window.progress_update((100.*index+value)/len(jobs)),
                                                upstream=upstream)
                except BAKE_ERRORS as exc:
                    active.settings().status = str(exc)
                    failures.append(f'{target.name}: {exc}')
                    blocked.add(active.source.rig)
                else:
                    active.settings().status = f'Baked {count} frames to {action.name}.'
                    completed += 1
                window.progress_update(100.*(index+1)/len(jobs))
        finally:
            window.progress_end()
        if failures:
            self.report({'WARNING'} if completed else {'ERROR'},
                        f'Baked {completed} of {len(jobs)} actors. {failures[0]}')
        else:
            self.report({'INFO'}, f'Baked {completed} actor(s) to separate Actions.')
        return {'FINISHED'} if completed else {'CANCELLED'}
