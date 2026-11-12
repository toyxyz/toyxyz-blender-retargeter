# Toyxyz Blender Retargeter User Guide

This guide covers live retargeting between humanoid rigs in Blender 5.0 or later and baking the result to an editable Action.

- [Install and connect a pair](#install-and-connect-a-pair)
- [Map bones](#map-bones)
- [Set reference poses](#set-reference-poses)
- [Tune the solver](#tune-the-solver)
- [Edit foot contact markers](#edit-foot-contact-markers)
- [Work with multiple actors](#work-with-multiple-actors)
- [Bake to an Action](#bake-to-an-action)
- [Rig-specific setup](#rig-specific-setup)
- [Troubleshooting](#troubleshooting)

## Install and connect a pair

1. Download the ZIP listed under **Assets** and install it through **Edit → Preferences → Get Extensions → Install from Disk**. [GitHub Releases](https://github.com/toyxyz/toyxyz-blender-retargeter/releases/latest)
2. Bring both armatures into the same Blender scene. The source can play an Action or NLA tracks, or you can pose it directly. Use **Pose Position** on both armatures and positive, uniform object scale. Use a local target or an editable library override.
3. In the 3D Viewport, press **N** to open the sidebar and select **Retarget**. Choose an actor in **Actors**. Use **+** to add one if the list is empty.
4. Set **Source** to the armature providing the motion and **Target** to the armature receiving it. You can also select a rig or one of its bones in the viewport and click the eyedropper beside the appropriate field.
5. Expand **Bone Mapping**, click **Build Bones List**, and review the bone assignments. In Object Mode or Pose Mode, click **Connect**.
6. Play or scrub the timeline. Adjust the selected actor in **Solver Options** or set a [reference pose](#set-reference-poses) to correct alignment. **Disconnect** removes the live preview and restores normal target evaluation.

The live preview drives the target through temporary constraints. Saving while connected records the connection so the add-on can restore it when the file opens. To open the file disconnected next time, disconnect the actor before saving.

## Map bones

**Build Bones List** checks the source and target independently. It fills missing assignments using common bone names, namespaces, side suffixes, and the parent hierarchy. It also detects the spine and neck chains. Review both columns after building the list, especially for rigs with deform bones, twist bones, and controllers close together.

### Main bones, spine, and neck

- Map the hips, head, and at least one spine bone. Each arm needs an upper arm, forearm, and hand. Each leg needs a thigh, shin, and foot. Clavicles, neck bones, and toe bases are optional.
- Set **Spine Bones** and **Neck Bones** for each rig in **Main Bones**. Enter each chain from the hips toward the head. When counts differ, the rows accommodate the longer chain, and motion is distributed along the target chain by length.
- Each bone should occupy one role on its rig. Use the anatomical parent chain so that the mapped shin descends from the mapped thigh, for example. Intermediate helper bones can sit between the mapped joints.
- The same bone name can mean different things on different rigs. In Mixamo, `LeftUpLeg` is the thigh and `LeftLeg` is the shin. In the SOMA rig used by Kimodo, `LeftLeg` is the thigh and `LeftShin` is the shin.

### Edit an assignment

1. Disconnect the actor, then open **Main Bones**, **Fingers**, or **Toes**.
2. Select the row you want to edit. The shared **Source** and **Target** fields below **Build Bones List** show its assignments.
3. Search for the bone by name or select it on the correct armature in Pose Mode or Edit Mode and click the row's corresponding eyedropper.
4. Reconnect and preview the motion. Finger and toe rotations transfer for roles mapped on both rigs. If a finger or toe does not move, first check that both rigs have that role assigned and that the source bone is animated.

### Reuse a mapping

Use **Export** under **Bone Mapping** to save the current actor's assignments as a JSON preset. Select a disconnected actor and click **Import** to load it. The preset contains the main bones, ordered spine and neck chains, fingers, and toes. Rig assignments, reference poses, contact markers, and solver settings are stored with the actor in the Blender file. Review any missing bone names after import.

## Set reference poses

The source and target reference poses define how the add-on interprets the mapped bones' orientations. Start with comparable poses. If the source has a T-pose and the target has an A-pose, align the target's shoulder, upper arm, and forearm reference so the same motion starts from a comparable baseline.

1. Expand **Reference Pose** and choose **Edit Reference Pose** under **Source** or **Target**. Blender opens a temporary armature in Pose Mode.
2. Adjust the bones of that temporary armature. While the actor is connected, the target preview responds to the draft.
3. Click **Save** to keep the calibration, **Reset** to restore the starting reference and continue editing, or **Cancel** to discard the draft.

**Use Current Pose** captures the chosen rig's evaluated pose at the current frame. Use it when you already have a suitable calibration pose in the scene. **Use Rig Rest** clears the custom calibration and returns to the armature's rest pose. The saved reference is stored separately from the rig's rest data and animation.

## Tune the solver

Open **Solver Options** for the selected actor. Changes affect the live preview and are included when you bake.

### Solver Strength

| Value | Result |
| --- | --- |
| 0% | Direct FK transfers the mapped rotations and the source root displacement at its original scale. |
| 100% | Applies the configured solver, including root travel, reach, knee settings, and floor contact. This is the default. |
| Between 0% and 100% | Blends root translation and joint rotations while preserving bone lengths. Floor clearance can change as you blend. |

To compare the solver with direct FK, pause at the same frame and move **Solver Strength** between 0% and 100%. Scrub through a few frames with the foot planted to judge travel and through the start of a knee bend to check the transition. The actor uses the same bone mapping and reference poses at every strength.

### Hands / Arms

| Option | Effect |
| --- | --- |
| **Position Reach** | Controls how strongly the target wrist follows the source wrist. The default is 0%. Raise it to keep a reaching hand closer to its intended position. Arm length limits the attainable position. |
| **Rotation Reach** | Controls how strongly the wrist retains its mapped orientation after position correction. The default is 100%. Lower values let it inherit more of the adjusted forearm's rotation. |
| **Calibrated Wrist Reach** | Uses the reference pose to guide the elbow and follows source wrist travel in the target's action space. The default is off, which follows wrist position relative to the upper body. |
| **Separate Left / Right** | Shows individual position and rotation sliders. The first split copies the shared values. Returning to shared controls keeps the individual settings available. |

For a one-handed reach, enable **Separate Left / Right** and adjust **Position Reach** on the reaching side. Compare the hand path and elbow bend over the whole gesture. For a hand supporting the body, wrist Reach can help follow the source pose, while palm placement still depends on wrist orientation and hand shape.

### Feet / Legs

| Option | Effect |
| --- | --- |
| **Position Reach** | Controls how closely the target ankle follows the source ankle path scaled to the target rig. The default is 100%. |
| **Rotation Reach** | Controls how much of the mapped ankle orientation is retained after position correction. The default is 100%. |
| **Staged Ankle Reach** | Guides knee flexion using the reference pose while aiming the leg toward the ankle target. It is off by default. |
| **Separate Left / Right** | Lets each ankle use its own position and rotation values. |
| **Floor Contact** | Corrects penetration using the heel and ball of each foot. It is on by default. |
| **Feet Contact Stiffness** | Preserves ankle rotation during floor correction. At the default 0%, the solver can adjust rotation as well as position. At 100%, it corrects floor contact through position only. |

Set **Source Floor** and **Target Floor** while the actor is disconnected. **World Z = 0** uses the scene's zero-height plane. **Reference Soles** uses the floor level derived from the chosen rig's calibrated feet. **Source Floor Offset (m)** and **Target Floor Offset (m)** raise or lower their respective planes. Reconnect to preview the changes.

For a walk on a character with different leg lengths, start with **Root Travel** set to **Scale to Proportions**. Compare **Position Reach** at a few values while the feet are planted, then check the knees during the swing. Use the target floor and contact markers to tune where its shoes meet the plane.

### Knee

- **Extension Damping** eases toward full leg extension and retains more of the original FK bend near a straight leg. The default is 70%.
- **Bend Preservation** guides airborne legs toward the source bend and eases the transition into floor correction. The default is 60%.
- Compare changes across several frames. A value that softens a straightening knee in a walk may change the shape of a deep bend in another part of the same clip.

### Root

| Option | Effect |
| --- | --- |
| **Scale to Proportions** | Scales horizontal movement to the target's proportions. This is the default **Root Travel** mode. |
| **Match Source Distance** | Transfers the source's horizontal travel distance in scene units. |
| **In Place** | Removes horizontal root travel while keeping the vertical motion. |
| **Travel Multiplier** | Scales the chosen horizontal travel. A value of 1.00 keeps the selected mode's distance. |
| **Hip Height Offset (m)** | Raises or lowers the target hips during the solve. The default is 0 m. |
| **Root Offset (m)** | Moves the entire retargeted pose along Blender's world X, Y, and Z axes after solving. Defaults to 0 m on each axis. |

Set **Root Offset (m)** separately for each actor to place the retargeted characters in the scene. For example, set **X** to 2.000 to move a character two metres along world X. The offset applies at every **Solver Strength** and is included in the baked Action. A Z offset moves the solved foot placement with the body.

## Edit foot contact markers

The target uses two markers per foot, one at the heel and one at the ball of the foot. Move them to match the part of the mesh or shoe that should contact the floor.

1. Open **Solver Options → Feet / Legs → Edit Contact Markers** in Object Mode or Pose Mode.
2. Select a marker in the viewport and move it with Blender's **Move** tool. With the actor connected and **Floor Contact** enabled, the draft updates the preview as you edit.
3. Click **Save** to keep the offsets for that actor and its mapped ankles. **Cancel** returns to the previously saved markers.
4. To restore automatic markers, click **Reset**. The editor stays open so you can check them. Click **Save** to keep the automatic positions.

The contact surface is the floor plane defined for the actor. The markers set where that plane meets each foot.

## Work with multiple actors

Use **+** and **−** beside **Actors** to add or remove pairs. Every actor stores its own mapping, reference poses, markers, and solver values. The selected row determines which pair you edit. Other pairs can stay connected while you make adjustments.

For example, set the same motion rig as **Source** on two actors and assign a different **Target** to each. Build and review the mappings separately, then click **Connect All**. Tune each target's floor, reach, or strength as needed. Each target can belong to one actor. Removing an actor removes its live connection and keeps both armatures in the scene.

**Connect** and **Disconnect** apply to the selected actor. **Connect All** connects the configured pairs. **Disconnect All** removes the live previews in the scene. You can also use one actor's target as another's source, provided the chain has no cycle.

## Bake to an Action

1. Connect the actor and preview the result. Finish editing any reference pose or contact markers.
2. Click **Bake to Action**. Enter an **Action Name**, **Start Frame**, and **End Frame**. The end frame is included.
3. Choose the sampling and curve settings, then confirm. A successful bake activates the new Action on the target and disconnects that actor.
4. Open the **Action Editor** to inspect and edit the keys. Scrub the timeline and check the frames with contact, rapid turns, or large pose changes.

The default range comes from the active source Action's keys. The scene range is used when the source has active NLA or lacks a directly keyed Action.

| Option | Effect |
| --- | --- |
| **Sample Step** | Sets the number of frames between samples. Use 1 to record every frame. |
| **Existing Source Keys** | Samples the source Action's existing key times and the range endpoints. Available for direct Action playback with source keys. Use a step of 1 when motion between those keys affects the result. |
| **Interpolation** | **Linear** is the default. **Constant** holds each value until the next key. **Bezier** provides editable curves and a **Bezier Handles** setting. |
| **Bezier Handles** | **Auto Clamped** smooths without overshooting the keys. **Auto** uses automatic handles. **Vector** makes straight segments. |
| **Fake User** | Keeps the new Action in the file when it is unassigned. Enabled by default. |
| **Mute Target Bone Constraints** | Keeps existing target bone constraints muted after baking their evaluated effect into the Action. Available for ordinary armature targets. |

The previous target Action is kept with a Fake User. Existing NLA tracks are retained, and target NLA playback can be turned off to isolate the new Action. The add-on checks that the baked Action reproduces the evaluated pose at the sampled frames. If drivers or other overrides prevent playback from matching, it restores the previous Action, NLA state, and live connection.

### Bake All

Use **Bake All** to write one Action for each connected actor. Each pair uses its own source motion range, with the scene range used when appropriate. A pair that reads another connected target uses the upstream source's range. Sampling and interpolation options apply to the batch. Successful actors disconnect. Any failed actor stays connected and reports its error in the sidebar.

## Rig-specific setup

### Auto-Rig Pro

For a standard generated Auto-Rig Pro humanoid, select the rig as **Target** and click **Build Bones List**. The panel displays **Target: Auto-Rig Pro FK** when the controller layout is recognized. The connection switches the four limbs to FK and drives their controllers, along with the pelvis, torso, and head. Disconnecting restores the previous switch behavior.

Baking writes controller rotations, pelvis translation, and the FK switch values. The baked motion uses the rig's constraints and drivers and leaves the FK controllers available for editing in Pose Mode. For fingers, **Build Bones List** can match FK controls such as `c_index1.l` through `c_index3.l` when they are present. A custom control layout may need manual mapping or an exported deform skeleton.

### MMD and other naming families

For PMX or PMD files, import the model with MMD Tools and choose the imported armature. **Build Bones List** recognizes common Japanese and English bone names, side suffixes, and MMD Tools bone-name metadata. If the alternate `D` leg chain carries the skin weights, mapping can select that chain. The imported rig's IK and physics settings are managed by MMD Tools. [MMD Tools](https://extensions.blender.org/add-ons/mmd-tools/)

Bone matching also recognizes naming conventions used by Mixamo, Kimodo SOMA, Unity Humanoid, Unreal Mannequin and MetaHuman, Rigify deform rigs, Character Creator and ActorCore, DAZ Genesis, VRM, VRoid, SMPL-X, and other common humanoid rigs. Review the generated list on each rig before connecting.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| **Connect** reports a missing bone or a hierarchy error | Expand **Bone Mapping** and review the named roles. Check for duplicate assignments and map the main anatomical chain in parent order. |
| The arms twist or stay too high or low | Confirm the left and right mapping, then compare the source and target [reference poses](#set-reference-poses). |
| The target covers too much or too little ground | Check **Root Travel**, **Travel Multiplier**, and **Solver Strength**. At 0%, the root follows the source displacement at its original scale. |
| Heels or toes sink into the floor | Check **Floor Contact**, the floor plane and offset, and the [contact markers](#edit-foot-contact-markers). |
| Bake reports active target constraints or a pose mismatch | For an ordinary armature, review the active constraints and use **Mute Target Bone Constraints** when their effect should be written into the Action. If the pose still differs, check drivers and other overrides affecting the reported bone. |
| **Existing Source Keys** is unavailable | Check whether the source has an active keyed Action and direct Action playback. For motion evaluated through NLA, use **Sample Step**. |
