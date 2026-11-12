"""Resolve each scene's actor rows to their independently saved settings."""

from uuid import uuid4

import bpy
from bpy.app.handlers import persistent


LEGACY = 'legacy'


def selected_key(scene):
    rows, index = scene.toyxyz_retarget_actors, scene.toyxyz_retarget_actor_index
    if 0 <= index < len(rows):
        return rows[index].key
    return None if scene.toyxyz_retarget_actors_initialized else LEGACY


def settings_for(scene, key=None):
    key = selected_key(scene) if key is None else key
    if key == LEGACY and not scene.toyxyz_retarget_actors_initialized:
        return scene.toyxyz_retarget
    for row in scene.toyxyz_retarget_actors:
        if row.key == key:
            return scene.toyxyz_retarget if key == LEGACY else row.settings
    return None


def entries(scene):
    if not scene.toyxyz_retarget_actors_initialized:
        yield LEGACY, scene.toyxyz_retarget
    else:
        for row in scene.toyxyz_retarget_actors:
            yield row.key, scene.toyxyz_retarget if row.key == LEGACY else row.settings


def owner_of(profile):
    scene = profile.id_data
    for key, settings in entries(scene):
        if profile == settings.source:
            return key, settings, 'source'
        if profile == settings.target:
            return key, settings, 'target'
    return None, None, None


def duplicate_target(scene, key, rig):
    return rig is not None and any(other != key and settings.target.rig == rig for other, settings in entries(scene))


def append(scene, *, legacy=False):
    rows = scene.toyxyz_retarget_actors
    existing = {row.name for row in rows}
    number = next(i for i in range(1, len(rows) + 2) if f'Actor {i:02d}' not in existing)
    row = rows.add()
    row.key = LEGACY if legacy else uuid4().hex
    row.name = f'Actor {number:02d}'
    scene.toyxyz_retarget_actors_initialized = True
    scene.toyxyz_retarget_actor_index = len(rows) - 1
    return row


@persistent
def ensure_existing(_=None):
    for scene in bpy.data.scenes:
        if scene.library is None and not scene.toyxyz_retarget_actors_initialized:
            append(scene, legacy=True)
