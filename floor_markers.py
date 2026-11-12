"""Edit target sole offsets in the viewport while the retarget solver is connected."""

import json

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty
from mathutils import Matrix
import numpy as np

from . import actors

TAG = '_toyxyz_contact_markers'
RIG = '_toyxyz_contact_rig'
ACTOR = '_toyxyz_contact_actor'
BONES = '_toyxyz_contact_bones'
SLOT = '_toyxyz_contact_slot'
START_SELECTION = '_toyxyz_contact_selection'
START_ACTIVE = '_toyxyz_contact_active'
START_POSE_MODE = '_toyxyz_contact_pose_mode'
LABELS = ('Left Heel', 'Left Ball', 'Right Heel', 'Right Ball')
paused = []


def saved_offsets(profile, ankle_names):
    if profile.contact_marker_rig != profile.rig or not profile.contact_marker_offsets:
        return None
    try:
        data = json.loads(profile.contact_marker_offsets)
        offsets = np.asarray(data['offsets_m'], dtype=float)
        if tuple(data['ankles']) == ankle_names and offsets.shape == (4, 3) and np.isfinite(offsets).all():
            return offsets
    except (ValueError, TypeError, KeyError):
        pass
    return None


def group_for(scene):
    group = scene.toyxyz_retarget.contact_marker_editor
    return group if group is not None and group.get(TAG) and scene.collection.children.get(group.name) == group else None


def handles(group):
    indexed = {obj.get(SLOT): obj for obj in group.objects if obj.type == 'EMPTY' and SLOT in obj}
    if indexed.keys() == set(range(len(LABELS))):
        return tuple(indexed[i] for i in range(len(LABELS)))
    return ()


def matches(group, model):
    return group is not None and group.get(RIG) == model.rig and group.get(BONES) == json.dumps(model.ankle_names)


def draft_offsets(scene, model):
    group = group_for(scene)
    if not matches(group, model):
        return None
    objects = handles(group)
    if not objects:
        return None
    # Read the editable offsets before constraints. Reading the posed handles
    # here would feed the solved ankle transform back into its own floor solve.
    offsets = tuple(tuple(value * model.unit for value in obj.location) for obj in objects)
    return offsets if np.isfinite(offsets).all() else None


def model_for(context, key=None):
    from . import arp_fk, runtime
    from .retarget import RigModel

    active = runtime.session_for(context.scene, key)
    if active is not None and not active.failed and active.scene == context.scene:
        return active.target
    settings = actors.settings_for(context.scene, key)
    if settings is None or settings.target.rig is None:
        raise ValueError('Choose a target armature first.')
    profile = settings.target
    return RigModel(profile, context.evaluated_depsgraph_get(), context.scene.unit_settings.scale_length,
                    controller_fk=arp_fk.layout(profile.rig) is not None)


def refresh(context):
    from . import runtime

    runtime.request_refresh(None, context)


def set_locations(objects, model, offsets):
    for obj, location in zip(objects, np.asarray(offsets).reshape(4, 3)):
        obj.location = location / model.unit
        obj.update_tag(refresh={'OBJECT'})


def create(context):
    from . import reference

    scene = context.scene
    if reference.editor is not None:
        raise ValueError('Finish editing the reference pose first.')
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Open the marker editor in Object Mode or Pose Mode.')
    if group_for(scene) is not None:
        raise ValueError('Finish editing the current contact markers first.')
    key = actors.selected_key(scene)
    model = model_for(context, key)
    if context.view_layer.objects.get(model.rig.name) != model.rig:
        raise ValueError('The target armature must be in the active view layer.')
    group = bpy.data.collections.new('Retarget Contact Markers')
    group[TAG], group[RIG], group[BONES] = True, model.rig, json.dumps(model.ankle_names)
    group[ACTOR] = key
    group[START_SELECTION] = json.dumps([obj.name for obj in context.selected_objects])
    active = context.view_layer.objects.active
    group[START_ACTIVE] = active.name if active else ''
    group[START_POSE_MODE] = context.mode == 'POSE'
    scene.collection.children.link(group)
    scene.toyxyz_retarget.contact_marker_editor = group
    try:
        if context.mode == 'POSE':
            bpy.ops.object.mode_set(mode='OBJECT')
        for i, label in enumerate(LABELS):
            obj = bpy.data.objects.new(label, None)
            group.objects.link(obj)
            obj[SLOT] = i
            obj.empty_display_type = 'SPHERE' if i % 2 == 0 else 'CUBE'
            obj.empty_display_size = float(np.clip(.06 * np.mean(model.skeleton.lengths[i // 2]), .012, .04)) / model.unit
            obj.show_in_front = obj.show_name = obj.hide_render = True
            obj.lock_rotation = obj.lock_scale = (True,) * 3
            follow = obj.constraints.new('CHILD_OF')
            follow.name = 'Follow Target Ankle'
            follow.target, follow.subtarget = model.rig, model.ankle_names[i // 2]
            follow.use_scale_x = follow.use_scale_y = follow.use_scale_z = False
            follow.inverse_matrix = Matrix.Identity(4)
            follow.set_inverse_pending = False
        objects = handles(group)
        saved = saved_offsets(actors.settings_for(scene, key).target, model.ankle_names)
        set_locations(objects, model, model.automatic_markers() if saved is None else saved)
        context.view_layer.update()
        for obj in context.selected_objects:
            obj.select_set(False)
        context.view_layer.objects.active = objects[0]
        objects[0].select_set(True)
        refresh(context)
    except Exception:
        close(context, group)
        raise


def discard_group(group):
    for obj in list(group.objects):
        if SLOT in obj and obj.type == 'EMPTY':
            bpy.data.objects.remove(obj, do_unlink=True)
    if group.objects or group.children:
        del group[TAG]
    else:
        bpy.data.collections.remove(group)


def close(context, group):
    context.scene.toyxyz_retarget.contact_marker_editor = None
    selected = json.loads(group.get(START_SELECTION, '[]'))
    active = group.get(START_ACTIVE, '')
    was_in_pose = bool(group.get(START_POSE_MODE))
    discard_group(group)
    view = context.view_layer
    for obj in context.selected_objects:
        obj.select_set(False)
    for name in selected:
        obj = view.objects.get(name)
        if obj is not None:
            obj.select_set(True)
    view.objects.active = view.objects.get(active)
    if was_in_pose and view.objects.active and context.mode == 'OBJECT':
        bpy.ops.object.mode_set(mode='POSE')
    refresh(context)


def save(context, group, model):
    scene = context.scene
    offsets = draft_offsets(scene, model)
    if offsets is None:
        raise ValueError('Contact markers are missing or the target changed. Cancel and reopen the editor.')
    profile = actors.settings_for(scene, group.get(ACTOR)).target
    automatic = model.automatic_markers().reshape(4, 3)
    if np.allclose(offsets, automatic, atol=1e-6, rtol=0):
        profile.contact_marker_offsets = ''
        profile.contact_marker_rig = None
    else:
        profile.contact_marker_offsets = json.dumps({'ankles': model.ankle_names, 'offsets_m': offsets})
        profile.contact_marker_rig = model.rig
    close(context, group)


class TRG_OT_contact_markers(bpy.types.Operator):
    bl_idname = 'toyxyz_retarget.contact_markers'
    bl_label = 'Edit Contact Markers'
    bl_options = {'UNDO'}
    ACTIONS = (
        ('EDIT', 'Edit Contact Markers', 'Move the target heel and ball markers in the viewport'),
        ('SAVE', 'Save', 'Keep the edited target contact marker positions'),
        ('RESET', 'Reset', 'Restore automatic markers and keep editing'),
        ('CANCEL', 'Cancel', 'Discard the draft and restore the last saved markers'),
    )
    action: EnumProperty(items=ACTIONS)

    @classmethod
    def description(cls, context, properties):
        return next(description for action, _, description in cls.ACTIONS if action == properties.action)

    def execute(self, context):
        group = group_for(context.scene)
        try:
            if self.action == 'EDIT':
                create(context)
            elif group is None:
                return {'CANCELLED'}
            elif self.action == 'CANCEL':
                close(context, group)
            else:
                model = model_for(context, group.get(ACTOR))
                if self.action == 'SAVE':
                    save(context, group, model)
                else:
                    objects = handles(group)
                    if not objects or not matches(group, model):
                        raise ValueError('Contact markers are missing or the target changed. Cancel and reopen the editor.')
                    set_locations(objects, model, model.automatic_markers())
                    refresh(context)
        except (RuntimeError, ValueError, KeyError, np.linalg.LinAlgError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


def button(layout, action, text):
    layout.operator(TRG_OT_contact_markers.bl_idname, text=text).action = action


@persistent
def before_save(_):
    context = bpy.context
    for scene in bpy.data.scenes:
        group = group_for(scene)
        if group is None:
            continue
        active = context.view_layer.objects.active if context.scene == scene else None
        objects = handles(group)
        selected = tuple(obj.name for obj in objects if context.scene == scene and obj.select_get())
        paused.append((scene, group, active.name if active in objects else '', selected))
        scene.toyxyz_retarget.contact_marker_editor = None
        scene.collection.children.unlink(group)


@persistent
def after_save(_):
    context = bpy.context
    for scene, group, active, selected in paused:
        scene.collection.children.link(group)
        scene.toyxyz_retarget.contact_marker_editor = group
        if context.scene == scene:
            for name in selected:
                obj = context.view_layer.objects.get(name)
                if obj is not None:
                    obj.select_set(True)
            if active:
                context.view_layer.objects.active = context.view_layer.objects.get(active)
            refresh(context)
    paused.clear()


@persistent
def cleanup(_=None):
    paused.clear()
    for scene in bpy.data.scenes:
        if scene.library is None and scene.toyxyz_retarget.contact_marker_editor is not None:
            scene.toyxyz_retarget.contact_marker_editor = None
    for group in list(bpy.data.collections):
        if group.library is None and group.get(TAG):
            discard_group(group)


HANDLERS = {'save_pre': before_save, 'save_post': after_save, 'save_post_fail': after_save, 'load_post': cleanup}


def register():
    for name, callback in HANDLERS.items():
        getattr(bpy.app.handlers, name).append(callback)


def unregister():
    cleanup()
    for name, callback in HANDLERS.items():
        getattr(bpy.app.handlers, name).remove(callback)
