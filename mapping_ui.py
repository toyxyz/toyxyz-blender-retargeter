"""Paired mapping lists and viewport selection tools."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import actors, arp_fk, floor_markers, reference, runtime
from .mapping import (
    ALL_FIXED_ROLES, FINGER_CHAINS, TOE_CHAINS, body_rows, bone_for_role, build_mapping,
    mapped_names, resize, role_field, role_label, role_property,
)


FINGER_ROWS = tuple(role for _, chain in FINGER_CHAINS for role in chain)
TOE_ROWS = ('LeftToeBase', 'RightToeBase', *(role for _, chain in TOE_CHAINS for role in chain))
SIDES = (('source', 'Source', ''), ('target', 'Target', ''))
GROUPS = (('body', 'Main Bones', ''), ('fingers', 'Fingers', ''), ('toes', 'Toes', ''))
MAPPING_FORMAT = 'toyxyz_live_retarget.bone_mapping'
MAPPING_VERSION = 1
CHAINS = ('spine', 'neck')


class TRG_PG_MapRow(bpy.types.PropertyGroup):
    role: StringProperty()


def sync_rows(settings):
    active_group = settings.map_active_group
    for group, roles in (('body', body_rows(settings.source, settings.target)),
                         ('fingers', FINGER_ROWS), ('toes', TOE_ROWS)):
        key = 'map_' + group
        rows = getattr(settings, key)
        if tuple(item.role for item in rows) == roles:
            continue
        active_key = key + '_index'
        index = getattr(settings, active_key)
        previous = rows[index].role if 0 <= index < len(rows) else None
        rows.clear()
        for role in roles:
            rows.add().role = role
        setattr(settings, active_key, roles.index(previous) if previous in roles else 0)
    settings.map_active_group = active_group


@persistent
def restore_rows(_):
    for scene in bpy.data.scenes:
        if scene.library is None:
            for _, settings in actors.entries(scene):
                if settings.source.rig or settings.target.rig:
                    sync_rows(settings)


class TRG_OT_build_bones_list(bpy.types.Operator):
    """Fill missing matches and update the spine and neck rows from both rigs"""

    bl_idname = 'toyxyz_retarget.build_bones_list'
    bl_label = 'Build Bones List'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if runtime.is_active() or reference.editor or floor_markers.group_for(context.scene) is not None:
            return {'CANCELLED'}
        settings = actors.settings_for(context.scene)
        if settings is None or not settings.source.rig or not settings.target.rig:
            self.report({'WARNING'}, 'Choose a source and target armature first.')
            return {'CANCELLED'}
        incomplete = []
        for profile, label in ((settings.source, 'Source'), (settings.target, 'Target')):
            controller_fk = label == 'Target' and arp_fk.layout(profile.rig) is not None
            build_mapping(profile, controller_fk=controller_fk)
            try:
                mapped_names(profile, controller_fk=controller_fk)
            except ValueError:
                incomplete.append(label)
        sync_rows(settings)
        settings.status = ('Bone lists built. Review ' + ' and '.join(incomplete) + ' mapping.'
                           if incomplete else 'Bone lists ready.')
        return {'FINISHED'}


def export_profile(profile):
    return {
        'bones': {role: bone_for_role(profile, role) for role in ALL_FIXED_ROLES},
        **{chain: [bone_for_role(profile, f'{chain.title()}{i + 1}')
                   for i in range(getattr(profile, f'{chain}_count'))] for chain in CHAINS},
    }


def read_mapping(path, settings):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('format') != MAPPING_FORMAT:
        raise ValueError('Choose a Toyxyz Blender Retargeter bone mapping file.')
    if type(data.get('version')) is not int or data['version'] != MAPPING_VERSION:
        raise ValueError('Unsupported bone mapping version.')

    for side, label, _ in SIDES:
        entry = data.get(side)
        bones = entry.get('bones') if isinstance(entry, dict) else None
        if not isinstance(bones, dict) or bones.keys() != set(ALL_FIXED_ROLES):
            raise ValueError(f'{label} has an invalid bone list.')
        names = list(bones.values())
        for chain in CHAINS:
            count = getattr(settings, side).bl_rna.properties[f'{chain}_count']
            values = entry.get(chain)
            if not isinstance(values, list) or not count.hard_min <= len(values) <= count.hard_max:
                raise ValueError(f'{label} has an invalid {chain} chain.')
            names.extend(values)
        if any(not isinstance(name, str) or '\0' in name for name in names):
            raise ValueError(f'{label} has an invalid bone name.')
        for name in names:
            name.encode('utf-8')
    return data


class TRG_OT_import_bone_mapping(bpy.types.Operator, ImportHelper):
    """Load both bone lists and chain counts for the selected actor"""

    bl_idname = 'toyxyz_retarget.import_bone_mapping'
    bl_label = 'Import Bone Mapping'
    bl_options = {'UNDO'}
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        scene = context.scene
        settings = actors.settings_for(scene)
        if settings is None or reference.editor or floor_markers.group_for(scene) is not None:
            return {'CANCELLED'}
        if runtime.is_active(scene):
            self.report({'WARNING'}, 'Disconnect this actor before importing its bone mapping.')
            return {'CANCELLED'}
        try:
            data = read_mapping(Path(self.filepath), settings)
        except (OSError, ValueError, RecursionError) as error:
            self.report({'ERROR'}, f'Could not import bone mapping. {error}')
            return {'CANCELLED'}

        missing = []
        for side, label, _ in SIDES:
            profile = getattr(settings, side)
            entry = data[side]
            for role, name in entry['bones'].items():
                setattr(profile, role_property(role), name)
            for chain in CHAINS:
                names = entry[chain]
                setattr(profile, f'{chain}_count', len(names))
                items = getattr(profile, f'{chain}_bones')
                resize(items, len(names))
                for item, name in zip(items, names):
                    item.bone = name
            profile.mapping_rig = profile.rig
            if profile.rig is not None:
                names = {*entry['bones'].values(), *entry['spine'], *entry['neck']}
                if any(name and name not in profile.rig.data.bones for name in names):
                    missing.append(label)
        sync_rows(settings)
        settings.status = 'Bone mapping imported.'
        if missing:
            settings.status += ' Review missing bones on ' + ' and '.join(missing) + '.'
        self.report({'WARNING'} if missing else {'INFO'}, settings.status)
        return {'FINISHED'}


class TRG_OT_export_bone_mapping(bpy.types.Operator, ExportHelper):
    """Save both bone lists and chain counts for the selected actor"""

    bl_idname = 'toyxyz_retarget.export_bone_mapping'
    bl_label = 'Export Bone Mapping'
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        settings = actors.settings_for(context.scene)
        if settings is None:
            return {'CANCELLED'}
        data = {'format': MAPPING_FORMAT, 'version': MAPPING_VERSION,
                **{side: export_profile(getattr(settings, side)) for side, _, _ in SIDES}}
        path = Path(self.filepath)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
                temporary = Path(file.name)
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write('\n')
            temporary.replace(path)
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, f'Could not export bone mapping. {error}')
            return {'CANCELLED'}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.report({'INFO'}, 'Bone mapping exported.')
        return {'FINISHED'}


def selected_bone(context, rig):
    if rig is None or context.object != rig:
        return None
    if context.mode == 'POSE':
        active = context.active_pose_bone
        if active is not None and active.select:
            return active.name
        bones = [bone for bone in rig.pose.bones if bone.select]
    elif context.mode == 'EDIT_ARMATURE':
        active = rig.data.edit_bones.active
        if active is not None and active.select:
            return active.name
        bones = [bone for bone in rig.data.edit_bones if bone.select]
    else:
        return None
    return bones[0].name if len(bones) == 1 else None


class TRG_OT_use_selected_bone(bpy.types.Operator):
    """Assign the active selected viewport bone to this mapping"""

    bl_idname = 'toyxyz_retarget.use_selected_bone'
    bl_label = 'Use Selected Bone'
    bl_options = {'REGISTER', 'UNDO'}
    side: EnumProperty(items=SIDES)
    role: StringProperty()

    def execute(self, context):
        if runtime.is_active() or reference.editor or floor_markers.group_for(context.scene) is not None:
            return {'CANCELLED'}
        settings = actors.settings_for(context.scene)
        if settings is None:
            return {'CANCELLED'}
        profile = getattr(settings, self.side)
        if self.side == 'target' and arp_fk.layout(profile.rig) is not None:
            self.report({'WARNING'}, 'Build Bones List assigns the Auto-Rig Pro FK controls.')
            return {'CANCELLED'}
        roles = (*body_rows(profile, profile), *FINGER_ROWS, *TOE_ROWS)
        if self.role not in roles:
            self.report({'WARNING'}, 'Choose a role in this rig’s bone list.')
            return {'CANCELLED'}
        name = selected_bone(context, profile.rig)
        if not name:
            self.report({'WARNING'}, f'Select a bone on the {self.side} armature in Pose or Edit Mode.')
            return {'CANCELLED'}
        assigned = next((role for role in roles if role != self.role and bone_for_role(profile, role) == name), None)
        if assigned:
            self.report({'WARNING'}, f'{name} is already assigned to {role_label(assigned)}.')
            return {'CANCELLED'}
        owner, key = role_field(profile, self.role)
        if owner is None:
            self.report({'WARNING'}, 'Increase the chain count before assigning this bone.')
            return {'CANCELLED'}
        setattr(owner, key, name)
        settings.status = ''
        return {'FINISHED'}


class TRG_OT_select_mapping_row(bpy.types.Operator):
    bl_idname = 'toyxyz_retarget.select_mapping_row'
    bl_label = 'Select Mapping'
    bl_description = 'Select this pair to edit its source and target bones'
    bl_options = {'INTERNAL'}
    group: EnumProperty(items=GROUPS)
    index: IntProperty(min=0)

    def execute(self, context):
        settings = actors.settings_for(context.scene)
        if settings is None:
            return {'CANCELLED'}
        key = 'map_' + self.group
        if self.index >= len(getattr(settings, key)):
            return {'CANCELLED'}
        setattr(settings, key + '_index', self.index)
        settings.map_active_group = self.group
        return {'FINISHED'}


class TRG_UL_bone_pairs(bpy.types.UIList):
    def draw_item(self, _context, layout, settings, item, _icon, _active, active_property, index):
        split = layout.split(factor=.5)
        source = bone_for_role(settings.source, item.role)
        target = bone_for_role(settings.target, item.role)
        group = active_property.removeprefix('map_').removesuffix('_index')
        for bone, empty in ((source, role_label(item.role)), (target, '—')):
            row = split.row()
            op = row.operator('toyxyz_retarget.select_mapping_row', text=bone or empty,
                              icon='BONE_DATA' if bone else 'DOT', emboss=False, translate=False)
            op.group, op.index = group, index


def draw_list(layout, settings, group, visible_rows):
    key = 'map_' + group
    rows = getattr(settings, key)
    if not rows:
        layout.label(text='Build the bone list to begin.')
        return
    header = layout.split(factor=.5)
    header.label(text='Source Bones')
    header.label(text='Target Bones')
    layout.template_list('TRG_UL_bone_pairs', group, settings, key, settings, key + '_index', rows=visible_rows)


def active_role(settings):
    key = 'map_' + settings.map_active_group
    rows = getattr(settings, key)
    index = getattr(settings, key + '_index')
    return rows[index].role if 0 <= index < len(rows) else None


def draw_editor(layout, settings, *, editable, controller_fk=False):
    role = active_role(settings)
    if role is None:
        return
    editor = layout.box()
    editor.enabled = editable
    editor.label(text=role_label(role))
    for side, title, _ in SIDES:
        profile = getattr(settings, side)
        owner, field = role_field(profile, role)
        row = editor.row(align=True)
        if profile.rig is None or owner is None:
            row.label(text=f'{title} · Not Used')
            continue
        row.prop_search(owner, field, profile.rig.data, 'bones', text=title)
        if side == 'target' and controller_fk:
            row.enabled = False
        else:
            op = row.operator('toyxyz_retarget.use_selected_bone', text='', icon='EYEDROPPER')
            op.side, op.role = side, role


CLASSES = (TRG_PG_MapRow, TRG_OT_build_bones_list, TRG_OT_import_bone_mapping, TRG_OT_export_bone_mapping,
           TRG_OT_use_selected_bone,
           TRG_OT_select_mapping_row, TRG_UL_bone_pairs)
