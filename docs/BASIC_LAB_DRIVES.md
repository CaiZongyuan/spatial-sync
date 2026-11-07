# Basic Panda laboratory drives

These four registered Newton programs provide joint positioning for the imported
laboratory. A Scene must explicitly bind their actual joints; the static
`panda-cell-passaging-reference` Scene has no such device bindings. Motor feedback
does not qualify a grasp, transport path, occupancy or biological observation.

| Type / program | Implemented actions | Actual feedback |
|---|---|---|
| `panda-wide-arm/v1` / `panda_wide.newton` | `moveJoints`, `home`, `setGripper`, `stop` | Seven joint positions/velocities in radians and jaw opening in meters |
| `linear-axis/v1` / `linear_axis.newton` | `moveTo`, `home`, `stop` | Rail position/velocity in meters; nominal command range 0–2.95m |
| `motorized-incubator/v1` / `motorized_incubator.newton` | `openOuter`, `openInner`, `closeInner`, `closeOuter`, `stop` | Both hinge angles/velocities and measured open/closed conditions |
| `plate-imager/v1` / `plate_imager.newton` | `extend`, `retract`, `stop` | Drawer extension/velocity and measured endpoints; nominal travel 0–0.19m |

Only implemented actions appear in the Catalog. There are no invented holding,
occupancy, microscopy or cell-count properties. Existing `robot-arm/v1` retains
its original seven-angle interface and0.08m gripper-width meaning; existing
`incubator/v1` remains environmental telemetry.

## Binding and ownership

All joint fields carry the existing `spatialReference: joint` declaration.
Config paths must be exact and distinct. The runtime checks actual joint kind,
position/velocity/target scalar widths and finite model limits. Standalone
world-root door/drawer joints need no forward-kinematics articulation.

- Panda Config: `jointKeys` (seven revolute), `fingerKeys` (two prismatic),
  `homePositions` (seven radians). It never owns or implicitly homes the rail.
- Rail Config: `jointKey` (prismatic), `minPositionM`, `maxPositionM`,
  `homePositionM`; defaults are0,2.95,0.
- Doors Config: `outerJoint`, `innerJoint` (revolute), mandatory source-qualified
  `outerOpenAngle`, `innerOpenAngle`; closed angles default to0.
- Imager Config: `drawerJoint` (prismatic), `retractedPositionM`,
  `extendedPositionM`; defaults are0,.19.

The source Panda's pad geometry establishes
`openingM = 0.11 + finger1M + finger2M`; `setGripper` takes the actual
`openingM` command0.11–0.19m and targets each finger to
`(openingM - 0.11) / 2`. Feedback uses both measured finger positions, including
asymmetric positions. Command qualification uses the actual actuator buffer's
numeric precision; measured feedback is never clamped to nominal travel.

Qualified Scene assembly must assert disjoint physical owners before publishing
its bindings. Call `assert_disjoint_joint_owners` from
`packages.programs.joint_drive` with the entity-to-joint sets returned by each
program's `owned_joint_keys(parsed_config)`. This checks the qualified Scene's
configuration; HostCore does not provide a global physical-joint ownership service.
The generic HTTP/CLI/MCP action pipeline remains the only device entrypoint.

## Completion, stopping and refusal

`moveJoints` requires seven `positions` and `durationSeconds`; rail `moveTo`
requires `positionM` and `durationSeconds`. Other motor actions accept optional
`durationSeconds`, defaulting to1s. Allowed durations are0.05–120s. Targets are
interpolated through the buffered actuator controls, with zero target velocity.

After interpolation, all requested joints must meet their actual position and
velocity tolerances for `settleSeconds` (default0.1 simulation seconds).
Target submission or elapsed duration alone never completes an invocation.
`settleTimeoutSeconds` defaults to5s; a failure holds measured positions and
reports `motion_timeout`/`fault`, preserving the uncertain physical outcome.

Concurrent `stop` and cancellation hold the entity's measured owned joints and
wait for physical settling. A stop cancels the previous motion only after that
settling; a new stop while another stop is pending fails busy. Reset rebuilds
goals from the new measured state and does not replay pre-reset targets.

Opening the inner door requires the outer door's actual open/settled feedback;
closing the outer door requires the inner door's actual closed/settled feedback.
These checks run at admission, again before writing targets, and during motion.
Arm clearance, support/contact qualification and safe Cartesian transport belong
to the physical integration scope and must be added before claiming a transfer.

## Verification and use

The component tests use real Catalog compilation, HostCore and ActionPipeline
with independent fake motor feedback. Their target writes never automatically
change measured positions. Optional readonly PhysicsPort metadata tests construct
no model and take no physical step. A real Source/Plan motor witness, complete
physics regression and shared preview recapture are coordinated with the Scene
owner and PM; these unit checks do not establish a physical experiment.

For a qualified Scene, follow `skills/lab-operate/SKILL.md`: inspect its actual
Catalog/state, submit an Operation Plan, run the returned revision and verify
terminal invocation/Plan status plus the measured final properties. Keep the
returned session/revision/epoch pinned; a wait timeout means inspect that same
invocation rather than resend the motor command.
