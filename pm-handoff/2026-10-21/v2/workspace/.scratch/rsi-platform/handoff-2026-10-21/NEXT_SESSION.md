请接手 spatial-sync 的后续开发，先完整读取用户提供的 GitHub PM 交接 issue 正文与评论。正文包含完整状态、计划与接手步骤，不要求先找到本地交接文件。

离线副本：`D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/handoff-2026-10-21/HANDOFF.md`。

远端计划摘要：https://github.com/CaiZongyuan/spatial-sync/issues/2#issuecomment-6306003018

用户已确认 #86 完成。本次基线 main500d9ed、main CI十项成功；原44票22关闭。已有持续开发、subagents、独立审查后自动合并授权，无需重复询问常规许可。接手先重新核对GitHub/main/工单依赖、工作树及旧owner/进程；本交接快照不能代替新事实。

恢复本地E2E前先处理#91的真实API用户库隔离。下一批以 #26 新UI语义整合收尾和 #18 A3关键风险为主，最多两条功能实现线；#85做限额维护，不新增全项目门禁。#18完成即可从#30启动T1，不等T0；#29只阻#35。容量允许时接#27→#28和已解锁#34，#42继续后置。I5仍欠真实Linux MCP旅程。

T0当前ledger的GO仅覆盖已完成D1；后续诊断/训练由PM写具体GO并更新ledger，不沿用旧窗口授权，无需重复向用户申请已有范围授权。

严格保留用户暂存HTML、routeTree修改、8011/5191/8014服务。不要恢复旧的GPU/渲染诊断、盲重跑CI或重新生成已交付PR89视频。后续新片Sambert01–15随机，含中文字幕及字母重点标记。原始失败/预算不改标，精确头检查和集成后main CI门禁照常执行。
