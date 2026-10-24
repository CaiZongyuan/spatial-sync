# spatial-sync

A local laboratory workbench where people and agents manage lab assets and scenes, operate simulated devices, and (later) train robots, with one physics source as the truth.

## Direction

**Lab World**:
The product's name: the laboratory platform on which people and agents run the RSI loop, as presented to people outside the project.
_Avoid_: spatial-sync (the repository), Lab Information Foundation (the information-plane specification), Lab RSI Platform (an earlier working name that the PRD keeps as its title)

**RSI loop** (递归自我改进闭环):
The cycle in which an agent, through the platform, goes from assets to scene to task to training to evaluation to a deployed device program, observes failures, and improves assets, scenes or rewards; people approve at the gates.
_Avoid_: Reference State Initialization (a different RL term), self-play

**Goal** (目标):
A result a person asks the lab to achieve, such as finding the best culture condition for a sample. It remains the same across Task revisions and groups the training rounds, experimental evidence and cumulative budget toward that result.
_Avoid_: Task (a training definition), mission

## Assets

**Asset** (素材):
An immutable, versioned package describing what a thing physically is: physics description, collision, visuals, provenance and license. It says nothing about behavior.
_Avoid_: Model, equipment model, resource

**Asset kind**:
One of `robot`, `device`, `labware` or `environment`.

**Asset Library** (素材库):
The local collection of every imported Asset version that people and agents can browse and place.
_Avoid_: Registry, repository

**Asset Factory** (素材工厂):
Any process outside the platform that produces Assets, such as the Blender scene-director pipeline; its output enters the Asset Library by import.
_Avoid_: Modeling pipeline (when meaning the platform itself)

## Scenes

**Entity** (实体):
A scene object with physics and an `entityKey`, which may be bound to an EntityType.
_Avoid_: Object, prop (when it has physics)

**Scene** (场景):
A laid-out laboratory: its Entities, Dressing, device bindings and Teach Locations, together with the simulation settings it runs under.
_Avoid_: World, level, environment (an Asset kind)

**Scene Library** (场景库):
The collection of every Scene Version the platform can show and launch, both built-in and imported.
_Avoid_: Scene list, scene registry

**Teach Location** (示教位置):
A named place in a Scene that a robot can reach, recorded as joint configurations; it belongs to the Scene because it depends on where the robot stands.
_Avoid_: Waypoint, registered location

**Physical TCP** (物理工具中心框):
The calibrated frame of a robot's actual hand used to describe tool motion and the region between its fingers.
_Avoid_: Object bottom frame, virtual task TCP

**Tube grasp point** (管上抓取点):
A point on a tube's centerline, measured upward from its bottom and moving with the tube's position and orientation.
_Avoid_: TCP, bottom Site

**Bottom Site** (管底站点):
A named place for the bottom of an upright, seated tube; it identifies occupancy independently of where the hand grasps that tube.
_Avoid_: Grasp point, tool frame

**Validation Report** (验证报告):
The recorded outcome of structural and physics checks on an Asset or Scene Version; it decides whether a Scene Version can be launched and what is missing.
_Avoid_: Audit, test results

**Scene Version** (场景版本):
An immutable, published revision of a scene, written `<name>@<rev>`; sessions and recordings refer to exactly one.
_Avoid_: Scene revision (the per-session manifest counter), snapshot

**Dressing** (布景):
Scene content that is only rendered: no physics, no behavior, never part of the physics model.
_Avoid_: Decoration, background entity

## Training

**Job** (作业):
A long-running piece of work the platform launches outside the gateway and tracks through queued, running, succeeded, failed or cancelled; training is the first kind.
_Avoid_: Run (when meaning the managed unit), process, experiment

**Task** (训练任务):
A versioned definition of what a robot should learn in a Scene Version: which action it implements, its success condition, and its rewards, observations and randomization.
_Avoid_: Environment, env, scenario

**Plan** (计划):
An agent's stated intent made visible before it acts: an ordered set of steps or a training round, with its own revisions and status. A Plan is of kind `operation` or `training`.
_Avoid_: Workflow, script, job

**Operation Plan** (操作计划):
A Plan whose steps are action calls, waits and checks on Entities, each with an expected result; the agent carries out the steps itself and the platform tracks them.
_Avoid_: Procedure, recipe, protocol (wet-lab sense)

**Training Plan** (训练计划):
A Plan for one round of training that a person must approve before any Job starts: goal, hypotheses, algorithm, model architecture, Task changes and budget.
_Avoid_: Experiment, sweep, config

**Hypothesis** (假设):
A Plan's stated cause of a past failure together with a measurable expected effect, later marked confirmed, refuted or inconclusive by an Evaluation.
_Avoid_: Idea, guess

**Lab Notebook** (实验记录):
The cumulative record across rounds of Hypotheses, what was tried and what the Evaluations showed, which an agent reads before proposing the next Plan. Automatic judgements and later human judgements remain separately attributable; neither approves deployment.
_Avoid_: Memory, log, history

**Evaluation Suite** (评估集):
The fixed set of cases that every Candidate for one goal is scored on, split into a train part the Optimizer may study and a held-out test part revealed only at promotion; it serves both Agents (lab tasks) and Controllers (robot episodes).
_Avoid_: Benchmark, test set

**Agent** (智能体):
An LLM-driven operator of the lab, defined by a fixed model and thinking level plus a Harness; the platform can run it headlessly to evaluate it.
_Avoid_: Bot, assistant, Claude Code (one client that can host an Agent)

**Harness**:
The editable part of an Agent: its Platform Skills version, instructions, hooks and tool configuration. Agent-layer RSI changes the Harness and never the model.
_Avoid_: Prompt, config, scaffold

**Embodiment** (具身本体):
A specific robot body, simulated or real, such as an FR3 with a Franka hand, together with its sensors.
_Avoid_: Robot model, device, Entity (an Embodiment appears in a Scene as one or more Entities)

**Controller** (控制器):
Everything that decides an Embodiment's motion: base Robot Model, Policy version, Device Program, action space, control rate and observation spec. Embodied-layer RSI changes the Controller.
_Avoid_: Brain, stack, policy (one part of a Controller)

**Robot Model** (机器人模型):
A registered model family a Controller can be built on, such as a VLA foundation model or a policy architecture trained here, with its source, licence, supported Embodiments and compute needs.
_Avoid_: Model (ambiguous with LLM models), checkpoint

**Reference** (参照版本):
The currently accepted best version of an Agent's Harness or of a Controller, against which every Candidate is compared.
_Avoid_: Baseline (only the first Reference), champion, production

**Candidate** (候选版本):
A new version of a Harness or Controller produced by one bounded change and kept only if its Evaluation passes the acceptance rule.
_Avoid_: Experiment, branch, trial

**Demonstration** (示范):
A recorded successful episode, produced by any Device Program, kept as data for resets or imitation learning.
_Avoid_: Trajectory (a planned motion), dataset

**Policy** (策略):
A versioned, trained controller produced by a training Job for one Task, which a device program can run in place of taught motions.
_Avoid_: Model, checkpoint (a training-time snapshot), weights

**Evaluation** (评估):
A recorded series of episodes that runs a Policy or Device Program on the platform against its Task's success condition; the platform's result is the one that counts.
_Avoid_: Benchmark, play, test

**Session Request** (会话启动请求):
An accepted intent to start an interactive Session when the platform's single session slot becomes available. It can wait or be cancelled before a Session is handed over.
_Avoid_: Session (the running or historical laboratory context)

## Agents and people

**Approval Gate** (人工关口):
A step in the RSI loop that only a person can pass: approving a Training Plan, promoting a Policy to a scene's default program, running a Job over budget, or deleting a library version.
_Avoid_: Permission, confirmation (when meaning a conversational check)

**Platform Skill** (平台 skill):
Instructions shipped with the platform that teach an agent how to operate the lab through `lw`, including when to interview a person and which Approval Gates it must not pass itself.
_Avoid_: Development skill (instructions for building this repository), prompt

**Capability Library** (能力库):
The shared collection of Policies and agent-written skills that agents in many labs draw from and add to, so that an improvement made in one lab reaches the others.
_Avoid_: Platform Skill (instructions shipped with the platform), skill store, model zoo

## Behavior

**EntityType** (实体类型):
The declarative description of what a kind of Entity can do: properties, actions, limits and preconditions. It says nothing about shape.
_Avoid_: Asset, equipment model, device class

**Device Program** (设备程序):
Code that carries out an EntityType's behavior for one host; a robot's program may follow Teach Locations, run a Policy, or be a script an agent wrote and verified in simulation.
_Avoid_: Skill (reserved for Platform Skill), driver, controller

**Live Lab** (实验室实况):
The always-running host for real devices, keyed under `/Real/`, independent of simulation sessions; a real device and its simulated twin never share an `entityKey`.
_Avoid_: Digital twin, real session

**Alert** (告警):
A declared condition on an Entity's properties that, once true for its duration, raises a notice with a severity for people and agents.
_Avoid_: Alarm, notification, event (a one-off occurrence)

**Binding Template** (绑定模板):
The part of an Asset that declares which EntityType it implements and which of its own joints and sites play which roles, so placing it in a scene yields its binding.
_Avoid_: Default config, adapter
