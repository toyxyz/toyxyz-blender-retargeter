# Toyxyz Blender Retargeter

Toyxyz Blender Retargeter transfers evaluated humanoid motion between source and target armatures in the viewport. It supports Blender 5.0 and later and uses Blender's bundled NumPy.

## Quick start

1. Download the add-on ZIP from [GitHub Releases](https://github.com/toyxyz/toyxyz-blender-retargeter/releases/latest) and install it using **Preferences > Get Extensions > Install from Disk**.
2. In the 3D View sidebar, open **Retarget**, select an actor, and choose its source and target armatures in the current scene. To assign from the viewport, select an armature or one of its bones and click **Use Selected Rig** next to **Source** or **Target**.
3. Open **Bone Mapping** and click **Build Bones List**. This fills missing matches by name and refreshes the spine and neck counts for both rigs. **Main Bones** displays each rig's counts and uses the larger count for the shared rows of each chain. You can edit either rig's counts and choose bones from the pelvis toward the head.
4. Click **Connect**. Play or scrub the source animation, or pose source bones directly. **Disconnect** removes the temporary constraints and restores normal target evaluation.

## Multiple actors

Use **+** and **−** beside **Actors** to add or remove pairs. Each row stores its own bone mapping, reference poses, floor markers, and solver values. Several actors can read the same source. Each target can be assigned to one actor in the scene.

**Connect All** connects configured pairs, and **Disconnect All** disconnects them. Selecting a different actor changes the panel and leaves the other pairs connected. **Connect**, **Disconnect**, and **Bake to Action** below the list apply to the selected actor. Removing an actor disconnects its preview and keeps the source and target objects in the scene. Existing single-pair files open with their settings in **Actor 01**.

## Mapping and calibration

Use **Export** below **Build Bones List** to save the selected actor's source and target bone assignments to a JSON preset. The preset includes the ordered spine and neck chains, fingers, and toes. Select a disconnected actor and click **Import** to replace its bone mapping. The actor keeps its assigned rigs, reference poses, contact markers, and solver settings. Review any imported bone names that are missing on the assigned rigs before connecting.

Build Bones List fills missing matches independently for the source and target. It recognizes namespaces, side prefixes and suffixes, and common joint naming conventions. It checks parent chains before choosing between competing names and discovers the spine and neck from the path between the hips and head. Review the proposed matches. Clavicles, toe bases, and neck bones are optional. The core limb chains, hips, head, and at least one spine bone are required.

After replacing either armature, click **Build Bones List** to populate its new mapping. In a Mixamo-style leg chain, `LeftUpLeg` is the thigh and `LeftLeg` is the shin. In the Kimodo add-on's SOMA rig, `LeftLeg` is the thigh and `LeftShin` is the shin.

Mixamo's numbered finger bones are matched by name, including names such as `mixamorig:LeftHandIndex1`. On a standard Auto-Rig Pro target, **Build Bones List** also assigns the three FK phalanx controls per finger when present, such as `c_index1.l` through `c_index3.l`. Live retargeting and baking use the finger roles shared by both rigs. Rebuild an existing list to populate the newly supported Auto-Rig Pro slots.

| Naming family | Examples |
| --- | --- |
| Motion capture and FBX | Mixamo and Autodesk HumanIK-style names, Move One, Perception Neuron, Xsens, Rokoko, DeepMotion, HEAT, Sony mocopi |
| Character and game rigs | Auto-Rig Pro export skeleton and FK-style names, Rigify deform-style names, Advanced Skeleton-style names, Character Creator / ActorCore, DAZ Genesis, MB-Lab, 3ds Max Biped / ValveBiped |
| Runtime and model skeletons | Kimodo SOMA, Unity Humanoid, Unreal Mannequin / MetaHuman, VRM and VRoid, SMPL-X, Roblox R15-style names |
| MikuMikuDance | PMX and PMD armatures with Japanese names, Blender `.L`/`.R` suffixes, common English translations, or MMD Tools bone-name metadata |

Auto-Rig Pro export skeletons can use `root.x`, `thigh_stretch.l`, and `leg_stretch.l`. Limb matching checks the main anatomical chain and avoids a twist or IK control on a parallel branch. Control rigs can use different hierarchies and may need manual mapping or an exported deform skeleton. VRM support uses recognized node names.

Import the PMX or PMD file with [MMD Tools](https://extensions.blender.org/add-ons/mmd-tools/), then select the imported armature as Source or Target. Build Bones List checks the shared pelvis or center parent, recognizes the upper body and neck chain, and handles both zero- and one-based thumb numbering. When only the alternate `D` chain has skin weights, the list uses that chain and updates a previously mapped ordinary leg chain. MMD IK and physics remain under MMD Tools control. [MMD skeleton reference](https://noname0310.github.io/babylon-mmd/docs/reference/understanding-mmd-behaviour/introduction-to-pmx-and-pmd/#standard-bone-structure--semi-standard-bone-structure)

Naming references include [Auto-Rig Pro Remap](https://www.lucky3d.fr/auto-rig-pro/doc/remap_doc.html), [Autodesk HumanIK](https://help.autodesk.com/cloudhelp/2022/ENU/Maya-CharacterAnimation/files/GUID-5DEFC6E5-033C-45D5-9A0E-224E7A35131B.htm), [Kimodo SOMA exports](https://research.nvidia.com/labs/sil/projects/kimodo/docs/user_guide/output_formats.html), and [VRM humanoid](https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_vrm-1.0/humanoid.md).

Select a row in **Main Bones**, **Fingers**, or **Toes** to edit it in the shared **Source** and **Target** fields below **Build Bones List**. To fill a field from the viewport, select a bone on the corresponding armature in Pose Mode or Edit Mode and click its eyedropper. Bone search remains available in the same fields. **Bone Mapping** folds the entire section, and each list folds independently. Digit rotations are transferred when both rigs have a bone assigned to the same role.

Open **Reference Pose** to set a calibration pose for each rig. **Edit Reference Pose** opens a temporary armature in Pose Mode. Edit it and choose **Save**. **Use Current Pose** captures the evaluated pose at the current frame. **Use Rig Rest** removes the saved calibration for that rig. Stored references are separate from Blender's armature rest data and Actions. Start from comparable poses for the two characters and align the limbs where their baseline A or T poses differ.

You can edit the source or target reference while connected. The temporary armature appears beside the rig and the connected target follows the draft pose as you edit. **Reset** restores the starting reference and keeps the editor open. **Save** keeps the new calibration. **Cancel** discards the edits and closes the editor. Undo and Redo apply to changes made within the reference editor.

### Auto-Rig Pro FK target

Select a standard, generated Auto-Rig Pro humanoid as the target, then click **Build Bones List** and **Connect**. The panel shows **Target: Auto-Rig Pro FK**. Toyxyz uses the rig's spine and neck controls and drives the pelvis, torso, head, arms, and legs through the FK controllers. The rig's existing constraints and drivers deform the character. The connection temporarily sets all four limbs to FK and restores the previous switch behavior on disconnect.

**Bake to Action** writes controller rotations, pelvis translation, and four FK switch properties. The Action plays through the rig's native constraints, and its FK controllers remain editable in Pose Mode. Verification covers the standard body and paired finger controls. Custom ARP modules require separate validation.

## Solver options

The Toyxyz solver drives live connections and Bake to Action. Each frame can be evaluated independently. **Solver Options** has four collapsible sections.

**Solver Strength** blends the actor's direct FK pose at 0% with the configured solver pose at 100%. Direct FK transfers the mapped rotations and the source root displacement at its original scale. Intermediate values blend the root position and joint rotations while preserving bone lengths. The blend includes the effects of Root Travel, Reach, Knee settings, and Floor Contact. At intermediate strengths, floor clearance can differ from the fully solved pose. The same bone mapping and reference poses drive both endpoints.

| Section | Options |
| --- | --- |
| Hands / Arms | Position Reach defaults to 0%. It follows the source wrists relative to the upper spine. Enable Calibrated Wrist Reach to follow source wrist travel in the target's action space, with a reference-pose bend direction for the elbows. Arm length limits reach in both modes. Rotation Reach defaults to 100%. |
| Feet / Legs | Position and Rotation Reach default to 100%. Enable Staged Ankle Reach to guide knee flexion with a reference-pose hinge as the leg follows the source ankle. Floor Contact uses heel and forefoot points. Feet Contact Stiffness preserves ankle rotation during floor correction. At 100%, floor correction affects position only. Source and Target Floor define each contact plane and offset. |
| Knee | Extension Damping (70%) eases into maximum reach using the FK bend as a reference. Bend Preservation (60%) follows the source leg proportions more closely in the air and helps ease floor correction. Setting both to 0% restores the previous knee and floor transition behavior. |
| Root | Root Travel scales horizontal travel to target proportions, uses source distance, or removes horizontal travel. Travel Multiplier scales that travel, and Hip Height Offset moves the target root vertically in metres. |

Both new Reach options are off by default. **Separate Left / Right** lets you tune Position and Rotation Reach independently for each side. The first split copies the shared settings. Returning to shared control keeps the individual settings available for later use. Reach uses the current frame for both the live connection and the action bake. Hand Reach targets the wrist joint. Floor Contact remains a separate correction for the feet.

Under **Solver Options → Feet / Legs**, choose **Edit Contact Markers** to adjust the target's heel and ball points. Select a point in the viewport and use Blender's Move tool. While connected, **Floor Contact** uses the draft points immediately. **Save** keeps the offsets with the target's mapped ankle bones. **Reset** restores the automatic points and keeps the editor open. **Cancel** restores the last saved points. To return to automatic points permanently, use **Reset**, then **Save**.

This preview drives the mapped target bones through temporary constraints. Existing target Action and NLA data stay attached. Undo and Redo reuse the preview links restored by Blender. Saving while connected records the connection and mutes the preview in the saved file. The add-on restores the preview when you reopen the scene. Save after **Disconnect** to open the file disconnected.

## Bake to Action

Use **Bake All** beside **Bake to Action** to bake all connected actors in the current scene. Each actor gets its own Action over the source Action's keyed range, with the scene range used when the source has no directly keyed Action or uses NLA. Actors that read another connected target use the original upstream motion's range. The dialog shares the sampling, interpolation, and constraint options across the batch. Successful actors disconnect automatically. Any failed actor stays connected and reports its error in the panel.

Connect the rigs and click **Bake to Action**. The dialog defaults to the source Action's keyed range when available and uses the scene range otherwise. Set the Action name, start and end frames, sample step, interpolation, and Fake User. Bezier interpolation also supports handle selection. **Existing Source Keys** writes the active source Action's key times and the selected endpoints. This option uses direct Action playback. Use a sample step of one to record changes between source keys.

The bake records the evaluated target pose into a new Action and verifies playback at the written frames. On success it activates the Action and disconnects the rigs. The previous target Action receives a Fake User. Existing NLA tracks are preserved, and NLA playback is turned off on the target when needed to isolate the new Action. For rigs with active bone constraints, **Mute Target Bone Constraints** allows the evaluated pose to play from the new keys with those constraints muted. If drivers or other overrides change the result at a recorded frame, the bake restores the previous Action, NLA state, and preview connection.

This version targets humanoid armatures with positive uniform object scale and mapped anatomical chains. The standard Auto-Rig Pro FK target uses its own controller layout.

## Build from source

Run Blender's extension build command from the repository root.

```sh
blender --command extension build --source-dir .
```

The `_core` package contains pose math, reach geometry, and arm and leg solvers. `retarget.py` converts Blender rigs into calibrated skeletons and combines the solver results. `runtime.py` manages live connections, and `bake.py` writes Actions.

## License

Released under [GPL-3.0-or-later](LICENSE).
