"""Persist explicit humanoid role maps and propose matches from bone names."""

import re
from functools import lru_cache
from itertools import product

from . import arp_fk
from .naming import ALIASES, FINGERS, MMD, MMD_D_LEG, MMD_LEG, PROFILES, TOES, axial_kind, bone_keys, digit_aliases, name_key, side_keys


SIDES = ('Left', 'Right')
LIMB_PARTS = ('Shoulder', 'Arm', 'ForeArm', 'Hand', 'Leg', 'Shin', 'Foot', 'ToeBase')
ARM_PARTS, LEG_PARTS = LIMB_PARTS[:4], LIMB_PARTS[4:]
ROLES = ('Hips', 'Head', *(side + part for side in SIDES for part in LIMB_PARTS))
OPTIONAL = {side + part for side in SIDES for part in ('Shoulder', 'ToeBase')}
FINGER_CHAINS = tuple((side + 'Hand', tuple(f'{side}{digit}{i}' for i in range(1, 5)))
                      for side in SIDES for digit in FINGERS)
TOE_CHAINS = tuple((side + 'ToeBase', tuple(f'{side}{digit}{i}' for i in range(1, 4)))
                   for side in SIDES for digit in TOES)
DIGIT_CHAINS = (*FINGER_CHAINS, *TOE_CHAINS)
DIGIT_ROLES = tuple(role for _, chain in DIGIT_CHAINS for role in chain)
ALL_FIXED_ROLES = (*ROLES, *DIGIT_ROLES)
LABELS = {
    'Hips': 'Hips', 'Head': 'Head', 'Shoulder': 'Clavicle', 'Arm': 'Upper Arm',
    'ForeArm': 'Forearm', 'Hand': 'Hand', 'Leg': 'Thigh', 'Shin': 'Shin',
    'Foot': 'Foot', 'ToeBase': 'Toe Base',
}


def role_property(role):
    return 'bone_' + role.lower()


def role_label(role):
    for side in SIDES:
        if role.startswith(side):
            part = role[len(side):]
            label = LABELS.get(part)
            if label is None:
                label = re.sub(r'(?<=[a-z])(?=[A-Z0-9])', ' ', part)
            return f'{side} {label}'
    if role.startswith(('Spine', 'Neck')):
        return re.sub(r'(?<=\D)(?=\d)', ' ', role)
    return LABELS[role]


def role_field(profile, role):
    for chain in ('Spine', 'Neck'):
        if role.startswith(chain):
            index = int(role[len(chain):]) - 1
            bones = getattr(profile, chain.lower() + '_bones')
            return (bones[index], 'bone') if 0 <= index < len(bones) else (None, None)
    return profile, role_property(role)


def bone_for_role(profile, role):
    owner, key = role_field(profile, role)
    return getattr(owner, key) if owner is not None else ''


def body_rows(source, target):
    return (
        'Hips', *(f'Spine{i}' for i in range(1, max(source.spine_count, target.spine_count) + 1)),
        *(f'Neck{i}' for i in range(1, max(source.neck_count, target.neck_count) + 1)),
        'Head', *(role for role in ROLES[2:] if not role.endswith('ToeBase')),
    )


def resize(items, count):
    while len(items) > count:
        items.remove(len(items) - 1)
    while len(items) < count:
        items.add()


def ancestry(bones, ancestor, child):
    node = bones.get(child)
    chain = []
    while node is not None and node.name != ancestor:
        chain.append(node)
        node = node.parent
    return list(reversed(chain)) if node is not None else []


def reset_mapping(profile):
    for role in ALL_FIXED_ROLES:
        setattr(profile, role_property(role), '')
    profile.spine_bones.clear()
    profile.neck_bones.clear()
    profile.spine_count = 1
    profile.neck_count = 0


def repair_legacy_leg_mapping(profile, bones):
    # A saved SOMA thigh name can be the shin on an imported Mixamo rig.
    for side in SIDES:
        thigh_role, shin_role = side + 'Leg', side + 'Shin'
        thigh = bones.get(bone_for_role(profile, thigh_role))
        if thigh is None or bone_for_role(profile, shin_role) in bones:
            continue
        keys = side_keys(side.lower(), 'leg')
        if name_key(thigh.name)[0] not in keys:
            continue
        upper_keys = {key for alias in ALIASES['Leg'] if alias != 'leg' for key in side_keys(side.lower(), alias)}
        parent = thigh.parent
        while parent is not None:
            if name_key(parent.name)[0] in upper_keys:
                setattr(profile, role_property(thigh_role), parent.name)
                setattr(profile, role_property(shin_role), thigh.name)
                break
            parent = parent.parent


def mmd_skinned_legs(rig, index):
    """Choose a complete D chain when it carries the skin and the ordinary chain does not."""
    bones = rig.data.bones

    def names(side, aliases):
        return tuple(dict.fromkeys(name for alias in aliases for key in side_keys(side, alias)
                                   for _, _, name in index.get(key, ())))

    options = {}
    for side in SIDES:
        suffix = side.lower()
        d = {part: names(suffix, MMD_D_LEG[part]) for part in LEG_PARTS}
        chains = [(leg, shin, foot) for leg, shin, foot in product(*(d[part] for part in LEG_PARTS[:3]))
                  if ancestry(bones, leg, shin) and ancestry(bones, shin, foot)]
        if not chains:
            continue
        normal = {part: names(suffix, (*MMD_LEG[part], *ALIASES[part])) for part in LEG_PARTS}
        options[side] = normal, d, chains
    if not options:
        return {}

    candidates = {name for normal, d, _ in options.values() for branch in (normal, d)
                  for group in branch.values() for name in group if bones[name].use_deform}
    meshes = {obj for scene in rig.users_scene for obj in scene.objects if obj.type == 'MESH'
              and any(mod.type == 'ARMATURE' and mod.object == rig and mod.use_vertex_groups for mod in obj.modifiers)}
    skinned = set()
    for obj in meshes:
        remaining = candidates - skinned
        groups = {group.index: group.name for group in obj.vertex_groups if group.name in remaining}
        if not groups:
            continue
        for vertex in obj.data.vertices:
            for group in vertex.groups:
                if group.group in groups and group.weight > 1e-5:
                    skinned.add(groups.pop(group.group))
            if not groups:
                break

    result = {}
    for side, (normal, d, chains) in options.items():
        if any(skinned.intersection(normal[part]) for part in LEG_PARTS):
            continue
        for leg, shin, foot in chains:
            toe = next((name for name in d['ToeBase'] if ancestry(bones, foot, name)), '')
            if skinned.intersection((leg, shin, foot, toe)):
                result[side] = normal, dict(zip(LEG_PARTS, (leg, shin, foot, toe)))
                break
    return result


def build_mapping(profile, *, controller_fk=False):
    if controller_fk:
        fixed, spine, neck = arp_fk.layout(profile.rig)
        fixed.update(arp_fk.finger_layout(profile.rig))
        for role in ALL_FIXED_ROLES:
            setattr(profile, role_property(role), fixed.get(role, ''))
        profile.mapping_rig = profile.rig
        set_chains(profile, spine, neck)
        return
    bones = profile.rig.data.bones
    repair_legacy_leg_mapping(profile, bones)
    names = {bone.name: tuple(bone_keys(bone)) for bone in profile.rig.pose.bones}
    index = {}
    for bone in bones:
        for key, penalty in names[bone.name]:
            if key and penalty < 8:
                index.setdefault(key, []).append((penalty, not bone.use_deform, bone.name))
    convention = next((aliases for signature, aliases in PROFILES if signature <= index.keys()), {})
    fixed = {role: bone_for_role(profile, role) for role in ALL_FIXED_ROLES
             if bone_for_role(profile, role) in bones}
    preferred = {}
    if convention is MMD:
        for side, (normal, alternate) in mmd_skinned_legs(profile.rig, index).items():
            # Earlier versions filled the ordinary chain even when none of it skinned the mesh.
            if (all(fixed.get(side + part) in normal[part] for part in LEG_PARTS[:3]) and
                    fixed.get(side + 'ToeBase', '') in ('', *normal['ToeBase'])):
                for part in LEG_PARTS:
                    fixed.pop(side + part, None)
            preferred.update((side + part, name) for part, name in alternate.items() if name)
    used = set(fixed.values())

    @lru_cache(maxsize=None)
    def candidates(role):
        if role in fixed:
            return (fixed[role],)
        side = next((side for side in SIDES if role.startswith(side)), '')
        part = role[len(side):]
        aliases = ((*convention.get(part, ()), *ALIASES[part]) if part in ALIASES else
                   digit_aliases(part, index, side.lower(), convention))
        scores = [(rank * 12 + penalty, non_deform, name) for rank, alias in enumerate(dict.fromkeys(aliases))
                  for key in side_keys(side.lower(), alias) for penalty, non_deform, name in index.get(key, ())]
        ranked = [name for _, _, name in sorted(scores)]
        if role in preferred:
            ranked.insert(0, preferred[role])
        return tuple(dict.fromkeys(name for name in ranked if name not in used))

    @lru_cache(maxsize=None)
    def below(parent, name):
        return bool(ancestry(bones, parent, name))

    def chain(roles, parent, reserved):
        @lru_cache(maxsize=None)
        def search(i, previous):
            if i == len(roles):
                return (), (0, 0)
            role = roles[i]
            best, score = (), (-1, -1)
            for name in (*candidates(role), ''):
                if name and ((name in reserved and role not in fixed) or not below(previous, name)):
                    continue
                tail, (main, total) = search(i + 1, name or previous)
                rank = main + bool(name and role in ROLES and role not in OPTIONAL), total + bool(name)
                if rank > score:
                    best, score = (name, *tail), rank
                if rank[1] == len(roles) - i:
                    break
            return best, score

        return dict(zip(roles, search(0, parent)[0]))

    saved_spine = {item.bone for item in profile.spine_bones if item.bone}
    saved_neck = {item.bone for item in profile.neck_bones if item.bone}
    best, spine, neck, rank = {}, [], [], (-1, -1, -1)
    for hips in candidates('Hips'):
        for head in candidates('Head'):
            axial = {'spine': [], 'neck': []}
            for bone in ancestry(bones, hips, head)[:-1]:
                name = bone.name
                kind = ('spine' if name in saved_spine else 'neck' if name in saved_neck else
                        next((kind for key, _ in names[name] if (kind := axial_kind(key, convention))), None))
                if kind:
                    axial[kind].append(name)
            if not axial['spine']:
                continue
            mapped = {'Hips': hips, 'Head': head}
            reserved = used | {hips, head} | set(axial['spine']) | set(axial['neck'])
            for side in SIDES:
                for parts, parent in ((ARM_PARTS, axial['spine'][0]), (LEG_PARTS, hips)):
                    limbs = chain(tuple(side + part for part in parts), parent, reserved)
                    mapped.update(limbs)
                    reserved.update(name for name in limbs.values() if name)
            # Select complete chains before falling back to an incomplete name match.
            required = sum(bool(name) for role, name in mapped.items() if role not in OPTIONAL)
            candidate_rank = required, sum(bool(name) for name in mapped.values()), len(axial['spine'])
            if candidate_rank > rank:
                best, spine, neck, rank = mapped, axial['spine'], axial['neck'], candidate_rank
            if required == len(ROLES) - len(OPTIONAL):
                break
        if rank[0] == len(ROLES) - len(OPTIONAL):
            break

    if not best:
        for role in ROLES:
            best[role] = next((name for name in candidates(role) if name not in used), '')
            if best[role]:
                used.add(best[role])
    used.update(name for name in best.values() if name)
    used.update((*spine, *neck))
    for base, roles in DIGIT_CHAINS:
        parent = fixed.get(base) or best.get(base)
        foot = base.replace('ToeBase', 'Foot')
        foot_name = fixed.get(foot) or best.get(foot)
        parent = parent or foot_name
        if parent:
            digits = chain(roles, parent, used)
            if foot_name and foot_name != parent and not any(digits.values()):
                digits = chain(roles, foot_name, used)
            best.update(digits)
            used.update(name for name in digits.values() if name)
    for role in ALL_FIXED_ROLES:
        if role not in fixed:
            setattr(profile, role_property(role), best.get(role, ''))
    profile.mapping_rig = profile.rig
    if spine:
        set_chains(profile, spine, neck)


def set_chains(profile, spine, neck):
    profile.spine_count = min(12, max(1, len(spine)))
    profile.neck_count = min(12, len(neck))
    resize(profile.spine_bones, profile.spine_count)
    resize(profile.neck_bones, profile.neck_count)
    for items, names in ((profile.spine_bones, spine), (profile.neck_bones, neck)):
        for i, item in enumerate(items):
            item.bone = names[i] if i < len(names) else ''


def mapped_names(profile, *, controller_fk=False):
    fixed = {role: getattr(profile, role_property(role)) for role in ROLES}
    spine = [item.bone for item in profile.spine_bones][:profile.spine_count]
    neck = [item.bone for item in profile.neck_bones][:profile.neck_count]
    if controller_fk:
        if (fixed, spine, neck) != arp_fk.layout(profile.rig):
            raise ValueError('Build Bones List to set up the Auto-Rig Pro FK controllers.')
        return fixed, spine, neck
    required = [(role_label(role), bone) for role, bone in fixed.items() if role not in OPTIONAL]
    required.extend((f'Spine {i}', bone) for i, bone in enumerate(spine, 1))
    required.extend((f'Neck {i}', bone) for i, bone in enumerate(neck, 1))
    missing = [role for role, name in required if not name or name not in profile.rig.data.bones]
    if len(spine) != profile.spine_count or len(neck) != profile.neck_count:
        missing.append('Spine or Neck')
    if missing:
        raise ValueError('Assign valid bones for ' + ', '.join(missing) + '.')
    selected = [name for name in (*fixed.values(), *spine, *neck) if name]
    if len(set(selected)) != len(selected):
        raise ValueError('Each mapped bone must occupy a single role.')
    if any(name not in profile.rig.data.bones for name in selected):
        raise ValueError('A mapped bone no longer exists.')
    bones = profile.rig.data.bones
    axial = [fixed['Hips'], *spine, *neck, fixed['Head']]
    links = list(zip(axial, axial[1:]))
    for side in SIDES:
        arm = [spine[0], *(fixed[side + part] for part in ARM_PARTS)]
        leg = [fixed['Hips'], *(fixed[side + part] for part in ('Leg', 'Shin', 'Foot', 'ToeBase'))]
        for chain in (arm, leg):
            chain = [bone for bone in chain if bone]
            links.extend(zip(chain, chain[1:]))
    for parent, child in links:
        if not ancestry(bones, parent, child):
            raise ValueError(f'{child} must descend from {parent}. Check the mapping order.')
    return fixed, spine, neck


def paired_digits(source, target, *, controller_fk=False):
    if controller_fk:
        layout = arp_fk.finger_layout(target.rig)
        return {role for role, name in layout.items()
                if bone_for_role(target, role) == name and bone_for_role(source, role)}
    return {role for role in DIGIT_ROLES if bone_for_role(source, role) and bone_for_role(target, role)}


def digit_parent_roles(fixed, profile, paired, *, controller_fk=False):
    """Yield paired bone roles in parent-first order."""
    bones = profile.rig.data.bones
    for base, chain in DIGIT_CHAINS:
        parent = base if fixed[base] else base.replace('ToeBase', 'Foot')
        for role in chain:
            if role not in paired:
                continue
            name = bone_for_role(profile, role)
            parent_name = fixed[parent] if parent in fixed else bone_for_role(profile, parent)
            if controller_fk and parent.endswith('Hand'):
                # The finger controls inherit the driven hand; the wrist FK control is on a parallel branch.
                parent_name = f'hand.{parent[0].lower()}'
            if not ancestry(bones, parent_name, name):
                if not parent.endswith('ToeBase') or not ancestry(bones, fixed[parent.replace('ToeBase', 'Foot')], name):
                    raise ValueError(f'{name} must descend from {parent_name}. Check the digit mapping.')
                parent = parent.replace('ToeBase', 'Foot')
            yield role, parent, name
            parent = role
