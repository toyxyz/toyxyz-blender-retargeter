"""Humanoid bone naming conventions used by Build Bones List."""

import re
from unicodedata import normalize


FINGERS = ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')
TOES = ('BigToe', 'SecondToe', 'MiddleToe', 'FourthToe', 'LittleToe')
ALIASES = {
    'Hips': ('hips', 'pelvis', 'hip', 'rootx', 'lowertorso', 'rootm', 'rootmasterx', 'root', 'spine'),
    'Head': ('head', 'skull', 'headx', 'headm'),
    'Shoulder': ('shoulder', 'clavicle', 'collar', 'scapula', 'shoulder1'),
    'Arm': ('upperarm', 'uparm', 'arm', 'humerus', 'armstretch', 'shldrbend', 'upperarm01', 'armfk', 'upperarmfk'),
    'ForeArm': ('forearm', 'lowerarm', 'lowarm', 'elbow', 'radius', 'forearmstretch', 'forearmbend',
                'lowerarm01', 'forearmfk'),
    'Hand': ('hand', 'wrist', 'handfk'),
    'Leg': ('upleg', 'upperleg', 'thigh', 'femur', 'thighstretch', 'thighbend', 'upperleg01', 'thighfk', 'leg'),
    'Shin': ('shin', 'lowerleg', 'lowleg', 'calf', 'knee', 'tibia', 'legstretch', 'lowerleg01', 'legfk', 'shinfk', 'leg'),
    'Foot': ('foot', 'ankle', 'footfk'),
    'ToeBase': ('toebase', 'toes', 'toe', 'ball', 'toe0', 'toes01', 'toesfk', 'toefk'),
}
SMPL = {'Shoulder': ('collar',), 'Arm': ('shoulder',), 'Leg': ('hip',), 'Foot': ('ankle',),
        'Hand': ('wrist',), 'ToeBase': ('foot',)}
ADVANCED = {'Shoulder': ('scapula',), 'Arm': ('shoulder',), 'Leg': ('hip',)}
BVH_COLLAR = {'Shoulder': ('collar',), 'Arm': ('shoulder',)}
RIGIFY = {'Hips': ('spine',), 'Head': ('spine006',)}
SOMA = {'Leg': ('leg',), 'Shin': ('shin',)}
MMD_LEG = {
    'Leg': ('足', 'leg'), 'Shin': ('ひざ', '膝', 'knee'), 'Foot': ('足首', 'ankle'),
    'ToeBase': ('つま先', '爪先', 'toe', 'toetip'),
}
MMD_D_LEG = {
    'Leg': ('足d', 'legd'), 'Shin': ('ひざd', '膝d', 'kneed', 'shind'),
    'Foot': ('足首d', 'ankled', 'footd'), 'ToeBase': ('足先ex', 'toeex', 'toeextra', 'toetipex'),
}
MMD = {
    'Hips': ('下半身', 'lowerbody', 'センター', 'center', '腰', 'waist', 'グルーブ', 'groove',
             '全ての親', 'すべての親', 'parentnode', 'motherbone', 'master'),
    'Head': ('頭',),
    'Shoulder': ('肩', '鎖骨'),
    'Arm': ('腕', 'うで', 'arm'),
    'ForeArm': ('ひじ', '肘', 'elbow'),
    'Hand': ('手首', '手', 'wrist'),
    **{part: (*aliases, *MMD_D_LEG[part]) for part, aliases in MMD_LEG.items()},
}
MMD_FINGERS = {
    'Thumb': ('親指',), 'Index': ('人指', '人差指', '人差し指'), 'Middle': ('中指',),
    'Ring': ('薬指',), 'Pinky': ('小指',),
}
PROFILES = (
    ({'下半身', '頭'}, MMD),
    ({'上半身', '頭'}, MMD),
    ({'lowerbody', 'upperbody'}, MMD),
    ({'leftleg', 'leftshin', 'lefthandindex4', 'chest', 'neck2'}, SOMA),
    ({'rootm', 'headm', 'scapulal', 'shoulderl'}, ADVANCED),
    ({'pelvis', 'leftcollar', 'leftshoulder', 'lefthip', 'leftankle', 'leftwrist'}, SMPL),
    ({'leftcollar', 'leftshoulder', 'leftelbow', 'leftwrist', 'leftupleg'}, BVH_COLLAR),
    ({'spine', 'spine004', 'spine005', 'spine006', 'upperarml', 'thighl'}, RIGIFY),
)
PREFIX = re.compile(r'^(def|deform|org|mch|ctrl|control|c|mixamorig\d*|bip\d*)[ _.-]+')
VENDOR_PREFIX = re.compile(r'^(?:valvebiped\.|cc_base_|heat_|j_bip_(?:c_)?|bip\d+[ _.-]+)')
AXIAL = re.compile(r'^(?:spine|chest|upperchest|torso|uppertorso|back|thorax|abdomen|rootpart|waist)'
                   r'(?:lower|upper|part)?\d*(?:part\d+)?[mx]?$')
NECK = re.compile(r'^(?:neck|cervical)(?:lower|upper|part|twist)?\d*(?:part\d+)?[mx]?$')
DIGIT_ALIASES = {'Pinky': ('little', 'small'), 'BigToe': ('hallux', 'greattoe'), 'SecondToe': ('indextoe',),
                 'MiddleToe': ('midtoe',), 'FourthToe': ('ringtoe',), 'LittleToe': ('smalltoe', 'pinkytoe')}


def name_key(name):
    local = normalize('NFKC', name).rsplit(':', 1)[-1].strip().lower()
    while prefix := VENDOR_PREFIX.match(local):
        local = local[prefix.end():]
    prefix = PREFIX.match(local)
    penalty = 0
    if prefix:
        penalty = {'def': -1, 'deform': -1, 'org': 4, 'mch': 8, 'ctrl': 3, 'c': 2}.get(prefix[1], 0)
        local = local[prefix.end():]
    local = re.sub(r'[ _.-]+(?:jnt|joint)$', '', local)
    side = {'左': 'left', '右': 'right'}.get(local[:1])
    if side:
        local = side + local[1:]
    return re.sub(r'[^a-z0-9\u3040-\u30ff\u3400-\u9fff]', '', local), penalty


def bone_keys(pose_bone):
    key, penalty = name_key(pose_bone.name)
    yield key, penalty
    if penalty >= 8:
        return
    metadata = pose_bone.get('mmd_bone')
    if not hasattr(metadata, 'get'):
        metadata = {}
    mmd = getattr(pose_bone, 'mmd_bone', None)
    japanese = (getattr(mmd, 'name_j', '') or metadata.get('name_j') or
                pose_bone.get('mmd_bone_name_j') or pose_bone.get('name_j'))
    english = (getattr(mmd, 'name_e', '') or metadata.get('name_e') or
               pose_bone.get('mmd_bone_name_e') or pose_bone.get('name_e'))
    seen = {key}
    for name in (japanese, english):
        if isinstance(name, str):
            alias, _ = name_key(name)
            if alias and alias not in seen:
                yield alias, penalty + 1
                seen.add(alias)


def side_keys(side, alias):
    return (side + alias, alias + side, side[0] + alias, alias + side[0]) if side else (alias,)


def digit_aliases(part, keys, side, convention):
    digit, segment = part[:-1], int(part[-1])
    bases = (digit.lower(), *DIGIT_ALIASES.get(digit, ()))
    number = segment
    if convention is MMD and digit == 'Thumb':
        zero_keys = (key for alias in ('親指0', 'thumb0', 'thumb00', 'thumbfinger0') for key in side_keys(side, alias))
        if any(key in keys for key in zero_keys):
            number -= 1
    numbers = (str(number), f'{number:02}')
    if digit in FINGERS:
        index = FINGERS.index(digit)
        names = [f'{base}{number}' for base in MMD_FINGERS[digit] for number in numbers] if convention is MMD else []
        names.extend(f'{prefix}{base}{suffix}{number}' for base in bases for number in numbers
                     for prefix, suffix in (('', ''), ('hand', ''), ('f', ''), ('finger', ''), ('', 'finger')))
        if segment <= 3:
            phases = ('proximal', 'intermediate', 'distal')
            if digit == 'Thumb' and keys.keys() & {'leftthumbmetacarpal', 'rightthumbmetacarpal'}:
                phases = ('metacarpal', 'proximal', 'distal')
            phase = phases[segment - 1]
            names.extend(base + phase for base in bases)
            if digit == 'Thumb':
                names.append(f'finger1{("metacarpal", "proximal", "distal")[segment - 1]}')
                if segment == 1:
                    names.append('thumbmetacarpal')
            else:
                names.append(f'finger{index + 1}{phase}')
                if segment == 2:
                    names.append(f'finger{index + 1}medial')
            if keys.keys() & {'lfinger0', 'rfinger0', 'finger0l', 'finger0r'}:
                suffix = str(segment - 1) if segment > 1 else ''
                names.append(f'finger{index}{suffix}')
            else:
                names.append(f'finger{index + 1}{segment}')
            if keys.keys() & {'lefthandindexstart', 'righthandindexstart'}:
                names.insert(0, f'hand{bases[0]}{("start", "1", "2")[segment - 1]}')
        return tuple(names)

    names = [f'{prefix}{base}{number}' for base in bases for number in numbers for prefix in ('', 'foot')]
    daz = 'bigtoe' if digit == 'BigToe' else f'smalltoe{TOES.index(digit)}'
    if segment <= 2:
        names.append(daz + ('2' if segment == 2 else ''))
    return tuple(names)


def axial_kind(key, convention):
    if convention is MMD:
        if re.fullmatch(r'(?:上半身|upperbody)\d*', key):
            return 'spine'
        if re.fullmatch(r'首\d*', key):
            return 'neck'
        if key in {'腰', 'waist'}:
            return None
    if NECK.fullmatch(key) or (convention is RIGIFY and key in {'spine004', 'spine005'}):
        return 'neck'
    if AXIAL.fullmatch(key):
        return 'spine'
    return None
