# summarize-mistake-notes

[English](README.md)

`summarize-mistake-notes` 是一个 Codex 技能，用于将学习题目、订正内容和错题笔记整理为结构化题库。它使用 SQLite 保存可检索内容与复习进度，并支持引导式自测、限定范围的 Markdown 导出，以及为依赖视觉信息的题目保存可选图片附件。

## 功能概览

| 能力 | 提供的功能 |
| --- | --- |
| SQLite 唯一事实来源 | 题目、分类、答案和复习状态统一保存在题库数据库中，而不是分散在 Markdown 状态文件里。 |
| 引导式复习状态 | 测验和到期复习流程只展示题目内容，再记录答对或答错的尝试。 |
| 限定范围的 Markdown 导出 | 按指定课程或主题生成可分享的完整复习材料，或 `questions-only` 自测试卷。 |
| 受管图片附件 | 将必要的 `prompt` 或 `solution` 图片保存为受管文件，而 SQLite 保存其元数据。 |
| 安全与完整性 | 明确的保存/删除确认、图片验证、SHA-256 完整性元数据及附件审计共同保护本地学习材料。 |

## 工作原理

1. Codex 整理候选复习项目，并在保存前取得学习者对保存项目的明确选择。
2. 结构化 JSON 经验证后，由 `scripts\export_review_set.py` 写入 `<notes_root>\exercise_bank.sqlite3`。
3. 搜索、测验、到期复习及复习事件命令读取并更新 SQLite 题库。
4. Markdown 导出仅用于分享、备份或自测，不承担复习状态存储职责。
5. 必要图片可复制到 `<notes_root>\attachments\` 下；基于角色的展示规则会阻止答案图片出现在自测流程中。

## 仓库结构

| 路径 | 用途 |
| --- | --- |
| `SKILL.md` | Codex 执行保存、搜索、复习、导出和附件任务时遵循的操作指令。 |
| `scripts\export_review_set.py` | SQLite、导出、附件管理和 CLI 实现。 |
| `references\schema.md` | JSON 导入约定，包括可选附件字段。 |
| `tests\test_export_review_set.py` | CLI 工作流的行为和安全测试。 |
| `docs\superpowers\specs\2026-05-24-question-image-attachments-design.md` | 受管图片的设计依据与安全约定。 |

## 环境要求

- 能够加载本地技能目录的 Codex 环境。
- 运行 CLI 需要安装了 Pillow 的 Python 3 环境，因为脚本在启动时导入 Pillow。图片附件功能仍为可选；SQLite 由 Python 标准库提供。
- 用于克隆、贡献及仓库验证流程的 Git。

## 安装

克隆本仓库，并将所得文件夹放入你的 Codex 安装可识别的技能目录中。本仓库描述本地技能目录布局，不声明提供官方安装器。

```powershell
git clone https://github.com/YiwenZ123/summarize-mistake-notes.git <installed-skill-directory>

$Python = "<python-3-with-pillow>"
$Script = "<installed-skill-directory>\scripts\export_review_set.py"
& $Python $Script --help
```

`SKILL.md` 使用相对于安装位置的占位符，而不包含开发者机器路径；直接调用 CLI 时，请按自己的安装位置解析 `$Python` 与 `$Script`。

## 配置存储

本仓库不附带或预配置任何笔记根目录。Codex 首次需要访问数据库时会检查配置；如果尚未设置存放位置，它会询问学习者由哪个私有文件夹保存题库数据库。选择后，数据库位于 `<notes_root>\exercise_bank.sqlite3`，受管图片位于 `<notes_root>\attachments\`，生成的 Markdown 导出文件位于 `<notes_root>\exports\`。

```powershell
$Python = "<python-3-with-pillow>"
$Script = "<installed-skill-directory>\scripts\export_review_set.py"

& $Python $Script config get
& $Python $Script config set --notes-root "<folder chosen by the user>"
```

不要手动编辑 SQLite 数据库。创建、搜索、复习、导出、更新及删除内容均应通过脚本执行。

## 常用工作流

下列示例假设已按上文定义 `$Python` 和 `$Script`。

### 验证并保存已选择项目

请按照下文格式准备 JSON。只有在学习者明确选择要保存的候选项目后，才能使用 `--confirmed-selection-by-user` 标志。仅在学习者明确请求或批准后，才可在导入数据中包含必要图片。

```powershell
& $Python $Script db validate --input "<prepared-json-file>"
& $Python $Script db add --input "<prepared-json-file>" --confirmed-selection-by-user
```

### 查看与搜索

```powershell
& $Python $Script db stats --human
& $Python $Script db search --query "shortest path" --limit 10 --include-content
```

### 复习并记录结果

使用 `db quiz` 复习指定课程，或使用 `db due` 获取到期且待复习的题目。正确答案可以标记完成；错误或不完整的答案会附带备注并保持待复习状态。

```powershell
& $Python $Script db quiz --course "Transport Planning" --limit 3
& $Python $Script db due --limit 3
& $Python $Script db mark-done <item_id>
& $Python $Script db mark-wrong <item_id> --note "Missed the capacity constraint."
```

### 导出限定范围的复习材料

导出必须使用 `--course`、`--topic` 或两者来限定范围。需要答案时使用 `full`，自测时使用 `questions-only`。

```powershell
& $Python $Script db export --course "Transport Planning" --mode full
& $Python $Script db export --topic "Network Equilibrium" --mode questions-only
```

### 附加与审计图片

只有当图片对于理解或解答已保存题目不可缺少，或者学习者明确要求保留时，才应添加附件。仅在学习者明确请求或批准该图片后，才能执行附加操作。

```powershell
& $Python $Script db attach <item_id> --source "<source-image-path>" --role prompt --provenance provided --caption "Network shown in the question"
& $Python $Script db attachment-audit
```

修改附件元数据、解除附件关联以及删除题目属于破坏性操作或会影响可见性的操作，必须取得用户的明确授权：

```powershell
& $Python $Script db attachment-update <attachment_id> --role solution --confirmed-by-user
& $Python $Script db detach <attachment_id> --confirmed-by-user
& $Python $Script db delete <item_id> --confirmed-by-user
& $Python $Script db attachment-audit --prune-orphans --empty-trash --confirmed-by-user
```

不带清理标志时，`db attachment-audit` 不会移除附件文件或关联；打开数据库仍可能初始化或迁移其模式。清理标志要求同时提供 `--confirmed-by-user`。

## JSON 输入格式

导入数据包含课程、集合类型、主题以及一个或多个完整题目。`attachments` 列表是可选的；以下简短示例包含一张可在题目中展示的图片：

```json
{
  "course": "Transport Planning",
  "collection_type": "question_set",
  "topic": "Network Equilibrium",
  "items": [
    {
      "title": "Choose a toll link",
      "original_question": "Which link should be tolled in this two-link network?",
      "knowledge_points": ["Second-best pricing"],
      "mistake_reason": "Needs review",
      "correct_approach": "Compare equilibria under a toll on each candidate link.",
      "answer_points": ["Use the tolled equilibrium that minimizes total travel cost."],
      "review_suggestion": "Re-solve one parallel-link toll example.",
      "attachments": [
        {
          "source_path": "<source-image-path>",
          "role": "prompt",
          "provenance": "provided",
          "caption": "Network shown in the question"
        }
      ]
    }
  ]
}
```

关于必填题目字段、可接受的 `collection_type` 值以及附件验证规则，请参阅 [references/schema.md](references/schema.md)。更新既有题目时，省略 `attachments` 或提供空列表都会保留现有附件。

## 受管图片附件

SQLite 保存附件元数据，包括角色、来源类型、受管相对路径、媒体信息和 SHA-256 摘要。图片字节作为文件存放在受管笔记根目录的附件文件夹中，而不是作为数据库 BLOB 保存。

| 值 | 含义与可见性 |
| --- | --- |
| `prompt` | 题目可见材料；可以出现在 `db quiz`、`db due` 和 `db export --mode questions-only` 中。 |
| `solution` | 可能泄露答案的材料；不得出现在 `db quiz`、`db due` 或 `questions-only` 导出中。 |
| `provided` | 所保存来源是可访问且由用户提供以便保留的图片文件。 |
| `reconstructed` | 图片是忠实的本地重建版本，而非原始来源图片。 |

`db validate` 会验证引用的来源文件而不复制文件。`db add` 和 `db attach` 会在安装文件前重新检查不可变的工作快照：输入大小限制为 25 MiB，解码格式限于 PNG、JPEG 或 WebP，并记录 SHA-256 摘要。受管文件访问会拒绝不安全的符号链接或 Windows junction 路径；`db attachment-audit` 会报告缺失、修改、孤立、残留回收区和不安全路径情况，且不会为清理而跟随不安全链接。

## 安全与隐私

- 除非你有意分享导出文件，否则应将笔记根目录、数据库、导出文件以及来源或受管图片保存在本地并保持私密。它们属于用户数据，而不是仓库内容。
- 不要将已配置的笔记根目录、数据库路径、来源图片路径或受管附件路径提交到本仓库；在 issue、pull request 和文档中仅使用占位符。
- 在执行 `db add --confirmed-selection-by-user` 前，保存操作要求用户明确选择候选项目。
- 通过 `db attachment-update` 改变附件可见性、通过 `db detach` 移除附件、通过 `db delete` 移除题目，均须在明确授权后提供 `--confirmed-by-user`。
- 如果 `db attachment-audit` 报告 `unsafe_paths`，技能不得打开、渲染、移动或删除被链接的文件；经确认的解除关联或删除操作仅可移除数据库关联，并将外部文件留给用户自行处理。

## 测试

在仓库根目录中，使用已安装所需依赖项的 Python 解释器：

```powershell
$Python = "<python-3-with-pillow>"

& $Python -m unittest discover -s tests -v
& $Python -m py_compile scripts\export_review_set.py tests\test_export_review_set.py
git diff --check
```

## 贡献与发布

请保持变更范围清晰，保留以 SQLite 为核心并以确认保护操作的安全模型，并在公开行为或命令发生变化时同步更新两种语言的 README。典型贡献流程使用 `codex/<description>` 功能分支，向 `main` 提交 pull request，并在 pull request 中记录验证命令。

对于发布或面向用户的文档变更，请清楚说明迁移或安全影响，且不要发布本地数据库、导出内容、题目材料或受管图片。

## 文档链接

- [Codex 操作指令](SKILL.md)
- [JSON 输入模式](references/schema.md)
- [受管图片附件设计](docs/superpowers/specs/2026-05-24-question-image-attachments-design.md)
