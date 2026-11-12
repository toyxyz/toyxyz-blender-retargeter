"""Toyxyz Blender Retargeter."""

import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty,
)

from . import actors, arp_fk, bake, floor_markers, mapping_ui, reference, runtime
from .mapping import ALL_FIXED_ROLES, reset_mapping, resize, role_label, role_property
from .retarget import evaluated_matrices


bl_info = {
    'name': 'Toyxyz Blender Retargeter',
    'author': 'Toyxyz',
    'version': (0, 4, 29),
    'blender': (5, 0, 0),
    'location': '3D View > Sidebar > Retarget',
    'category': 'Animation',
}

SIDES = (('source', 'Source', ''), ('target', 'Target', ''))


def is_armature(profile, obj):
    if obj.type != 'ARMATURE' or obj.get(runtime.TAG) or obj.get(reference.TAG):
        return False
    key, _, side = actors.owner_of(profile)
    return side != 'target' or not actors.duplicate_target(profile.id_data, key, obj)


def resize_chains(profile, context):
    resize(profile.spine_bones, profile.spine_count)
    resize(profile.neck_bones, profile.neck_count)
    _, settings, _ = actors.owner_of(profile)
    if settings is not None:
        mapping_ui.sync_rows(settings)


def change_rig(profile, context):
    key, settings, side = actors.owner_of(profile)
    if side == 'target' and actors.duplicate_target(profile.id_data, key, profile.rig):
        previous = profile.accepted_rig or profile.mapping_rig
        profile.rig = previous if not actors.duplicate_target(profile.id_data, key, previous) else None
        settings.status = 'This target is already assigned to another actor.'
        return
    profile.accepted_rig = profile.rig
    if profile.mapping_rig is not None and profile.mapping_rig != profile.rig:
        reset_mapping(profile)
        profile.mapping_rig = profile.rig
    resize_chains(profile, context)


def reach_split_changed(part):
    def update(settings, context):
        if getattr(settings, f'{part}_separate_reach'):
            for side in ('left', 'right'):
                for channel in ('position', 'rotation'):
                    name = f'{part}_{side}_{channel}_reach'
                    if not settings.is_property_set(name):
                        setattr(settings, name, getattr(settings, f'{part}_{channel}_reach'))
        runtime.request_refresh(settings, context)
    return update


class TRG_PG_ChainBone(bpy.types.PropertyGroup):
    bone: StringProperty(name='Bone', description='Assign a bone to this position in the chain')


class TRG_PG_RigProfile(bpy.types.PropertyGroup):
    rig: PointerProperty(name='Armature', description='Armature to read motion from or apply it to',
                         type=bpy.types.Object, poll=is_armature, update=change_rig)
    mapping_rig: PointerProperty(type=bpy.types.Object, options={'HIDDEN'})
    accepted_rig: PointerProperty(type=bpy.types.Object, options={'HIDDEN'})
    spine_count: IntProperty(name='Spine Bones', description='Number of spine bones to map on this rig',
                             min=1, max=12, default=1, update=resize_chains)
    neck_count: IntProperty(name='Neck Bones', description='Number of neck bones to map on this rig',
                            min=0, max=12, default=0, update=resize_chains)
    spine_bones: CollectionProperty(type=TRG_PG_ChainBone)
    neck_bones: CollectionProperty(type=TRG_PG_ChainBone)
    reference_pose: StringProperty(options={'HIDDEN'})
    reference_rig: PointerProperty(type=bpy.types.Object, options={'HIDDEN'})
    contact_marker_offsets: StringProperty(options={'HIDDEN'})
    contact_marker_rig: PointerProperty(type=bpy.types.Object, options={'HIDDEN'})
    floor_mode: EnumProperty(name='Floor Origin', description='Choose the ground level for this rig', items=(
        ('WORLD', 'World Z = 0', 'Use the Blender world ground plane'),
        ('REFERENCE', 'Reference Soles', 'Place the floor under this armature’s calibrated feet'),
    ), default='WORLD')
    floor_offset: FloatProperty(name='Floor Offset (m)', min=-2., max=2., precision=3,
                                description='Raise or lower the ground level in metres')


for role in ALL_FIXED_ROLES:
    label = role_label(role)
    TRG_PG_RigProfile.__annotations__[role_property(role)] = StringProperty(
        name=label, description=f'Assign a rig bone to {label}')


class TRG_PG_Settings(bpy.types.PropertyGroup):
    source: PointerProperty(type=TRG_PG_RigProfile)
    target: PointerProperty(type=TRG_PG_RigProfile)
    reconnect_on_load: BoolProperty(options={'HIDDEN'})
    contact_marker_editor: PointerProperty(type=bpy.types.Collection, options={'HIDDEN'})
    map_body: CollectionProperty(type=mapping_ui.TRG_PG_MapRow)
    map_fingers: CollectionProperty(type=mapping_ui.TRG_PG_MapRow)
    map_toes: CollectionProperty(type=mapping_ui.TRG_PG_MapRow)
    map_active_group: EnumProperty(items=mapping_ui.GROUPS, default='body')
    map_body_index: IntProperty(min=0, update=lambda settings, _: setattr(settings, 'map_active_group', 'body'))
    map_fingers_index: IntProperty(min=0, update=lambda settings, _: setattr(settings, 'map_active_group', 'fingers'))
    map_toes_index: IntProperty(min=0, update=lambda settings, _: setattr(settings, 'map_active_group', 'toes'))
    status: StringProperty(options={'SKIP_SAVE'})
    solver_strength: FloatProperty(
        name='Solver Strength', subtype='PERCENTAGE', min=0., max=100., default=100.,
        description='Blend from direct FK at 0% to the configured solver at 100%',
        update=runtime.request_refresh)
    travel: EnumProperty(name='Root Travel', description='Choose how to transfer horizontal root movement', items=(
        ('PROPORTIONAL', 'Scale to Proportions', 'Scale horizontal travel for the target skeleton'),
        ('SOURCE', 'Match Source Distance', 'Transfer the source horizontal distance in world units'),
        ('IN_PLACE', 'In Place', 'Remove horizontal source root travel'),
    ), default='PROPORTIONAL', update=runtime.request_refresh)
    stride_scale: FloatProperty(name='Travel Multiplier', description='Multiply the target’s horizontal travel distance',
                                 min=0., max=4., default=1., update=runtime.request_refresh)
    hip_offset: FloatProperty(name='Hip Height Offset (m)', min=-1., max=1., default=0., precision=3,
                              description='Raise or lower the target hips in metres', update=runtime.request_refresh)
    floor_contact: BoolProperty(name='Floor Contact', description='Correct foot penetration around the heel or forefoot',
                                default=True, update=runtime.request_refresh)
    foot_contact_stiffness: FloatProperty(
        name='Feet Contact Stiffness', subtype='PERCENTAGE', min=0., max=100., default=0.,
        description='Preserve ankle rotation during floor contact. At 100%, correct penetration using position only',
        update=runtime.request_refresh)
    knee_extension_damping: FloatProperty(
        name='Extension Damping', subtype='PERCENTAGE', min=0., max=100., default=70.,
        description='Ease into full leg extension and preserve some of the FK knee bend',
        update=runtime.request_refresh)
    knee_bend_preservation: FloatProperty(
        name='Bend Preservation', subtype='PERCENTAGE', min=0., max=100., default=60.,
        description='Follow the source leg bend in the air and ease transitions into floor correction',
        update=runtime.request_refresh)
    show_map: BoolProperty(name='Bone Mapping', description='Show the source and target bone mappings', default=True)
    show_body: BoolProperty(name='Main Bones', description='Show body and limb mappings and spine and neck counts', default=True)
    show_fingers: BoolProperty(name='Fingers', description='Show finger bone mappings', default=False)
    show_toes: BoolProperty(name='Toes', description='Show toe bone mappings', default=False)
    show_reference: BoolProperty(name='Reference Pose', description='Show reference pose tools for both rigs', default=False)
    show_solver: BoolProperty(name='Solver Options', description='Show hand, foot, knee, and root settings', default=False)
    show_hand_reach: BoolProperty(name='Hands / Arms', description='Show wrist Reach settings', default=False)
    show_foot_reach: BoolProperty(name='Feet / Legs', description='Show ankle Reach and floor contact settings', default=False)
    show_knee: BoolProperty(name='Knee', description='Show knee bend and extension settings', default=False)
    show_root: BoolProperty(name='Root', description='Show root travel and hip height settings', default=False)
    hand_separate_reach: BoolProperty(name='Separate Left / Right', description='Adjust each wrist independently',
                                     update=reach_split_changed('hand'))
    foot_separate_reach: BoolProperty(name='Separate Left / Right', description='Adjust each ankle independently',
                                     update=reach_split_changed('foot'))
    calibrated_wrist_reach: BoolProperty(
        name='Calibrated Wrist Reach', description='Follow source wrist travel and use the reference pose to guide the elbow',
        update=runtime.request_refresh)
    staged_ankle_reach: BoolProperty(
        name='Staged Ankle Reach', description='Follow ankle travel while guiding knee flexion with the reference pose',
        update=runtime.request_refresh)


for part, joint, default_position in (('hand', 'wrist', 0.), ('foot', 'ankle', 100.)):
    descriptions = {
        'position': ('Follow the source wrist position using the selected Reach method' if part == 'hand' else
                     'Follow the source ankle path scaled to the target skeleton'),
        'rotation': f'Preserve the source {joint} orientation after position correction',
    }
    for suffix in ('', '_left', '_right'):
        for channel, default in (('position', default_position), ('rotation', 100.)):
            name = f'{part}{suffix}_{channel}_reach'
            TRG_PG_Settings.__annotations__[name] = FloatProperty(
                name=f'{channel.title()} Reach', description=descriptions[channel], subtype='PERCENTAGE',
                min=0., max=100., default=default, update=runtime.request_refresh)


class TRG_PG_Actor(bpy.types.PropertyGroup):
    key: StringProperty(options={'HIDDEN'})
    settings: PointerProperty(type=TRG_PG_Settings)


class TRG_UL_actors(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        settings = actors.settings_for(context.scene, item.key)
        row = layout.row(align=True)
        row.label(text=item.name, icon='ARMATURE_DATA')
        if settings is not None and settings.target.rig is not None:
            row.label(text=settings.target.rig.name)
        if runtime.is_active(context.scene, item.key):
            row.label(text='', icon='LINKED')


class TRG_OT_actor_list(bpy.types.Operator):
    bl_idname = 'toyxyz_retarget.actor_list'
    bl_label = 'Manage Retarget Actors'
    bl_options = {'UNDO'}
    action: EnumProperty(items=(('ADD', 'Add', 'Add an independent source and target pair'),
                                ('REMOVE', 'Delete', 'Remove the selected actor and disconnect its target')))

    @classmethod
    def description(cls, context, properties):
        return ('Add an independent source and target pair' if properties.action == 'ADD'
                else 'Remove the selected actor and disconnect its target')

    def execute(self, context):
        scene = context.scene
        if reference.editor is not None or floor_markers.group_for(scene) is not None:
            return {'CANCELLED'}
        if self.action == 'ADD':
            actors.append(scene, legacy=not scene.toyxyz_retarget_actors_initialized)
            return {'FINISHED'}
        index = scene.toyxyz_retarget_actor_index
        rows = scene.toyxyz_retarget_actors
        if not 0 <= index < len(rows):
            return {'CANCELLED'}
        key = rows[index].key
        runtime.stop(scene, key)
        rows.remove(index)
        if key == actors.LEGACY:
            scene.property_unset('toyxyz_retarget')
        scene.toyxyz_retarget_actor_index = max(0, min(index, len(rows) - 1))
        return {'FINISHED'}


class TRG_OT_actor_connections(bpy.types.Operator):
    bl_idname = 'toyxyz_retarget.actor_connections'
    bl_label = 'Actor Connections'
    connect: BoolProperty(default=True)

    @classmethod
    def description(cls, context, properties):
        return ('Connect all actors with a source and target' if properties.connect
                else 'Disconnect all actors in this scene')

    def execute(self, context):
        scene = context.scene
        if reference.editor is not None or floor_markers.group_for(scene) is not None:
            return {'CANCELLED'}
        if not self.connect:
            for key, _ in actors.entries(scene):
                runtime.stop(scene, key)
            return {'FINISHED'}
        failures = []
        for key, settings in actors.entries(scene):
            if not settings.source.rig or not settings.target.rig or runtime.is_active(scene, key):
                continue
            try:
                runtime.start(context, key=key)
                settings.status = ''
            except (RuntimeError, ValueError) as exc:
                settings.status = str(exc)
                failures.append(str(exc))
        if failures:
            self.report({'WARNING'}, f'{len(failures)} actor(s) could not connect. {failures[0]}')
        return {'FINISHED'}


class TRG_OT_use_selected_rig(bpy.types.Operator):
    bl_idname = 'toyxyz_retarget.use_selected_rig'
    bl_label = 'Use Selected Rig'
    bl_description = 'Assign the selected armature or the armature of the selected bone'
    bl_options = {'UNDO'}
    side: EnumProperty(items=SIDES)

    def execute(self, context):
        scene = context.scene
        settings = actors.settings_for(scene)
        if (settings is None or runtime.is_active(scene) or reference.editor is not None
                or floor_markers.group_for(scene) is not None):
            return {'CANCELLED'}
        rig = context.object
        if rig is None or rig.type != 'ARMATURE' or not (
            rig.select_get() if context.mode == 'OBJECT' else mapping_ui.selected_bone(context, rig)
        ):
            self.report({'WARNING'}, 'Select an armature or one of its bones in the viewport.')
            return {'CANCELLED'}
        profile = getattr(settings, self.side)
        if not is_armature(profile, rig):
            message = ('This target is already assigned to another actor.'
                       if self.side == 'target' and actors.duplicate_target(scene, actors.selected_key(scene), rig)
                       else 'Choose an editable scene armature.')
            self.report({'WARNING'}, message)
            return {'CANCELLED'}
        profile.rig = rig
        settings.status = ''
        return {'FINISHED'}


class TRG_OT_live(bpy.types.Operator):
    bl_idname = 'toyxyz_retarget.live'
    bl_label = 'Connect or Disconnect'
    bl_description = 'Transfer motion from the source rig to the target as you edit or play'

    @classmethod
    def description(cls, context, properties):
        if runtime.is_active():
            return 'Disconnect the rigs and restore the target’s own animation.'
        return cls.bl_description

    def execute(self, context):
        settings = actors.settings_for(context.scene)
        if settings is None:
            return {'CANCELLED'}
        if reference.editor:
            self.report({'ERROR'}, 'Finish editing the reference pose first.')
            return {'CANCELLED'}
        if floor_markers.group_for(context.scene) is not None:
            self.report({'ERROR'}, 'Finish editing the contact markers first.')
            return {'CANCELLED'}
        try:
            if runtime.is_active():
                runtime.stop()
                settings.status = ''
            else:
                runtime.start(context)
                settings.status = ''
        except (RuntimeError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            settings.status = str(exc)
            return {'CANCELLED'}
        return {'FINISHED'}


class TRG_OT_reference(bpy.types.Operator):
    bl_idname = 'toyxyz_retarget.reference_pose'
    bl_label = 'Reference Pose'
    bl_description = 'Set the pose used to align the rigs for retargeting'
    ACTIONS = (
        ('EDIT', 'Edit', 'Adjust the reference pose using a temporary edit rig'),
        ('CAPTURE', 'Capture Current', 'Use the current evaluated pose as the reference'),
        ('RESET', 'Use Rig Rest', 'Use the original rest pose as the reference'),
        ('SAVE', 'Save Reference', 'Save the edited pose as the reference'),
        ('CANCEL', 'Cancel Edit', 'Discard the reference pose edits'),
    )
    side: EnumProperty(items=SIDES)
    action: EnumProperty(items=ACTIONS)

    @classmethod
    def description(cls, context, properties):
        return next((help_text for action, _, help_text in cls.ACTIONS if action == properties.action),
                    cls.bl_description)

    def execute(self, context):
        if floor_markers.group_for(context.scene) is not None:
            self.report({'ERROR'}, 'Finish editing the contact markers first.')
            return {'CANCELLED'}
        if runtime.is_active() and reference.connected_session(context.scene) is None:
            return {'CANCELLED'}
        settings = actors.settings_for(context.scene)
        if settings is None:
            return {'CANCELLED'}
        profile = getattr(settings, self.side)
        try:
            if self.action in ('SAVE', 'CANCEL'):
                reference.finish(context, commit=self.action == 'SAVE')
            elif reference.editor or not profile.rig:
                return {'CANCELLED'}
            elif self.action == 'EDIT':
                reference.begin(context, self.side)
            elif self.action == 'CAPTURE':
                matrices = evaluated_matrices(profile.rig, context.evaluated_depsgraph_get())
                reference.apply_reference(context, self.side, matrices)
            else:
                reference.apply_reference(context, self.side)
        except (RuntimeError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        settings.status = ''
        return {'FINISHED'}


class TRG_OT_reset_reference_draft(bpy.types.Operator):
    bl_idname = 'toyxyz_retarget.reset_reference_draft'
    bl_label = 'Reset'
    bl_description = 'Discard the current pose edits and keep the reference editor open'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        active = reference.editor
        return (active is not None and context.scene == active.scene and context.object == active.obj
                and context.mode == 'POSE')

    def execute(self, context):
        try:
            reference.editor.reset()
        except (RuntimeError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        settings = actors.settings_for(context.scene, reference.editor.actor_key)
        settings.status = ''
        return {'FINISHED'}


def disclosure(layout, settings, key):
    opened = getattr(settings, key)
    layout.prop(settings, key, icon='TRIA_DOWN' if opened else 'TRIA_RIGHT', emboss=False)
    return opened


def reference_button(layout, side, action, text):
    op = layout.operator('toyxyz_retarget.reference_pose', text=text)
    op.side, op.action = side, action


def draw_reach(layout, settings, part):
    layout.prop(settings, f'{part}_separate_reach')
    prefixes = (('Left', f'{part}_left'), ('Right', f'{part}_right')) if getattr(
        settings, f'{part}_separate_reach') else (('', part),)
    for label, prefix in prefixes:
        column = layout.column(align=True)
        if label:
            column.label(text=label)
        column.prop(settings, f'{prefix}_position_reach', slider=True)
        column.prop(settings, f'{prefix}_rotation_reach', slider=True)


class TRG_PT_retarget(bpy.types.Panel):
    bl_idname = 'TRG_PT_retarget'
    bl_label = 'Toyxyz Blender Retargeter'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Retarget'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = actors.settings_for(scene)
        marker_edit = floor_markers.group_for(scene) is not None
        actor_box = layout.column(align=True)
        actor_box.enabled = not marker_edit and reference.editor is None
        actor_box.label(text='Actors')
        row = actor_box.row()
        row.template_list('TRG_UL_actors', '', scene, 'toyxyz_retarget_actors',
                          scene, 'toyxyz_retarget_actor_index', rows=3)
        buttons = row.column(align=True)
        buttons.operator('toyxyz_retarget.actor_list', text='', icon='ADD').action = 'ADD'
        remove = buttons.row()
        remove.enabled = bool(scene.toyxyz_retarget_actors)
        remove.operator('toyxyz_retarget.actor_list', text='', icon='REMOVE').action = 'REMOVE'
        if len(scene.toyxyz_retarget_actors) > 1:
            row = actor_box.row(align=True)
            row.operator('toyxyz_retarget.actor_connections', text='Connect All', icon='LINKED').connect = True
            row.operator('toyxyz_retarget.actor_connections', text='Disconnect All', icon='UNLINKED').connect = False
        if settings is None:
            return
        if marker_edit:
            layout.label(text='Editing Contact Markers', icon='EMPTY_AXIS')
            if reference.connected_session(context.scene) is not None:
                layout.label(text='Connected', icon='CHECKMARK')
            layout.prop(settings, 'floor_contact')
            layout.label(text='Move the heel and ball markers.', icon='INFO')
            row = layout.row(align=True)
            for action, label in (('SAVE', 'Save'), ('RESET', 'Reset'), ('CANCEL', 'Cancel')):
                floor_markers.button(row, action, label)
            return
        if reference.editor:
            layout.label(text=f'Editing {reference.editor.side.title()} Reference', icon='ARMATURE_DATA')
            if reference.connected_session(context.scene):
                layout.label(text='Connected', icon='CHECKMARK')
            if settings.status:
                layout.label(text=settings.status)
            row = layout.row(align=True)
            reference_button(row, reference.editor.side, 'SAVE', 'Save')
            row.operator('toyxyz_retarget.reset_reference_draft')
            reference_button(row, reference.editor.side, 'CANCEL', 'Cancel')
            return
        active = runtime.is_active()
        controller_fk = arp_fk.layout(settings.target.rig) is not None
        profiles = (settings.source, settings.target)
        pair = layout.column()
        pair.enabled = not active
        for (side, title, _), profile in zip(SIDES, profiles):
            row = pair.row(align=True)
            row.prop(profile, 'rig', text=title)
            row.operator('toyxyz_retarget.use_selected_rig', text='', icon='EYEDROPPER').side = side
        if controller_fk:
            layout.label(text='Target: Auto-Rig Pro FK', icon='ARMATURE_DATA')
        layout.operator('toyxyz_retarget.live', text='Disconnect' if active else 'Connect',
                        icon='UNLINKED' if active else 'LINKED')
        if active:
            layout.label(text='Connected', icon='CHECKMARK')
        bake_row = layout.row(align=True)
        bake_row.operator('toyxyz_retarget.bake', icon='ACTION')
        bake_row.operator('toyxyz_retarget.bake_all', icon='NLA')
        if settings.status:
            layout.label(text=settings.status)
        if disclosure(layout, settings, 'show_solver'):
            box = layout.box()
            box.prop(settings, 'solver_strength', slider=True)
            if disclosure(box, settings, 'show_hand_reach'):
                hands = box.column()
                hands.prop(settings, 'calibrated_wrist_reach')
                draw_reach(hands, settings, 'hand')
            if disclosure(box, settings, 'show_foot_reach'):
                feet = box.column()
                feet.prop(settings, 'staged_ankle_reach')
                draw_reach(feet, settings, 'foot')
                feet.separator()
                feet.prop(settings, 'floor_contact')
                stiffness = feet.row()
                stiffness.enabled = settings.floor_contact
                stiffness.prop(settings, 'foot_contact_stiffness', slider=True)
                markers = feet.row()
                markers.enabled = settings.target.rig is not None
                floor_markers.button(markers, 'EDIT', 'Edit Contact Markers')
                for (_, title, _), profile in zip(SIDES, profiles):
                    floor = feet.column(align=True)
                    floor.enabled = not active
                    floor.prop(profile, 'floor_mode', text=f'{title} Floor')
                    floor.prop(profile, 'floor_offset', text=f'{title} Floor Offset (m)')
            if disclosure(box, settings, 'show_knee'):
                knee = box.column(align=True)
                knee.prop(settings, 'knee_extension_damping', slider=True)
                knee.prop(settings, 'knee_bend_preservation', slider=True)
            if disclosure(box, settings, 'show_root'):
                root = box.column(align=True)
                root.prop(settings, 'travel')
                root.prop(settings, 'stride_scale')
                root.prop(settings, 'hip_offset')
        if disclosure(layout, settings, 'show_reference'):
            for (side, title, _), profile in zip(SIDES, profiles):
                box = layout.box()
                box.enabled = profile.rig is not None and (not active or reference.connected_session(context.scene) is not None)
                custom = profile.reference_rig == profile.rig and bool(profile.reference_pose)
                box.label(text=f'{title} · ' + ('Custom Reference' if custom else 'Rig Rest'))
                reference_button(box, side, 'EDIT', 'Edit Reference Pose')
                reference_button(box, side, 'CAPTURE', 'Use Current Pose')
                reference_button(box, side, 'RESET', 'Use Rig Rest')
        if disclosure(layout, settings, 'show_map'):
            box = layout.box()
            build = box.row()
            build.enabled = not active
            build.operator('toyxyz_retarget.build_bones_list', icon='BONE_DATA')
            files = box.split(factor=.5, align=True)
            import_row = files.row()
            import_row.enabled = not active
            import_row.operator('toyxyz_retarget.import_bone_mapping', text='Import', icon='IMPORT')
            files.operator('toyxyz_retarget.export_bone_mapping', text='Export', icon='EXPORT')
            mapping_ui.draw_editor(box, settings, editable=not active, controller_fk=controller_fk)
            if disclosure(box, settings, 'show_body'):
                main = box.column()
                columns = main.row()
                columns.enabled = not active
                for (side, title, _), profile in zip(SIDES, profiles):
                    col = columns.column(align=True)
                    col.enabled = side != 'target' or not controller_fk
                    col.label(text=title)
                    col.prop(profile, 'spine_count')
                    col.prop(profile, 'neck_count')
                if (settings.source.spine_count, settings.source.neck_count) != (
                    settings.target.spine_count, settings.target.neck_count
                ):
                    main.label(text='Unequal chains are distributed by length.', icon='INFO')
                mapping_ui.draw_list(main, settings, 'body', 6)
            if disclosure(box, settings, 'show_fingers'):
                mapping_ui.draw_list(box, settings, 'fingers', 4)
            if disclosure(box, settings, 'show_toes'):
                mapping_ui.draw_list(box, settings, 'toes', 4)


CLASSES = (*mapping_ui.CLASSES, TRG_PG_ChainBone, TRG_PG_RigProfile, TRG_PG_Settings,
           TRG_PG_Actor, TRG_UL_actors, TRG_OT_actor_list, TRG_OT_actor_connections, TRG_OT_use_selected_rig,
           TRG_OT_live, TRG_OT_reference, TRG_OT_reset_reference_draft, floor_markers.TRG_OT_contact_markers,
           bake.TRG_OT_bake_retarget, bake.TRG_OT_bake_all, TRG_PT_retarget)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.toyxyz_retarget = PointerProperty(type=TRG_PG_Settings)
    bpy.types.Scene.toyxyz_retarget_actors = CollectionProperty(type=TRG_PG_Actor)
    bpy.types.Scene.toyxyz_retarget_actor_index = IntProperty(min=0)
    bpy.types.Scene.toyxyz_retarget_actors_initialized = BoolProperty(options={'HIDDEN'})
    bpy.app.handlers.load_post.append(actors.ensure_existing)
    bpy.app.timers.register(actors.ensure_existing, first_interval=0.)
    runtime.register()
    reference.register()
    floor_markers.register()
    bpy.app.handlers.load_post.append(mapping_ui.restore_rows)


def unregister():
    bpy.app.handlers.load_post.remove(mapping_ui.restore_rows)
    floor_markers.unregister()
    reference.unregister()
    runtime.unregister()
    if bpy.app.timers.is_registered(actors.ensure_existing):
        bpy.app.timers.unregister(actors.ensure_existing)
    bpy.app.handlers.load_post.remove(actors.ensure_existing)
    del bpy.types.Scene.toyxyz_retarget_actors_initialized
    del bpy.types.Scene.toyxyz_retarget_actor_index
    del bpy.types.Scene.toyxyz_retarget_actors
    del bpy.types.Scene.toyxyz_retarget
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
