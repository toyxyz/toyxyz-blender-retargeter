"""Drive the standard Auto-Rig Pro humanoid through its FK controls."""

import re

from .naming import FINGERS


PARTS = {'Shoulder': 'c_shoulder', 'Arm': 'c_arm_fk', 'ForeArm': 'c_forearm_fk', 'Hand': 'c_hand_fk',
         'Leg': 'c_thigh_fk', 'Shin': 'c_leg_fk', 'Foot': 'c_foot_fk', 'ToeBase': 'c_toes_fk'}
BODY = {'Hips': 'c_root_master.x', 'Head': 'c_head.x'} | {
    side + role: f'{name}.{side[0].lower()}' for side in ('Left', 'Right') for role, name in PARTS.items()
}
SWITCHES = tuple(f'c_{part}_ik.{side}' for part in ('hand', 'foot') for side in ('l', 'r'))
SWITCH = 'ik_fk_switch'
SNAPSHOT = '_toyxyz_fk_switches'
TARGET = '_toyxyz_fk_rig'
VALUE = '_toyxyz_fk_value'


def layout(rig):
    if rig is None or not rig.data.get('has_match_to_rig'):
        return None
    bones = rig.pose.bones
    if 'c_traj' not in bones or any(name not in bones for role, name in BODY.items() if not role.endswith('ToeBase')):
        return None
    if any(name not in bones or SWITCH not in bones[name] for name in SWITCHES):
        return None
    spine = sorted(name for name in bones.keys() if re.fullmatch(r'c_spine_\d\d\.x', name))
    neck = sorted((name for name in bones.keys() if re.fullmatch(r'c_subneck_\d+\.x', name)),
                  key=lambda name: int(name.rsplit('_', 1)[1].split('.')[0]))
    if not spine or 'c_neck.x' not in bones:
        return None
    neck.append('c_neck.x')
    return {role: name if name in bones else '' for role, name in BODY.items()}, spine, neck


def finger_layout(rig):
    """Map only ARP's user-facing phalanx controls."""
    bones = rig.pose.bones
    result = {}
    for side in ('Left', 'Right'):
        suffix = side[0].lower()
        if f'hand.{suffix}' not in bones:
            continue
        for digit in FINGERS:
            for segment in range(1, 4):
                name = f'c_{digit.lower()}{segment}.{suffix}'
                if name in bones:
                    result[f'{side}{digit}{segment}'] = name
    return result


def switch_path(bone):
    return bone.path_from_id() + f'["{SWITCH}"]'


def preview_drivers(rig):
    if not rig.animation_data:
        return ()
    return tuple(curve for curve in rig.animation_data.drivers
                 if curve.data_path.endswith(f'["{SWITCH}"]') and any(
                     variable.name == 'toyxyz_fk' and variable.targets[0].data_path == f'["{VALUE}"]'
                     for variable in curve.driver.variables))


def force_fk(rig, helper):
    paths = {name: switch_path(rig.pose.bones[name]) for name in SWITCHES}
    drivers = rig.animation_data.drivers if rig.animation_data else ()
    if any(curve.data_path in paths.values() for curve in drivers):
        raise ValueError('An Auto-Rig Pro IK/FK switch already has a driver. Remove that driver before connecting.')
    if SNAPSHOT not in helper:
        helper[SNAPSHOT] = {name: float(rig.pose.bones[name][SWITCH]) for name in SWITCHES}
        helper[TARGET] = rig
        helper[VALUE] = 1.
    for path in paths.values():
        driver = rig.driver_add(path).driver
        # Blender can evaluate a rebuilt driver's hidden helper input as zero after undo.
        # The helper variable identifies the owner so release only removes our drivers.
        driver.type = 'SCRIPTED'
        driver.expression = '1.0'
        variable = driver.variables.new()
        variable.name = 'toyxyz_fk'
        variable.type = 'SINGLE_PROP'
        variable.targets[0].id = helper
        variable.targets[0].data_path = f'["{VALUE}"]'
    rig.update_tag(refresh={'OBJECT'})


def release_fk(helper):
    rig = helper.get(TARGET)
    if rig is None or not rig.animation_data:
        return
    drivers = rig.animation_data.drivers
    for name, value in helper[SNAPSHOT].items():
        bone = rig.pose.bones.get(name)
        if bone is None:
            continue
        path = switch_path(bone)
        curve = drivers.find(path)
        if curve is not None and any(v.name == 'toyxyz_fk' and v.targets[0].id == helper
                                     for v in curve.driver.variables):
            rig.driver_remove(path)
            bone[SWITCH] = value
    rig.update_tag(refresh={'OBJECT'})
