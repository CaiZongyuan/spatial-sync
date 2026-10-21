# 建议规格草案：真实夹持框与管底任务分离

状态：**待 root 取舍、用户按 AGENTS/grilling 接受；未授权实现。** 本轮只读现有源码、原 advisor、`MECHANICAL_PROPOSAL.md`/SVG 和既有证据，未修改生产代码或运行搜索、测试、SDK、Source、Blender。

## 1. 要解决的问题与已知边界

运行时仍以 `f7a480f` 为依据；main `e8a2f61` 的 history 改动不改变这些抓取逻辑。当前 `_finger_position()` 把 **tube body 原点**直接放进真实 TCP 的手指区域。管底既是 occupancy 原点，也被隐含当作抓取点，无法明确表达长管上部抓取。

新方向只解除这个隐含等同，不改变 Tube 底部身份、真实 TCP 或碰撞几何。原固定满载路径已在40.840 mm开口时包含邻管真实壁点；14个旧姿态中8个也有该类 witness，余6个未知。175 mm抬盖环壁在26°撞管；slot1仍P6 FAIL 18.602685 mm；只有1管做过FREE body实测。本草案不解除这些负例，不证明上部抓取可行，更不宣告A3完成。

## 2. 只推荐一个新增接口

在 `robot_arm.newton.Config` 与 `robot_arm.virtual.Config` 各新增同一可选字段：

```yaml
graspHeightM: 0.0  # number，米；有限、非负；缺省=0
```

Config约束示意：`graspHeightM: float = Field(default=0.0, ge=0, allow_inf_nan=False, strict=True)`；JSON Schema为`{"type":"number","minimum":0,"default":0}`，并明确拒绝bool和数字字符串。

含义：所有 `config.tubes` 中 Tube 的**本体局部 +Z 轴**上，距底部 body 原点 `h` 的中心线抓取点。一个 arm binding 只声明一个公共高度；暂不支持逐管 map、横向偏移、旋转、任意 Site、自动选点或运行时切换。若同一高度不适用于所有配置管，此最小接口不支持该绑定，不能静默只适配其中一管。

`tcpSite` 保持原名、原物理含义：手掌/指尖实际校准的物理夹持框 F。`handBody`、`fingerKeys`、手指区域和闭爪Y轴不变。不得新建“管底工具TCP”、改 builtin 物理 Site，或重命名后把任务框冒充 F。

新场景示例（**80 mm只是未验证示例，尚未选定可用高度或示教向量**）：

```yaml
# 新的 fr3-lab@<下一未发布整数版本>/devices.yaml 摘录
scene: fr3-lab
entities:
  - key: /World/Lab/RobotA
    template: robot/fr3@1
    config:
      locations: locations.yaml
      tubes:
        - /World/Lab/Tubes/Tube01
        - /World/Lab/Tubes/Tube02
        # 实际包必须列出全部承诺管，不能用本摘录裁掉其余载荷
      graspHeightM: 0.080
# handBody/tcpSite/jointKeys/fingerKeys/graspMode继承固定资产模板。
# locations.yaml仍用原底部site、approach/grasp关节向量、zone/requires；无新Site字段。
```

零高度完整退化到旧模型；129 mm原管本体、视觉尺寸、底部Site及所有既定世界坐标不随 `h` 移动。非零高度必须重新示教物理 TCP 的实际关节向量，不能给旧向量贴新解释。

## 3. 坐标与方程

均为米、右手系、Z-up。B是实际Tube底部body框；F是实际物理TCP；L是实际occupancy底部Site。`T_WB=(R_B,p_B)`、`T_WF=(R_F,p_F)`均来自同一物理快照，`g_B=(0,0,h)`：

```text
p_G_actual = p_B + R_B · g_B
v_F        = R_Fᵀ · (p_G_actual - p_F)

p_G_nominal(L,h) = p_L + (0,0,h)
P6_error        = ‖p_F(FK(q_grasp)) - p_G_nominal(L,h)‖ ≤ 0.010 m
```

P6的世界+Z表达“管名义竖直落座”，来自现有 upright/底部模型，不使用手的旋转偏移，也不把任意Site的倾斜轴当成管轴。它不承诺实际Tube一定名义竖直。实际抓取始终用 `R_B`：倾19°时100 mm处横移约33 mm，不能因管底仍在Site而忽略。P6仍只检验名义位置可达，不新增未经批准的方向容差，不证明指垫接触或路径安全。

## 4. Newton动作语义

- `_finger_position()`改算上述 `v_F`；`_in_fingers()`的±16/20/25 mm区域仍固定在**真实F**，不随任务点移动。contact holding仍按原宽度与该区域判定，仍是既有启发式，不能宣称接触力证明。
- weld捕获仍在真实F中用 `v_F` 检查；既有 `17 mm + 2|v_F.y|`捕获/释焊阈值和指速不变。h只改变“被检查的是管上哪个点”，不扩大区域或开口。若上部截面令实际夹爪停在捕获阈值之外，这个候选就失败；本轮不再加直径/接触策略字段。
- `_open()`仍查询实际hand/tube焊接，释焊使用真实F中的抓取点Y偏置；weld holding继续读取实际焊接启停状态。约束仍hand↔tube，启用取当前相对pose，不把body或Site传送到名义目标。
- pick的选管仍读底部 `location`，抬升仍读实际body底部Z且≥20 mm；place仍等实际底部occupancy匹配目标Site，现有1秒和失败码不变。h不得进入 `tube_location()` 的底部距离、相对速度或upright判断。
- 公开 `tcpPose`、运动轨迹显示、`moveTo`仍表示真实F及实际关节运动。`tcpPose`不会变成管底或抓取点G的pose；HTTP/CLI/MCP动作参数不变。

## 5. Generic P6必须显式选择物理端点

现 `packages/library/physics.py:235`计数所有 `spatialReference=site` 字段，不能直接加第二个Site。本方案**不新增Site Config字段**，并把现有 `tcpSite` 的程序字段元数据明确为：

```yaml
# json_schema_extra；程序声明，非用户任意填写的Config
spatialReference: site
postureEndpoint:
  offsetField: graspHeightM
  offsetAxis: worldZ
```

P6先选恰好一个显式 `postureEndpoint`；其它普通Site不是端点。元数据指向一个已定义、有限非负的标量Config字段，才可按上式计算。重复标记、无效offsetField/axis都为结构错误，不得声称P6通过；直接调用P6时亦须fail，不能降为notApplicable。未带新角色的旧程序/SDK fixture仅在h缺省或0时按“恰好一个Site、零偏移”的旧规则读取；非零h缺少显式角色必须拒绝，不能默默忽略高度。多个未标记Site仍拒绝。

**不能只信角色名字。** 非零h的Newton场景必须使用已有的精确、固定hash机器人Binding Template；S5验证实际 `tcpSite`、`handBody`与模板的rebase结果一致，Site实际归hand所有，组合后的hand→TCP局部pose及手/指校准几何与固定资产一致。对FR3即原 `robot/fr3@1`/原URDF物理框，不允许场景强意见挪TCP、指关节轴或夹爪碰撞体。允许现已支持的整机base pose、初始关节值和驱动意见。不能凭一个任意Xform推断“真实手指中心”；校准信任源是固定资产及其真实物理证据。无法核实的非模板非零h场景先拒绝；旧h=0包沿用旧检查，不倒追修改旧资产。

P6报告分别记录原始 `bottomSiteWorld`、`graspHeightM`、名义 `graspTargetWorld`、**实际物理TCP FK世界位置**和误差，并标记 `targetDefinition: tube-grasp-height/1`。同一实体各位置都测，绝不删slot1。校准不符必须失败，即便“某个虚拟点”距离为零。旧P6的h=0数值与阈值不变；这需要明确修订Assets §7.2 P6及信息规格§4.2/§6.3的解释。

## 6. 结构验证、模板与拒绝方式

| 输入或缺口 | 明确结果 |
|---|---|
| 缺省/显式0 | 旧语义；不迁移旧包，不改变阈值 |
| 负数、NaN/Inf、bool/字符串、未知字段 | Config拒绝并给S5 fail及字段路径；只有外层metadata/文档结构无效才用现有`import_invalid`，不新造HTTP协议 |
| h超出任一配置管的本体轴向范围、管底/轴约定无法核实 | S5 fail；先要求可核对的资产/碰撞边界，不能假造最大高度 |
| 同一Scene中的两个arm对同一Tube声明不同h且至少一个非零 | S5 fail；此最小模型不含交接/多高度策略 |
| 物理TCP替换、owner不符、模板/校准摘要变化 | S5 fail，报告实体与字段；不等待动作时KeyError |
| FK超限/不有限/端点角色歧义 | S8或P6 fail；照常 `scene_not_launchable`，不放宽10 mm |
| 动态抓取点不在实际手指区域/无法捕获、反馈不收敛、放置不匹配 | 原 `grasp_failed` / `motion_timeout` / `place_failed`；不写成功事实 |

共同h来自Scene的arm Config，作为场景对所用labware的操作声明，不写入不可变 `robot/fr3@1`。现有模板先rebase/展开，再合并场景override、完整Config验证；h是数值，不作路径rebase/glob展开。S5还核对全部 `tubes` 的实际body资源及高度范围；无可靠几何/一致性证据即不支持此非零模式。它不借机修复原plain-Config glob问题。

## 7. Synthetic同一语义，保留其能力边界

`robot_arm.virtual`接受相同h和默认0；其公开TCP仍是 `kinematics()` 的手框，不能向下挪动来凑目标。已有Synthetic保持管竖直的简化：持管时应满足 `p_bottom = p_hand - (0,0,h)`、`R_B=I`，所以G与真实虚拟手点相合。`SyntheticSource._poses():230`现把管底直接放hand处，必须同步改；不能只更新Program而让画面管底还粘着TCP。

非零模式的virtual pick/place要用同一名义目标和实际kinematic TCP检查；错误关节向量必须失败，不能先设置holding/location再反向让画面满足。释放前预测的管底应满足现有位置条件，不能用 `tube.virtual` 仅观察 `atLocation` 的旧成功捷径掩盖错误；source与program应共用现有Site位置计算，核对 `locations.p` 与实际Synthetic Site。h=0旧路径行为保持兼容。

Synthetic仍是竖直管、确定性运动/声明式事实的简化源，不添加接触求解器、假焊接或FR3物理声称。通过单独新版本 `lab-demo` 的非零h用例验证此语义；USD `fr3-lab` 仍只支持Newton，不新增Synthetic加载USD能力，也不共用两源的关节向量。

## 8. 实际consumer改动清单

| 现有consumer | 需要的具体变化 |
|---|---|
| `robot_arm/newton.py:56/64/191/267` | 统一实际抓取点→真实F变换；禁止各处各自加减h；weld holding/底部lift逻辑保留 |
| `robot_arm/virtual.py`、`tube/virtual.py`、`synthetic_source.py:215` | 非零h动作几何检查、held body底部绘制与释放事实一致 |
| `robot_arm/taught.py`、Config JSON Schema、`library/structural.py` | 有限h、实体/几何/校准一致性；旧Locations结构不加字段 |
| `library/templates.py`、`config_fields()`、API registry payload、Scene runtime payload | 转发同一角色元数据和已验证h；不在HTTP/info中识别robot设备 |
| `library/physics.py:taught_checks()` | 显式端点、校准核对、独立名义点与物理FK输出；保持generic角色驱动 |
| `tools/teach.py` | 新模式从已选Scene/robot Catalog读取h及固定模型/base，比较真实F与名义G；当前硬编码旧base/仅robot-and-tubes的Teacher不能直接声称支持fr3-lab。capture打印bottom/height/target/physicalTCP/误差和输入版本；输出新场景工厂目录，拒绝覆盖已发布包。旧零高度离线教点保留 |
| `tube/newton.py`、`tube/facts.py`、centrifuge、`welds.py` | 底部occupancy、转子孔位身份、实际relpose启停不改；用回归证据确认 |
| CLI、MCP、API action pipeline、Web Inspector/录制器 | 沿用generic Config/动作/属性；只显示现有Config可选h与真实tcpPose，不添加专用抓管路由/tool |

坐标计算适合放在现有arm模块旁的一个SDK-free纯函数模块，由Newton、Synthetic、P6与teach共享**方程**。校准和实际观测分别验证；不能让共享helper成为唯一预期值oracle。无需规划器、硬件抽象框架、运行时IK或新Host协议。

## 9. 版本、旧包和录制

- 未声明h的旧包按0读取；旧示教、动作参数、底Site和旧行为回归必须成立。旧Newton非模板包不强制迁移为模板。旧运行时的 `extra=forbid` 会拒绝新字段，不能静默当0运行；新包明确要求支持本模型的运行时。
- 采用h必须导入**显式新Scene Version**，重新示教并取得新S/P报告；不覆盖旧locations、资产、报告或hash，不改旧基准。新机械派生需要自己的资产版本/provenance；不能伪造原环境许可。
- P6报告在现有可扩展check measurements里注明上面的定义，新场景不借用旧零高度P6结论。preview按实际源码输入digest真实重捕获派生缓存，保持发布包字节不变。
- `robot-arm/v1`动作/属性类型、空间协议和录制段布局不变；h位于已有Config JSON容器，报告字段位于已有扩展容器，不需要偷偷修改固定wire schema。若实施发现必须改固定公共schema，则另行版本化，不能靠本草案豁免AGENTS合同。
- v1/v2录制按原记录的Catalog、属性和pose回放，不通过新程序重新模拟、不补写默认h、不更改旧tcpPose解释。新录制照常固定sceneVersion/contentHash并保存有效Config；旧读者能读取既有Config容器。必须验证新/旧录制与新/旧包组合，不以只加载新包代替兼容证明。

## 10. 反例与接受证据（均待实施后执行）

必须覆盖：默认0在两源、旧模板/非模板及既有录制中的回归；非零h的body旋转/坐标变换、释放Y偏置与底部occupancy独立性；虚拟TCP替换即使距离0也拒绝；额外普通Site不改变显式P6端点，两个端点标记拒绝；h=80 mm却沿用旧管底关节向量应给约80 mm的P6错误；19°倾管用真实上部点计算，不能因管底对齐而获得holding/焊接；Synthetic错误grasp不得通过状态和画面反向修正。

真实Newton接受必须用新候选Scene的原尺度完整载荷：6根独立FREE管、真实支承/碰撞、全部目标，P1/P5、原480步/2 s P2≤5 mm、P3≤2 mm、全部P6≤10 mm；未查静态对仍报告WARN并补独立几何证据，不能伪造PASS。记录真实physical TCP、tube bottom/grasp point、指垫/邻管最小间隙和全段反馈。通过既有动作链pick/lift/place：实际抬升≥20 mm、weld无跳动、运输相对漂移沿用既有≤1 mm门槛，另在明确自由空间诊断解焊后真实运动；稳定落座的place仍按底部location判定。contact成功率与weld效果分开报告。

旧40.840 mm闭爪邻管witness与26°环壁witness必须继续作为负例保留。新姿态/新高度须覆盖完整载荷、80→实际闭合全程、带管运动、盖/铰链扫掠和转子整圈；无顶点witness不代表完整几何已分离。slot1、支承、腔室、许可仍可独立否决交付；不删目标、不放大容差、不缩管或关闭碰撞。

## 11. 供root合并成一次grilling决定的推荐默认值

建议接受的功能方向只有这一项：**以默认0、共同轴向 `graspHeightM` 明确管上抓取点，保持真实TCP与底部任务身份，新增模式经新Scene Version和完整证据交付。** 接受该方向包括上述P6语义修订、两源一致性和兼容要求；不等于接受80 mm为可用值、满载路径/slot1/腔室已解决或允许移动原世界目标。若该受限接口不能表达实际需要，再带具体反例另作规格决定，不预先扩成通用grasp planner。
