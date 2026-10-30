# PM交接完整文件包

当前执行状态与计划以承载本文件包的GitHub交接issue正文为入口。
workspace/镜像了交接文档、依赖/工作树快照、关键报告和仓库规范的原相对路径。
source/frozen-candidates.bundle保存4条未发布分支引用；第5个候选f7a480f6已经在公共main历史中，先fetch main即可取得对象。branches.json区分这两种来源。可先git bundle verify，再fetch到新的refs/remotes/pm-handoff命名空间，并按issue正文显式建立f7a历史引用。
MANIFEST.json列出每个文件的SHA256和大小；原文件逐字保留。

legacy目录、旧checkpoint、旧HANDOFF、旧posting/receipt均属历史证据，不能据其旧main、旧owner、旧音色或旧GO恢复执行；当前发布包以本MANIFEST为准。
此包不是可启动运行环境，不包含.venv、第三方原始素材、模型630/reset数据、原始大体积录制或用户未提交文件。运行资产仍在交接issue列出的原工作区，跨机器执行前须按证据manifest迁移和校验，不能伪造替代数据。
所有候选仍须对齐当前main、刷新验证和独立审查；源码备份不代表可直接合并。
请解压到新的自有scratch目录，勿直接覆盖正在使用的checkout或用户目录。
