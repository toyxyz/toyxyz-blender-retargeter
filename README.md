# Toyxyz Blender Retargeter

Transfer humanoid animation between armatures in Blender 5.0 or later. Preview the motion live, adjust the retargeting, and bake the result to a Blender Action.

[Download the latest release](https://github.com/toyxyz/toyxyz-blender-retargeter/releases/latest) · [Read the user guide](docs/USER_GUIDE.md)

## Quick start

1. Download the add-on ZIP and install it from **Preferences → Get Extensions → Install from Disk**.
2. Open the **Retarget** tab in the 3D Viewport sidebar. Choose the **Source** and **Target** armatures.
3. Click **Bone Mapping → Build Bones List**, review the assignments, and click **Connect**.
4. Preview the motion and click **Bake to Action** when you are ready to edit the result.

## Features

- Live retargeting for multiple rig pairs, with per-actor settings
- Automatic bone matching, manual mapping, and reusable JSON presets
- Editable reference poses, limb reach, root travel, and foot contact
- Single-actor and batch baking to Blender Actions

## Build from source

Run `blender --command extension build --source-dir .` from the repository root.

[GPL-3.0-or-later](LICENSE)
