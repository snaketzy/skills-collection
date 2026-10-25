# skills-collection

各类 skill 集合。

## 目录结构约定

skill 放在本项目（或目标项目）的 `.trae/skills/<skill-folder>/` 下：

```text
.trae/skills/
└── <skill-folder>/          # 目录名必须等于 SKILL.md 里的 name
    ├── SKILL.md             # 必需
    ├── scripts/             # 可选，确定性或需精确执行的代码
    ├── references/          # 可选，较长文档，按需加载
    └── assets/              # 可选，模板、图片、样板文件
```

- project skill：`<仓库>/.trae/skills/<skill-folder>/`，只在该仓库生效。
- global skill：`~/.trae/skills/<skill-folder>/`，所有项目可用。

## SKILL.md 写法要点

frontmatter 只放 `name` 和 `description`：

```markdown
---
name: kebab-case-name
description: 做什么 + 何时触发（含触发词）+ 不用于什么。单行纯标量，不引号，不用冒号空格。
---
```

- `name`：小写字母/数字/单个连字符，2–64 字符，必须等于目录名。
- `description`：路由唯一依据，触发词放这里而不是正文；不写 `<` `>`。
- 正文：写「怎么做」，不重复 description；只写模型不知道的坑与精确命令。
- 长素材外置到 `references/`，确定性代码放 `scripts/`，模板放 `assets/`。
- 保持 `SKILL.md` 精简（远小于 500 行），未在目标项目实测的坑不要写进去。

## 已有 skill

| skill | 说明 |
|---|---|
| [mux-r2-video-pipeline](.trae/skills/mux-r2-video-pipeline/SKILL.md) | 本地视频 → Cloudflare R2 → Mux Pull → HLS 的批量发布流水线（含脚本、配置模板、launchd 定时） |
