---
title: Physical AI：让 AI 在实验室里做事
description: 看设备清单、看三维大屏，与让 AI 操作实验室有什么区别？用一次处理样品的例子，讲清 Physical AI、信息管理系统、数字孪生与具身智能的关系。
date: 2026-10-28
author: Lab World 团队
cover: /images/blog/physical-ai-workbench.webp
---

<script setup>
import './physical-ai-diagrams.css'
</script>

# Physical AI：让 AI 在实验室里做事

![机械臂夹着试管，准备放进离心机，摄像头观察着实验台](/images/blog/physical-ai-workbench.webp)

*图 1 · 从“看见实验室”到“在实验室里做事”。AI 概念插图。*

设备清单、实时读数、一个能旋转查看的三维实验室。

第一次看到 Lab World 的界面，很容易想到实验室管理系统，或者数字孪生大屏。毕竟，这些软件也会显示设备、数据和三维场景。

但我们想做的事情，还要再往前走一步：

**你给 AI 一个目标，让它根据现场情况安排操作，通过机器人和设备去完成，再检查结果、处理变化。**

这就是我们所说的 Physical AI，也就是物理人工智能。它关心的是：AI 怎样在物理世界里把事情做成。

先用一件具体的事来理解。

## 同一间实验室，三种不同的工作

假设实验台上有一批样品，需要放进离心机处理。

**信息管理系统**可以记录：有哪些样品，离心机放在哪里，谁预约了它，实验结果存在哪个文件里。它帮助我们把信息管清楚。

**数字孪生**可以让现实中的设备有一个对应的数字版本，接收设备数据，帮助人观察、分析，有的还能模拟接下来可能发生什么。三维大屏只是它的一种展示方式。

**Physical AI**进一步关心操作：这批样品该怎么处理？设备现在能不能用？机器人要先拿哪支试管？操作没有成功，下一步怎么办？

<figure class="pai-diagram" aria-labelledby="pai-roles-caption">
  <div class="pai-diagram-body">
    <p class="pai-eyebrow">同一间实验室 · 三种工作重点</p>
    <dl class="pai-roles">
      <div>
        <dt>信息管理系统</dt>
        <dd><strong>把信息管清楚</strong><span>有哪些样品？谁预约了设备？</span></dd>
      </div>
      <div>
        <dt>数字孪生</dt>
        <dd><strong>帮助观察与分析</strong><span>现场现在怎样？接下来会怎样？</span></dd>
      </div>
      <div class="pai-focus">
        <dt>Physical AI</dt>
        <dd><strong>操作，再检查结果</strong><span>接下来怎么做？事情做成了吗？</span></dd>
      </div>
    </dl>
  </div>
  <figcaption id="pai-roles-caption">图 2 · 三种常见的工作重点。它们可以出现在同一套软件里。</figcaption>
</figure>

这里没有一条绝对的分界线。信息管理系统也能下发指令，数字孪生也能参与预测和控制。

我们真正要分清的是：**拥有设备数据和三维画面，还不足以说明 AI 已经有了完成物理任务的能力。** 还要看它怎样利用这些信息去行动，以及行动之后是否真的取得了结果。

## 从“显示一个温度”，到“根据温度做事”

大屏上显示“温度偏高”，人看见之后去处理，这是一个很有用的监控功能。

如果希望 AI 接手一部分工作，它还需要知道：哪台设备温度偏高？这会影响正在做的任务吗？现在该继续等待、调整操作，还是暂停并请人处理？采取措施之后，温度有没有恢复？

同样，三维画面里的一支试管到了目标位置，也不代表真实试管已经放好了。它可能根本没有被夹住，也可能在途中滑落。

因此，一次操作要走完这样一个过程：

<figure class="pai-diagram" aria-labelledby="pai-loop-caption">
  <div class="pai-diagram-body">
    <p class="pai-eyebrow">一次操作 · 从观察到反馈</p>
    <ol class="pai-steps">
      <li><span class="pai-step-number" aria-hidden="true">01</span><strong>看现场</strong><p>样品在哪里？<br>设备能不能用？</p></li>
      <li><span class="pai-step-number" aria-hidden="true">02</span><strong>决定下一步</strong><p>先做什么？<br>现在需要等待吗？</p></li>
      <li><span class="pai-step-number" aria-hidden="true">03</span><strong>执行操作</strong><p>让机器人和设备<br>拿起、移动、放入。</p></li>
      <li><span class="pai-step-number" aria-hidden="true">04</span><strong>检查结果</strong><p>真的放好了吗？<br>要不要调整？</p></li>
    </ol>
    <p class="pai-feedback"><span aria-hidden="true">←</span> 没有达到目标，就带着新情况回到观察，再判断。</p>
  </div>
  <figcaption id="pai-loop-caption">图 3 · 不只发出命令，还要检查事情有没有做成。</figcaption>
</figure>

这里的“根据结果再做调整”，通常叫作反馈。自动化早就有反馈，也能完成许多固定流程。Physical AI 希望进一步提高的是：当物体位置、设备安排或任务要求发生变化时，系统仍能理解情况，作出合适的调整。

能适应多少变化，需要实际测试。给系统接上一个大模型，或者让它能调用设备接口，都只是开始。

## 它和具身智能有什么区别

“具身智能”里的“身”，可以先理解为身体。

AI 有了机械臂、轮子或腿，就要学会通过这个身体与环境打交道。摄像头装在哪里，夹爪是什么形状，手臂能伸多远，都会影响它能做什么、该怎样做。

**教机械臂把一支试管拿稳，就是一个典型的具身智能问题。** 这里最关心的是机器人如何利用自己的身体完成动作。这个身体可以是真实的，也可以是仿真里的。

把视线拉远一点：机械臂拿起试管之后，还要与样品架、离心机和其他设备配合，把整项任务完成。这就很适合从 Physical AI 的角度来理解。

<figure class="pai-diagram" aria-labelledby="pai-perspectives-caption">
  <div class="pai-diagram-body">
    <p class="pai-eyebrow">具身智能 × Physical AI</p>
    <div class="pai-perspectives">
      <div>
        <p class="pai-term">具身智能</p>
        <strong>通过身体与环境交互</strong>
        <p>机械臂怎样把试管拿稳？</p>
        <p class="pai-detail">关注身体怎样感知和操作环境；这个身体也可以在仿真中。</p>
      </div>
      <div>
        <p class="pai-term">Physical AI</p>
        <strong>把物理任务做成</strong>
        <p>机器人和仪器怎样配合，完成一批样品的处理？</p>
        <p class="pai-detail">关注任务的执行与结果；也可以通过一组固定设备工作。</p>
      </div>
    </div>
    <p class="pai-overlap"><span>共同之处</span>真实环境中的智能机器人，通常同时涉及两者。</p>
  </div>
  <figcaption id="pai-perspectives-caption">图 4 · 两种看问题的角度，有很大的重叠。这里用例子帮助理解，不是在划定严格的学科边界。</figcaption>
</figure>

**具身智能是实现 Physical AI 的一条重要路径。** 同一台在真实环境中工作的智能机器人，往往会同时涉及这两个概念。

Physical AI 也可以通过一组固定设备工作。例如，AI 根据温室的环境和植物情况，安排通风、浇水，再观察效果。传感器、阀门和通风窗分散在不同地方，并不需要一个人形机器人。

![自主车辆、机器人实验室和带有传感器及控制装置的温室](/images/blog/physical-ai-explained/applications.webp)

*图 5 · AI 可以通过一台机器人行动，也可以通过多台设备配合行动。AI 概念插图。*

## 那么，Lab World 到底在做什么

我们选择先从实验室出发，为 AI 搭一个能够观察、操作和练习的环境。

在这里，AI 需要看懂有哪些设备、当前是什么状态；能够发出设备支持的操作；操作之后，能知道成功还是失败；失败了，还能回看当时发生了什么。

<figure class="pai-diagram" aria-labelledby="pai-route-caption">
  <div class="pai-diagram-body">
    <p class="pai-eyebrow">Lab World · 正在搭建的路线</p>
    <ol class="pai-steps">
      <li><span class="pai-step-number" aria-hidden="true">01</span><strong>准备环境</strong><p>先在仿真里搭好<br>设备和场景。</p></li>
      <li><span class="pai-step-number" aria-hidden="true">02</span><strong>尝试操作</strong><p>让 AI 读取状态，<br>发出设备操作。</p></li>
      <li><span class="pai-step-number" aria-hidden="true">03</span><strong>检查结果</strong><p>保存成功与失败，<br>看清有没有做成。</p></li>
      <li><span class="pai-step-number" aria-hidden="true">04</span><strong>练习与比较</strong><p>尝试改进，验证<br>是否真的变好。</p></li>
    </ol>
    <p class="pai-note">三维界面帮助人观察整个过程。仿真里的能力，仍需在真实设备上验证。</p>
  </div>
  <figcaption id="pai-route-caption">图 6 · 我们正在搭建的路线：让 AI 有地方练习，也有依据判断自己做得怎样。</figcaption>
</figure>

这样再看界面上的功能，就容易理解它们为什么存在：

- **素材和场景库**，是为了准备不同的实验环境。
- **三维视图和实时读数**，是为了让人看清现场，检查 AI 在做什么。
- **设备操作接口**，是为了让 AI 能实际尝试操作。
- **运行记录和评估**，是为了找到失败原因，比较改进前后的效果。

它们共同服务于一个目标：让 AI 逐渐学会把实验任务做好。

项目现在交付的是本地实验工作台，主要在仿真环境中连接这些能力。仿真让我们可以反复布置实验、尝试操作、分析失败；但仿真里的成功，仍然需要在真实设备上验证。稳定抓取、可靠搬管，以及真实实验室的长期自主运行，都还有工作要做。

所以，理解这个项目最直接的方法，是看一项具体任务：AI 有没有看懂现场，操作有没有执行，样品有没有真的放好，遇到变化后能不能继续处理。

一间实验室显示在屏幕上，是人看见了它。

AI 能在其中完成任务，再根据结果调整下一步，才是我们朝着 Physical AI 前进的方向。

## 参考与延伸阅读

1. 《物理人工智能导论》第 1 章，尤其是 §1.2 的概念边界和 §1.5 的应用讨论。
2. [Burger 等，A mobile robotic chemist](https://doi.org/10.1038/s41586-020-2442-2)，Nature，2020。
3. [Degrave 等，Magnetic control of tokamak plasmas through deep reinforcement learning](https://doi.org/10.1038/s41586-021-04301-9)，Nature，2022。
4. [Lab World 当前验收边界](https://github.com/CaiZongyuan/spatial-sync/blob/d2746d32c0b4c9b052ad1068878be5594af0004d/docs/spec/LAB_RSI_PLATFORM_PRD_V1.md)。
