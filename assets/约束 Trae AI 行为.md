# 约束 Trae AI 行为

> **Workspace:** /Users/jerry/Working/芮智联睿/Repositories/isee

---

**User**

怎么给trae加skill，对它的行为做约束


**Assistant**

在 TRAE 中给 AI 添加 Skill 并约束其行为，可以通过以下几种方式实现：

## 1. Skill 的作用与分类

Skill 是具备**主动触发逻辑**和**执行步骤**的工具包，与被动记录的「记忆」不同。分为两个层级：

| 层级 | 生效范围 | 存储位置 |
|------|---------|---------|
| **项目级 Skill** | 仅当前项目可用 | 项目根目录下 `.trae/skills/` |
| **全局级 Skill** | 所有项目可用 | `~/.trae/skills/` (用户目录) |

## 2. 创建 Skill 的结构

一个标准的 Skill 目录通常包含：

```
your-skill-name/
├── skill.yaml          # 元数据配置（必填）
├── SKILL.md            # Skill 的核心指令与行为约束（必填）
├── tools/              # 自定义工具定义（可选）
│   └── *.json
└── scripts/            # 可执行脚本（可选）
    └── *.py / *.sh
```

### skill.yaml 示例（元数据配置）

```yaml
name: my-custom-skill
description: 用于约束 AI 在特定场景下的行为
version: 1.0.0
author: your-name
triggers:
  - when: 用户提到「特定关键词」时
  - when: 检测到特定文件模式
```

### SKILL.md 示例（核心行为约束）

```markdown
# My Custom Skill

## 何时触发本 Skill
- 当用户要求执行 XX 类型任务时
- 当项目中存在 XX 文件时

## 行为约束（MUST / MUST NOT）
- **必须**使用 Flutter 3.x 版本的 API
- **严禁**使用任何国内云服务 SDK
- **必须**优先检查 `mock_data.dart` 中的视频地址

## 执行步骤
1. 先读取项目配置文件
2. 验证约束条件
3. 按规范执行任务
```

## 3. 三种添加 Skill 的方式

### 方式一：手动创建（适合自定义约束）

1. 在项目根目录创建 `.trae/skills/your-skill/` 目录
2. 编写 `skill.yaml` 和 `SKILL.md`
3. 重启 TRAE 或刷新会话即可生效

### 方式二：从 GitHub / 市场安装

如果有现成的 Skill 仓库，可以直接 clone 到对应目录：

```bash
# 安装到全局（所有项目）
git clone https://github.com/xxx/skill-name.git ~/.trae/skills/skill-name

# 安装到项目级（仅当前项目）
git clone https://github.com/xxx/skill-name.git .trae/skills/skill-name
```

### 方式三：通过我协助创建

直接告诉我你的约束需求，例如：
- 「所有视频地址必须走 Mux HLS」
- 「代码风格必须遵循 Netflix UI 规范」
- 「禁止修改 mock_data.dart 的 v001 数据」

我会按照规范生成完整的 Skill 包（包含 `skill.yaml` + `SKILL.md`）并写入正确的目录。

---

## 4. 常用的行为约束写法

在 `SKILL.md` 中，你可以通过**规则清单**精确约束 AI：

```markdown
## 硬约束（Hard Constraints）
- 🚫 **禁止**提交包含任何 API Key / Secret 的代码
- 🚫 **禁止**修改 `lib/core/data/mock_data.dart` 中 v001~v005 的元数据
- ✅ **必须**使用 `VideoQuality.q720p` 作为默认画质映射
- ✅ **必须**所有视频 URL 使用 HLS (.m3u8) 协议

## 代码风格约束
- 所有状态管理使用 Riverpod 2.x `@riverpod` 注解
- 路由必须使用 GoRouter 的 `ShellRoute` 嵌套结构
- 颜色值必须使用 `Color(0xE6000000)` 而非已移除的 `Colors.black90`
```

## 5. Skill vs 记忆（Project Memory）的取舍

| 场景 | 推荐方式 |
|------|---------|
| 记录项目背景、用户偏好 | `project_memory.md`（被动） |
| 「遇到 X 情况必须做 Y」的触发式规则 | **Skill**（主动） |
| 代码规范、API 调用限制 | **Skill** |
| 常用路径、文件位置记录 | `project_memory.md` |

---

你想针对 iSEE 项目添加什么具体的 Skill 约束？比如：
- 视频数据入库流程规范？
- 代码提交前的检查清单？
- Mux API 调用的自动化步骤？

直接告诉我你的约束目标，我可以帮你生成对应的 Skill 文件。

---

