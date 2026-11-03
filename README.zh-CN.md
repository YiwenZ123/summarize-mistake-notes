# summarize-mistake-notes

[English](README.md)

`summarize-mistake-notes` 是一个与 Agent 平台无关的 `SKILL.md` 技能，用于将学习题目、订正内容和错题笔记整理为结构化题库。它使用 SQLite 保存可检索内容与间隔重复复习计划，并支持引导式自测、限定范围的 Markdown 导出，以及为依赖视觉信息的题目保存可选图片附件。

## 功能概览

| 能力 | 提供的功能 |
| --- | --- |
| SQLite 唯一事实来源 | 题目、分类、答案和复习状态统一保存在题库数据库中，而不是分散在 Markdown 状态文件里。 |
| 间隔重复复习 | 测验和到期复习流程只展示题目内容。每次作答评为 `correct`、`partial` 或 `wrong`，并据此安排下次复习，间隔依次为 1、3、7、14、30 天。 |
| 限定范围的 Markdown 导出 | 按指定课程或主题生成可分享的完整复习材料，或 `questions-only` 自测试卷。 |
| 受管图片附件 | 将必要的 `prompt` 或 `solution` 图片保存为受管文件，而 SQLite 保存其元数据。 |
| 安全与完整性 | 保存、修改、重置和删除都需明确确认；图片验证、SHA-256 完整性元数据及附件审计共同保护本地学习材料。笔记文件夹被移动或尚未同步完成时会直接报告，而不会被一个空数据库替代。 |

## 工作原理

1. Agent 整理候选复习项目，并在保存前取得学习者对保存项目的明确选择。
2. 结构化 JSON 经验证后，由 `scripts/export_review_set.py` 写入 `<notes_root>/exercise_bank.sqlite3`。
3. 搜索、测验、到期复习及评分命令读取并更新 SQLite 题库；每次评分都会安排该题的下次复习。
4. Markdown 导出仅用于分享、备份或自测，不承担复习状态存储职责。
5. 必要图片可复制到 `<notes_root>/attachments/` 下；基于角色的展示规则会阻止答案图片出现在自测流程中。

## 仓库结构

| 路径 | 用途 |
| --- | --- |
| `SKILL.md` | Agent 执行保存、搜索、复习、导出和附件任务时遵循的操作指令。 |
| `scripts/export_review_set.py` | SQLite、复习调度、导出、附件管理和 CLI 实现。 |
| `references/schema.md` | JSON 导入与修改约定，包括可选附件字段。 |
| `tests/` | CLI 工作流的行为、安全、复习调度及文档一致性测试。 |
| `.github/workflows/tests.yml` | 在 Linux、macOS 和 Windows 上运行测试。 |
| `docs/superpowers/specs/2026-05-24-question-image-attachments-design.md` | 受管图片的设计依据与安全约定。 |

## 环境要求

- 支持本地 `SKILL.md` 技能的 Agent 环境。
- Python 3.9 或更新版本，SQLite 由 Python 标准库提供。Pillow 仅在使用图片附件时需要；未安装时，附件命令会报告 `PILLOW_REQUIRED`，其余命令照常可用。
- 用于克隆、贡献及仓库验证流程的 Git。

## 安装

克隆本仓库，并将所得文件夹放入 Agent 支持的任意技能目录中。本仓库采用通用的 `SKILL.md` 目录布局，不提供安装器。

```bash
git clone https://github.com/YiwenZ123/summarize-mistake-notes.git summarize-mistake-notes
cd summarize-mistake-notes
python3 scripts/export_review_set.py --help
```

示例使用 `python3`，请按系统环境替换为相应的 Python 3 命令。Agent 应相对于已安装的 `SKILL.md` 解析 `scripts/export_review_set.py`，而不是从用户当前工作目录查找。

## 配置存储

本仓库不附带或预配置任何笔记根目录。Agent 首次需要访问数据库时会检查配置；如果尚未设置存放位置，它会询问学习者由哪个私有文件夹保存题库数据库。选择后，数据库位于 `<notes_root>/exercise_bank.sqlite3`，受管图片位于 `<notes_root>/attachments/`，生成的 Markdown 导出文件位于 `<notes_root>/exports/`。

默认 CLI 配置文件为 `~/.config/summarize-mistake-notes/config.json`，可通过 `--config` 指定其他配置文件。

```bash
python3 scripts/export_review_set.py config get
python3 scripts/export_review_set.py config set --notes-root "<folder chosen by the user>"
```

如果已配置的文件夹之后变得不可用（被移动、重命名、断开连接，或云盘文件夹尚未同步完成），所有命令都会以 `NOTES_ROOT_MISSING` 停止；如果文件夹中仍有题库文件却找不到数据库，则以 `NOTES_DB_MISSING` 停止。两种情况下 CLI 都不会新建替代品；只读命令也从不创建数据库文件，只有首次 `db add` 才会创建。

打开由旧版本创建的题库时，会就地升级数据库结构，并保留所有题目及复习记录；由更新版本技能创建的数据库会被拒绝打开，而不会被修改。

不要手动编辑 SQLite 数据库。创建、搜索、复习、导出、更新及删除内容均应通过脚本执行。

## 常用工作流

以下示例应在技能目录中运行，并使用 Python 3.9 或更新版本；图片相关命令另需 Pillow。

### 验证并保存已选择项目

请按照下文格式准备 JSON。只有在学习者明确选择要保存的候选项目后，才能使用 `--confirmed-selection-by-user` 标志。仅在学习者明确请求或批准后，才可在导入数据中包含必要图片。

```bash
python3 scripts/export_review_set.py db validate --input "<prepared-json-file>"
python3 scripts/export_review_set.py db add --input "<prepared-json-file>" --confirmed-selection-by-user
```

### 查看与搜索

```bash
python3 scripts/export_review_set.py db stats --human --topics
python3 scripts/export_review_set.py db search --query "shortest path" --limit 10 --include-content
python3 scripts/export_review_set.py db search --course "Transport" --topic "Equilibrium" --limit 10
```

`--query` 搜索所有已保存文本，`--course` 和 `--topic` 按名称子串匹配。

### 复习并记录结果

`db due` 列出今天及之前到期的题目，最逾期的排在最前；`--shuffle` 会打乱顺序。`db quiz` 按课程、主题或关键词出题，并把尚未到期的题目也作为练习提供。两个命令都不会泄露答案。学习者作答后，`db show` 返回已保存的答案和复习记录用于评分，再由 `db grade` 记录结果：

```bash
python3 scripts/export_review_set.py db due --limit 3
python3 scripts/export_review_set.py db quiz --topic "Network Equilibrium" --limit 3
python3 scripts/export_review_set.py db show <item_id>
python3 scripts/export_review_set.py db grade <item_id> --result partial --note "Missed the capacity constraint."
```

| 结果 | 对复习计划的影响 |
| --- | --- |
| 到期题目答 `correct` | 进入下一档间隔（1、3、7、14、30 天）；通过 30 天那次复习后标记为已掌握。 |
| 未到期题目答 `correct` | 记为练习，复习计划不变。 |
| `partial` | 保持当前间隔，明天再复习。 |
| `wrong` | 复习计划从头开始，明天再复习。 |

`db mark-done <item_id>` 会立即把题目标记为已掌握，`db mark-wrong <item_id>` 等同于 `db grade --result wrong`。想把整门课重新复习一遍时，先用 `db reset --course "<course>"` 预览将重新开始的题目，再加 `--confirmed-by-user` 执行；`db quiz --include-done` 则可以在不重置的情况下练习已掌握的题目。

### 修改已保存的题目

`db update` 就地修改一道已保存题目的字段，保留其 id、附件和复习记录。不带 `--confirmed-by-user` 时只输出修改预览。可修改的字段见 [references/schema.md](references/schema.md)。

```bash
python3 scripts/export_review_set.py db update <item_id> --input "<changes-json-file>"
python3 scripts/export_review_set.py db update <item_id> --input "<changes-json-file>" --confirmed-by-user
```

### 导出限定范围的复习材料

导出必须使用 `--course`、`--topic` 或两者来限定范围。需要答案时使用 `full`，自测时使用 `questions-only`。

```bash
python3 scripts/export_review_set.py db export --course "Transport Planning" --mode full
python3 scripts/export_review_set.py db export --topic "Network Equilibrium" --mode questions-only
```

### 附加与审计图片

只有当图片对于理解或解答已保存题目不可缺少，或者学习者明确要求保留时，才应添加附件。仅在学习者明确请求或批准该图片后，才能执行附加操作。

```bash
python3 scripts/export_review_set.py db attach <item_id> --source "<source-image-path>" --role prompt --provenance provided --caption "Network shown in the question"
python3 scripts/export_review_set.py db attachment-audit
```

修改附件元数据、解除附件关联以及删除题目属于破坏性操作或会影响可见性的操作，必须取得用户的明确授权：

```bash
python3 scripts/export_review_set.py db attachment-update <attachment_id> --role solution --confirmed-by-user
python3 scripts/export_review_set.py db detach <attachment_id> --confirmed-by-user
python3 scripts/export_review_set.py db delete <item_id> --confirmed-by-user
python3 scripts/export_review_set.py db attachment-audit --prune-orphans --empty-trash --confirmed-by-user
```

不带清理标志时，`db attachment-audit` 不会移除附件文件或关联；打开数据库仍可能初始化或迁移其模式。清理标志要求同时提供 `--confirmed-by-user`。

## 命令速查

| 命令 | 用途 |
| --- | --- |
| `config get` / `config set` | 查看或设置笔记根目录。 |
| `db validate` | 检查导入 JSON，不写入任何内容。 |
| `db add` | 保存选中的题目；课程、标题和原题都相同的题目会被更新，而不是重复保存。 |
| `db update` | 就地修改一道已保存的题目。 |
| `db show` | 显示一道题目及其答案、附件和复习记录。 |
| `db search` | 搜索已保存文本，可按课程、主题或复习状态筛选。 |
| `db pending` | 列出尚未掌握的题目。 |
| `db quiz` | 按课程、主题或关键词给出不含答案的题目，到期题目优先。 |
| `db due` | 给出今天及之前到期、不含答案的题目。 |
| `db grade` | 记录 `correct`、`partial` 或 `wrong`，并安排下次复习。 |
| `db mark-done` | 立即把题目标记为已掌握。 |
| `db mark-wrong` | 等同于 `db grade --result wrong`。 |
| `db reset` | 让某门课程、某个主题或指定题目的复习计划重新开始。 |
| `db export` | 按范围导出 `full` 或 `questions-only` Markdown 文件。 |
| `db stats` | 按课程统计；`--human` 输出可读列表，`--topics` 增加按主题统计。 |
| `db rename-course` | 重命名课程，同时保留题目 id 和复习记录。 |
| `db delete` | 永久删除一道题目及其受管图片。 |
| `db attach` | 为已保存题目附加一张经过验证的图片。 |
| `db attachment-update` | 修改图片的角色、来源类型或说明。 |
| `db detach` | 移除一张受管图片。 |
| `db attachment-audit` | 报告缺失、被修改、孤立、残留回收区及不安全的图片文件。 |

`db update`、`db reset`、`db rename-course`、`db delete`、`db attachment-update`、`db detach` 以及审计清理都需要 `--confirmed-by-user`；`db add` 需要 `--confirmed-selection-by-user`。缺少这些标志时命令不做任何修改，以退出码 5 结束，大多数还会输出一份 `preview`，准确说明将要改动的内容。任何命令都可加 `--help` 查看全部选项。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功。 |
| 1 | 出错，原因输出在 stderr。`PILLOW_REQUIRED` 表示附件命令需要 Pillow。 |
| 2 | 命令参数无效。 |
| 3 | `NOT_CONFIGURED`：尚未设置笔记根目录。 |
| 4 | `NOTES_ROOT_MISSING` 或 `NOTES_DB_MISSING`：已配置的文件夹或其中的数据库不可用。 |
| 5 | 需要确认；没有做任何修改。 |

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

`db show` 会返回两种角色的图片，以便 Agent 在学习者作答后展示答案材料。

`db validate` 会验证引用的来源文件而不复制文件。`db add` 和 `db attach` 会在安装文件前重新检查不可变的工作快照：输入大小限制为 25 MiB，解码格式限于 PNG、JPEG 或 WebP，并记录 SHA-256 摘要。受管文件访问会拒绝不安全的符号链接或 Windows junction 路径；`db attachment-audit` 会报告缺失、修改、孤立、残留回收区和不安全路径情况，且不会为清理而跟随不安全链接。

## 安全与隐私

- 除非你有意分享导出文件，否则应将笔记根目录、数据库、导出文件以及来源或受管图片保存在本地并保持私密。它们属于用户数据，而不是仓库内容。
- 不要将已配置的笔记根目录、数据库路径、来源图片路径或受管附件路径提交到本仓库；在 issue、pull request 和文档中仅使用占位符。
- 在执行 `db add --confirmed-selection-by-user` 前，保存操作要求用户明确选择候选项目。
- 通过 `db attachment-update` 改变附件可见性、通过 `db detach` 移除附件、通过 `db delete` 移除题目、通过 `db update` 修改题目，以及通过 `db reset` 重置复习进度，均须在明确授权后提供 `--confirmed-by-user`。
- 如果笔记根目录位于云同步文件夹中，在另一台设备上使用本技能前，请先等待同步完成。如需手动备份，请在没有命令运行时，把 `exercise_bank.sqlite3` 与 `attachments/` 一起复制。
- 如果 `db attachment-audit` 报告 `unsafe_paths`，技能不得打开、渲染、移动或删除被链接的文件；经确认的解除关联或删除操作仅可移除数据库关联，并将外部文件留给用户自行处理。

## 测试

在技能目录中，使用已安装所需依赖项的 Python 3 解释器：

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/export_review_set.py tests/test_export_review_set.py
git diff --check
```

## 贡献与发布

请保持变更范围清晰，保留以 SQLite 为核心并以确认保护操作的安全模型，并在公开行为或命令发生变化时同步更新两种语言的 README。典型贡献流程使用 `feature/<description>` 功能分支，向 `main` 提交 pull request，并在 pull request 中记录验证命令。

对于发布或面向用户的文档变更，请清楚说明迁移或安全影响，且不要发布本地数据库、导出内容、题目材料或受管图片。

## 文档链接

- [通用技能操作指令](SKILL.md)
- [JSON 输入模式](references/schema.md)
- [受管图片附件设计](docs/superpowers/specs/2026-05-24-question-image-attachments-design.md)
