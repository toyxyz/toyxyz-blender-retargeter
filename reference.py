"""Edit a calibration pose on a temporary armature with the same bone hierarchy."""

import bpy
from bpy.app.handlers import persistent

from . import actors, runtime
from .retarget import evaluated_matrices, reference_matrices, save_reference
from .runtime import object_mode, remove_object


TAG = '_toyxyz_retarget_reference_editor'
ORIGINAL_RIG = '_toyxyz_retarget_reference_rig'
ORIGINAL_VISIBILITY = '_toyxyz_retarget_reference_hidden'
editor = None
resume = None
history_editor = None


def connected_session(scene, key=None):
    active = runtime.session_for(scene, key)
    return active if active is not None and not active.failed and active.scene == scene else None


class Editor:
    def __init__(self, context, side, matrices=None, key=None):
        self.scene = context.scene
        self.side = side
        self.actor_key = actors.selected_key(self.scene) if key is None else key
        settings = actors.settings_for(self.scene, self.actor_key)
        if settings is None:
            raise ValueError('Choose an actor first.')
        self.profile = getattr(settings, side)
        self.rig = self.profile.rig
        if self.rig is None:
            raise ValueError('Choose an armature first.')
        if self.rig.name not in context.view_layer.objects:
            raise ValueError('The reference armature must be in the active view layer.')
        if context.mode not in {'OBJECT', 'POSE'}:
            raise ValueError('Open the reference editor in Object Mode or Pose Mode.')
        matrices = reference_matrices(self.profile) if matrices is None else matrices
        active = connected_session(self.scene, self.actor_key)
        self.selected = list(context.selected_objects)
        self.active = context.view_layer.objects.active
        self.was_in_pose = context.mode == 'POSE'
        self.view_layer = context.view_layer
        self.hidden = self.rig.hide_get(view_layer=self.view_layer)
        obj = self.obj = bpy.data.objects.new('Retarget Reference · ' + self.rig.name, self.rig.data.copy())
        self.scene.collection.objects.link(obj)
        obj[TAG] = True
        obj[ORIGINAL_RIG] = self.rig
        obj[ORIGINAL_VISIBILITY] = self.hidden
        obj.hide_render = True
        obj.show_in_front = True
        obj.data.pose_position = 'POSE'
        obj.data.display_type = 'STICK'
        world = self.rig.matrix_world.copy()
        if active is not None:
            model = getattr(active, side)
            other = active.target if side == 'source' else active.source
            center, other_center = world.translation.x, other.rig.matrix_world.translation.x
            direction = -1. if center < other_center or (center == other_center and side == 'source') else 1.
            span = model.skeleton.rest[:, 0]
            world.translation.x += direction * (.75 * float(span.max() - span.min()) + .25) / model.unit
        obj.matrix_world = world
        try:
            if self.was_in_pose:
                bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='OBJECT')
            self.view_layer.update()
            self.set_pose(matrices)
            for item in self.selected:
                item.select_set(False)
            if active is None and not any(self.rig in (item.source.rig, item.target.rig)
                                          for item in runtime.sessions.values()):
                self.rig.hide_set(True, view_layer=self.view_layer)
            self.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='POSE')
        except Exception:
            self.close()
            raise

    def read(self, context):
        return evaluated_matrices(self.obj, context.evaluated_depsgraph_get())

    def set_pose(self, matrices):
        for pose in self.obj.pose.bones:
            parent = pose.parent
            parent_args = {} if parent is None else {
                'parent_matrix': matrices[parent.name], 'parent_matrix_local': parent.bone.matrix_local,
            }
            pose.matrix_basis = pose.bone.convert_local_to_pose(
                matrices[pose.name], pose.bone.matrix_local, invert=True, **parent_args)
        self.obj.update_tag(refresh={'OBJECT'})

    def reset(self):
        if self.profile.rig != self.rig:
            raise ValueError('The reference armature changed.')
        self.set_pose(reference_matrices(self.profile))

    def attach(self):
        active = connected_session(self.scene, self.actor_key)
        if active is not None:
            active.reference_editor = self.side, self.obj
            active.reference_draft = None
            self.obj.update_tag(refresh={'OBJECT'})

    def close(self):
        if bpy.context.object == self.obj:
            object_mode()
        remove_object(self.obj)
        self.rig.hide_set(self.hidden, view_layer=self.view_layer)
        for item in self.selected:
            if item.name in self.view_layer.objects:
                item.select_set(True, view_layer=self.view_layer)
        self.view_layer.objects.active = self.active
        if self.was_in_pose and self.active:
            bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='POSE')


def begin(context, side):
    global editor
    if editor is not None:
        raise ValueError('Finish the active reference edit first.')
    editor = Editor(context, side)
    editor.attach()


def apply_reference(context, side, matrices=None, key=None):
    settings = actors.settings_for(context.scene, key)
    if settings is None:
        raise ValueError('Choose an actor first.')
    profile = getattr(settings, side)
    active = connected_session(context.scene, key)
    if active is not None:
        reference = reference_matrices(profile, use_saved=False) if matrices is None else matrices
        active.recalibrate(context.evaluated_depsgraph_get(), side, reference)
    if matrices is None:
        profile.reference_pose = ''
        profile.reference_rig = None
    else:
        save_reference(profile, profile.rig, matrices)
    if active is not None:
        profile.rig.update_tag(refresh={'OBJECT'})


def finish(context, commit=False):
    global editor
    active = editor
    if active is None:
        return
    connection = connected_session(active.scene, active.actor_key)
    if commit:
        if active.profile.rig != active.rig:
            raise ValueError('The reference armature changed.')
        apply_reference(context, active.side, active.read(context), active.actor_key)
    elif connection is not None:
        connection.recalibrate(context.evaluated_depsgraph_get(), active.side)
    if connection is not None:
        connection.reference_editor = connection.reference_draft = None
    editor = None
    active.close()


@persistent
def before_save(_):
    global resume
    if editor is not None:
        resume = (editor.scene, editor.side, editor.actor_key, editor.read(bpy.context))
        finish(bpy.context)


@persistent
def after_save(_):
    global resume, editor
    previous, resume = resume, None
    if previous is not None and previous[0] == bpy.context.scene:
        scene, side, key, matrices = previous
        try:
            editor = Editor(bpy.context, side, matrices, key)
            editor.attach()
        except (ValueError, RuntimeError) as exc:
            settings = actors.settings_for(scene, key)
            if settings is not None:
                settings.status = str(exc)


@persistent
def cleanup(*_):
    global history_editor
    history_editor = None
    finish(bpy.context)


@persistent
def before_history(_):
    global editor, history_editor
    if editor is not None:
        history_editor = (
            runtime.id_key(editor.scene), editor.side, editor.actor_key, runtime.id_key(editor.obj), editor.obj.name,
            tuple(obj.name for obj in editor.selected), editor.active.name if editor.active else '',
            editor.was_in_pose, editor.hidden,
        )
        editor = None


def restore_history():
    global editor, history_editor
    if runtime.history_session is not None:
        return .1
    previous, history_editor = history_editor, None
    context = bpy.context
    scene = context.scene
    if previous is not None and runtime.same_id(scene, previous[0]):
        _, side, key, obj_key, obj_name, selected, active, was_in_pose, hidden = previous
        obj = scene.objects.get(obj_name)
        settings = actors.settings_for(scene, key)
        profile = getattr(settings, side) if settings is not None else None
        if (runtime.same_id(obj, obj_key) and obj.get(TAG) and profile is not None
                and profile.rig and obj.get(ORIGINAL_RIG) == profile.rig):
            view = context.view_layer
            editor = Editor.__new__(Editor)
            editor.scene, editor.side, editor.actor_key, editor.profile = scene, side, key, profile
            editor.rig, editor.obj, editor.view_layer = profile.rig, obj, view
            editor.selected = [view.objects[name] for name in selected if name in view.objects]
            editor.active, editor.was_in_pose, editor.hidden = view.objects.get(active), was_in_pose, hidden
            editor.attach()
    for obj in list(bpy.data.objects):
        if not obj.get(TAG) or (editor is not None and obj == editor.obj):
            continue
        if context.object == obj:
            object_mode()
        rig = obj.get(ORIGINAL_RIG)
        if rig is not None and context.view_layer and rig.name in context.view_layer.objects:
            rig.hide_set(bool(obj.get(ORIGINAL_VISIBILITY)), view_layer=context.view_layer)
        remove_object(obj)
    return None


@persistent
def after_history(_):
    if not bpy.app.timers.is_registered(restore_history):
        bpy.app.timers.register(restore_history, first_interval=0.)


HANDLERS = {
    'save_pre': before_save, 'save_post': after_save, 'save_post_fail': after_save,
    'load_pre': cleanup, 'undo_pre': before_history, 'redo_pre': before_history,
    'undo_post': after_history, 'redo_post': after_history,
}


def register():
    for name, callback in HANDLERS.items():
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and callback not in handlers:
            handlers.append(callback)


def unregister():
    cleanup()
    if bpy.app.timers.is_registered(restore_history):
        bpy.app.timers.unregister(restore_history)
    for name, callback in HANDLERS.items():
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and callback in handlers:
            handlers.remove(callback)
