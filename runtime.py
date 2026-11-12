"""Manage transient Blender pose links and update them from evaluated source bones."""

from contextlib import nullcontext

import bpy
from bpy.app.handlers import persistent
import numpy as np

from . import actors, arp_fk, floor_markers
from .retarget import Retarget, RigModel, evaluated_matrices, reach_weights
from .mapping import paired_digits


TAG = '_toyxyz_retarget_helper'
PAIR = '_toyxyz_retarget_pair'
DESTINATION = '_toyxyz_retarget_destination'
CONSTRAINT = 'Toyxyz Blender Retargeter · '
CONSTRAINT_PREFIXES = (CONSTRAINT, 'Toyxyz Live Retarget · ')
sessions = {}
save_mutes = []
history_session = None


def remove_object(obj):
    if obj is None:
        return
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data.users == 0:
        bpy.data.armatures.remove(data)


def object_mode():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def helper_rig(context, model):
    data = bpy.data.armatures.new('Live Retarget Pose Data')
    obj = bpy.data.objects.new('Live Retarget Pose', data)
    context.scene.collection.objects.link(obj)
    obj[TAG] = True
    obj.hide_render = True
    view = context.view_layer
    active = view.objects.active
    selected = list(context.selected_objects)
    pose_mode = context.mode == 'POSE'
    try:
        if pose_mode:
            bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='OBJECT')
        for item in selected:
            item.select_set(False)
        obj.select_set(True)
        view.objects.active = obj
        bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='EDIT')
        for j in model.mapped:
            bone = data.edit_bones.new(model.names[j])
            bone.head, bone.tail = (0., 0., 0.), (0., .1, 0.)
        bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='OBJECT')
        obj.select_set(False)
        obj.hide_set(True)
        obj.hide_select = True
        return obj
    except Exception:
        object_mode()
        remove_object(obj)
        raise
    finally:
        for item in selected:
            item.select_set(True)
        view.objects.active = active
        if pose_mode and active:
            bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='POSE')


class Session:
    def __init__(self, context, key, *, restore=False):
        self.scene = context.scene
        self.key = key
        settings = self.settings()
        src, dst = settings.source.rig, settings.target.rig
        if not src or not dst or src == dst:
            raise ValueError('Choose two different armatures.')
        if src.name not in self.scene.objects or dst.name not in self.scene.objects:
            raise ValueError('Both armatures must be in this scene.')
        if src.data.pose_position != 'POSE' or dst.data.pose_position != 'POSE':
            raise ValueError('Set both armatures to Pose Position.')
        if dst.library is not None and dst.override_library is None:
            raise ValueError('The target must be local or a library override.')
        graph = context.evaluated_depsgraph_get()
        unit = self.scene.unit_settings.scale_length
        controller_fk = arp_fk.layout(dst) is not None
        digits = paired_digits(settings.source, settings.target, controller_fk=controller_fk)
        self.source = RigModel(settings.source, graph, unit, digits)
        self.target = RigModel(settings.target, graph, unit, digits, controller_fk=controller_fk)
        self.solver = Retarget(self.source, self.target)
        self.previous = self.options = self.target_transform = self.frame = None
        self.reference_editor = None
        self.reference_draft = None
        self.busy = False
        self.failed = False
        pair = f'{src.session_uid}:{dst.session_uid}'
        specs = []
        for j, name in self.target.mapped.items():
            types = ('COPY_ROTATION', 'COPY_LOCATION') if j == 0 else ('COPY_ROTATION',)
            for kind in types if controller_fk else ('COPY_TRANSFORMS',):
                specs.append((name, self.target.names[j], kind))
        if restore and self.rebind_preview(dst, pair, specs):
            self.update(graph)
            return
        if restore:
            clear_helpers(target=dst)
        self.helper = helper_rig(context, self.target)
        self.helper[PAIR] = pair
        self.helper[DESTINATION] = dst
        self.links = []
        try:
            if controller_fk:
                arp_fk.force_fk(dst, self.helper)
            for name, role, kind in specs:
                constraint = dst.pose.bones[name].constraints.new(kind)
                self.links.append((name, constraint))
                constraint.name = CONSTRAINT + role
                constraint.target = self.helper
                constraint.subtarget = role
                constraint.target_space = constraint.owner_space = 'POSE'
                if kind != 'COPY_LOCATION':
                    constraint.mix_mode = 'REPLACE'
            self.update(context.evaluated_depsgraph_get())
        except Exception:
            self.close()
            raise

    def rebind_preview(self, dst, pair, specs):
        # Native history already restored these links. Replacing their target ID here
        # can invalidate dependencies still referenced by the adjacent history step.
        helper = next((obj for obj in self.scene.objects
                       if obj.type == 'ARMATURE' and obj.get(TAG) and obj.get(PAIR) == pair), None)
        if helper is None or any(role not in helper.data.bones for _, role, _ in specs):
            return False
        links = []
        for name, role, kind in specs:
            constraint = next((c for c in dst.pose.bones[name].constraints
                               if c.type == kind and c.name.startswith(CONSTRAINT_PREFIXES) and c.subtarget == role
                               and c.target == helper), None)
            if constraint is None:
                return False
            links.append((name, constraint))
        self.helper, self.links = helper, links
        return True

    def settings(self):
        settings = actors.settings_for(self.scene, self.key)
        if settings is None:
            raise ValueError('The retarget actor was removed.')
        return settings

    def update(self, graph, samples=None):
        if self.busy:
            return
        self.busy = True
        try:
            settings = self.settings()
            if settings.source.rig != self.source.rig or settings.target.rig != self.target.rig:
                raise ValueError('The rig pair changed. Connect the rigs again.')
            self.update_reference_draft(graph)
            source = self.source.read(graph, samples)
            target_world = self.target.rig.evaluated_get(graph).matrix_world.copy()
            frame = self.scene.frame_current_final
            fps = self.scene.render.fps / self.scene.render.fps_base
            draft = floor_markers.draft_offsets(self.scene, self.target)
            marker_key = draft if draft is not None else settings.target.contact_marker_offsets
            options = (settings.solver_strength, settings.travel, settings.stride_scale, settings.hip_offset, settings.floor_contact,
                       settings.foot_contact_stiffness, settings.knee_extension_damping,
                       settings.knee_bend_preservation, marker_key,
                       settings.calibrated_wrist_reach, settings.staged_ankle_reach,
                       reach_weights(settings, 'hand'), reach_weights(settings, 'foot'))
            if (self.previous is not None and frame == self.frame and options == self.options
                    and target_world == self.target_transform
                    and np.array_equal(source.positions, self.previous.positions)
                    and np.array_equal(source.rotations, self.previous.rotations)):
                return
            offsets = draft if draft is not None else floor_markers.saved_offsets(settings.target, self.target.ankle_names)
            if offsets is None:
                pose = self.solver.solve(source, settings, frame, fps)
            else:
                pose = self.solver.solve(source, settings, frame, fps, contact_soles=self.target.contact_soles(offsets))
            self.target.write(self.helper, pose, target_world)
            self.previous, self.frame, self.options, self.target_transform = source, frame, options, target_world
        finally:
            self.busy = False

    def recalibrate(self, graph, side, matrices=None):
        settings = self.settings()
        current = getattr(self, side)
        profile = getattr(settings, side)
        if profile.rig != current.rig:
            raise ValueError('The reference armature changed.')
        digits = paired_digits(settings.source, settings.target, controller_fk=self.target.controller_fk)
        model = RigModel(profile, graph, current.unit, digits, reference=matrices, world=current.reference_world,
                         controller_fk=current.controller_fk)
        source, target = (model, self.target) if side == 'source' else (self.source, model)
        solver = Retarget(source, target)
        self.source, self.target, self.solver = source, target, solver
        self.previous = None

    def update_reference_draft(self, graph):
        if self.reference_editor is None:
            return
        side, obj = self.reference_editor
        matrices = evaluated_matrices(obj, graph)
        if matrices == self.reference_draft:
            return
        self.reference_draft = matrices
        settings = self.settings()
        try:
            self.recalibrate(graph, side, matrices)
        except (ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
            settings.status = str(exc)
        else:
            settings.status = ''

    def close(self):
        for name, constraint in self.links:
            try:
                bone = self.target.rig.pose.bones.get(name)
                if bone is not None:
                    bone.constraints.remove(constraint)
            except (ReferenceError, RuntimeError):
                pass
        try:
            arp_fk.release_fk(self.helper)
            remove_object(self.helper)
        except ReferenceError:
            pass


def session_for(scene, key=None):
    if scene is None:
        return None
    key = actors.selected_key(scene) if key is None else key
    return sessions.get((scene.session_uid, key))


def __getattr__(name):
    if name == 'session':
        return session_for(bpy.context.scene)
    raise AttributeError(name)


def start(context, *, restore=False, key=None):
    scene = context.scene
    key = actors.selected_key(scene) if key is None else key
    settings = actors.settings_for(scene, key)
    if settings is None:
        raise ValueError('Choose a retarget actor first.')
    if session_for(scene, key) is not None:
        raise ValueError('Disconnect this actor first.')
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Connect the rigs in Object Mode or Pose Mode.')
    target = settings.target.rig
    if actors.duplicate_target(scene, key, target) or any(active.target.rig == target for active in sessions.values()):
        raise ValueError('This target is already assigned to another actor.')
    upstream = {active.target.rig: active.source.rig for active in sessions.values()}
    rig = settings.source.rig
    while rig in upstream:
        rig = upstream[rig]
        if rig == target:
            raise ValueError('These actor connections would form a cycle.')
    active = Session(context, key, restore=restore)
    sessions[scene.session_uid, key] = active
    context.view_layer.update()
    return active


def is_active(scene=None, key=None):
    scene = bpy.context.scene if scene is None else scene
    if scene is None:
        return False
    key = actors.selected_key(scene) if key is None else key
    return session_for(scene, key) is not None or any(
        same_id(scene, item[0]) and key == item[1] for item in history_session or ())


def stop(scene=None, key=None):
    global history_session
    scene = bpy.context.scene if scene is None else scene
    if scene is None:
        return
    key = actors.selected_key(scene) if key is None else key
    previous = sessions.pop((scene.session_uid, key), None)
    pending = [item for item in history_session or () if same_id(scene, item[0]) and key == item[1]]
    history_session = [item for item in history_session or () if item not in pending] or None
    if previous is not None:
        previous.close()
    elif pending:
        settings = actors.settings_for(scene, key)
        if settings is not None and settings.target.rig is not None:
            clear_helpers(target=settings.target.rig)


def stop_all():
    global history_session
    current = tuple(sessions.values())
    sessions.clear()
    for active in current:
        active.close()
    if history_session is not None:
        history_session = None
        clear_helpers()


@persistent
def update(scene, depsgraph):
    if save_mutes or not sessions or depsgraph.mode != 'VIEWPORT':
        return
    if scene.as_pointer() in bpy.app.driver_namespace.get('toyxyz_motion_ai.sampling_scenes', {}):
        return
    # Only share world-space source samples within this evaluated graph pass.
    samples = {}
    for active in tuple(sessions.values()):
        if scene != active.scene or active.failed:
            continue
        try:
            active.update(depsgraph, samples)
        except (KeyError, ReferenceError, RuntimeError, ValueError, np.linalg.LinAlgError) as exc:
            active.failed = True
            settings = actors.settings_for(scene, active.key)
            if settings is not None:
                settings.status = str(exc)
            if not bpy.app.timers.is_registered(stop_failed):
                bpy.app.timers.register(stop_failed, first_interval=0.)


def stop_failed():
    for active in tuple(sessions.values()):
        if active.failed:
            stop(active.scene, active.key)


@persistent
def before_save(_):
    # Save an inert preview without replacing the IDs still referenced by Undo/Redo.
    for scene in bpy.data.scenes:
        if scene.library is None:
            for key, settings in actors.entries(scene):
                active = session_for(scene, key)
                pending = any(same_id(scene, item[0]) and key == item[1]
                              and same_id(settings.source.rig, item[2]) and same_id(settings.target.rig, item[3])
                              for item in history_session or ())
                reconnect = pending or (active is not None and not active.failed)
                if settings.reconnect_on_load != reconnect:
                    settings.reconnect_on_load = reconnect
    for obj in bpy.data.objects:
        if obj.type != 'ARMATURE' or obj.get(TAG):
            continue
        links = [constraint for _, constraint in preview_constraints(obj)]
        links.extend(arp_fk.preview_drivers(obj))
        for link in links:
            save_mutes.append((link, link.mute))
            if not link.mute:
                link.mute = True


def resume_connection(context, key, settings, *, restore=False):
    try:
        start(context, key=key, restore=restore)
        settings.status = ''
    except (KeyError, ReferenceError, RuntimeError, ValueError, np.linalg.LinAlgError) as exc:
        settings.status = str(exc)


@persistent
def after_save(_):
    for link, muted in save_mutes:
        if link.mute != muted:
            link.mute = muted
    save_mutes.clear()


@persistent
def cleanup(*_):
    stop_all()


def preview_constraints(obj):
    for bone in obj.pose.bones:
        for constraint in bone.constraints:
            if (constraint.type in {'COPY_TRANSFORMS', 'COPY_ROTATION', 'COPY_LOCATION'}
                    and constraint.name.startswith(CONSTRAINT_PREFIXES)):
                yield bone, constraint


def clear_helpers(*, target=None, keep=()):
    discard = set()
    objects = list(bpy.data.objects)
    rigs = objects if target is None else (target,)
    for obj in rigs:
        if obj.type != 'ARMATURE' or obj.get(TAG):
            continue
        for bone, constraint in list(preview_constraints(obj)):
            if constraint.target in keep:
                continue
            if constraint.target is not None and constraint.target.get(TAG):
                discard.add(constraint.target)
            bone.constraints.remove(constraint)
    for obj in objects:
        if (obj.type == 'ARMATURE' and obj.get(TAG) and obj not in keep
                and (target is None or obj in discard or obj.get(DESTINATION) == target)):
            arp_fk.release_fk(obj)
            remove_object(obj)


@persistent
def after_load(_):
    clear_helpers()
    if bpy.app.background:
        restore_loaded_connection()
    elif not bpy.app.timers.is_registered(restore_loaded_connection):
        bpy.app.timers.register(restore_loaded_connection, first_interval=0.)


def restore_loaded_connection():
    context = bpy.context
    for scene in bpy.data.scenes:
        if scene.library is not None:
            continue
        pending = [(key, settings) for key, settings in actors.entries(scene)
                   if settings.reconnect_on_load and session_for(scene, key) is None]
        if pending:
            with scene_context(context, scene):
                for key, settings in pending:
                    resume_connection(context, key, settings)


def scene_context(context, scene):
    return nullcontext() if context.scene == scene else context.temp_override(scene=scene, view_layer=scene.view_layers[0])


def id_key(block):
    return block.session_uid, block.name_full


def same_id(block, key):
    return block is not None and (block.session_uid == key[0] or block.name_full == key[1])


@persistent
def before_history(_):
    global history_session
    if sessions:
        history_session = [(id_key(active.scene), active.key, id_key(active.source.rig), id_key(active.target.rig))
                           for active in sessions.values() if not active.failed] or None
        # Undo can replace these RNA objects. The next update uses the restored data.
        sessions.clear()


@persistent
def after_history(_):
    if bpy.app.timers.is_registered(restore_history):
        bpy.app.timers.unregister(restore_history)
    # Restore the preview once after a burst of Undo/Redo events.
    bpy.app.timers.register(restore_history, first_interval=.2)


def restore_history():
    global history_session
    context = bpy.context
    if context.scene is None or context.mode not in {'OBJECT', 'POSE'}:
        return .1
    previous, history_session = history_session or (), None
    for scene_key, key, source, target in previous:
        scene = next((item for item in bpy.data.scenes if same_id(item, scene_key)), None)
        settings = actors.settings_for(scene, key) if scene is not None else None
        if (settings is not None and same_id(settings.source.rig, source) and same_id(settings.target.rig, target)
                and session_for(scene, key) is None):
            with scene_context(context, scene):
                resume_connection(context, key, settings, restore=True)
    clear_helpers(keep={active.helper for active in sessions.values()})
    return None


def request_refresh(_settings, context):
    scene = _settings.id_data if _settings is not None else context.scene
    sources = {active.source.rig for active in sessions.values() if scene == active.scene and not active.failed}
    for source in sources:
        source.update_tag(refresh={'OBJECT'})


HANDLERS = {
    'depsgraph_update_post': update, 'frame_change_post': update,
    'save_pre': before_save, 'save_post': after_save, 'save_post_fail': after_save,
    'load_pre': cleanup, 'load_post': after_load,
    'undo_pre': before_history, 'redo_pre': before_history,
    'undo_post': after_history, 'redo_post': after_history,
}


def register():
    for name, callback in HANDLERS.items():
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and callback not in handlers:
            handlers.append(callback)


def unregister():
    stop_all()
    for callback in (stop_failed, restore_history, restore_loaded_connection):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    for name, callback in HANDLERS.items():
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and callback in handlers:
            handlers.remove(callback)
