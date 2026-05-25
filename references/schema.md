# Question JSON Schema

Use this reference only when preparing import JSON, debugging `db validate`, or explaining why an import was rejected.

## Minimal JSON

```json
{
  "course": "Calculus",
  "collection_type": "mistake_set",
  "topic": "Derivative Applications",
  "items": [
    {
      "title": "Confusing derivative and function value",
      "original_question": "Why is f'(2) not the same as f(2)?",
      "knowledge_points": ["Geometric meaning of derivatives"],
      "mistake_reason": "Confused function value with instantaneous rate of change.",
      "correct_approach": "Distinguish the function output from the derivative value.",
      "answer_points": ["A derivative gives instantaneous rate of change."],
      "review_suggestion": "Redo three tangent-slope problems.",
      "attachments": [
        {
          "source_path": "<source-image-path>",
          "role": "prompt",
          "provenance": "provided",
          "caption": "Graph shown in the question"
        }
      ]
    }
  ]
}
```

## Fields

| Field | Requirement |
|---|---|
| `course` | Required confirmed course name. |
| `collection_type` | Required; only `mistake_set` or `question_set`. |
| `topic` | Required short topic. |
| `items` | Required non-empty list. |
| `title` | Required short title. |
| `original_question` | Required original question or prompt. |
| `knowledge_points` | Required non-empty list, usually 1-4 items. |
| `mistake_reason` | Required specific cause; for question sets use `Needs review` when no mistake exists. |
| `correct_approach` | Required correct reasoning or steps. |
| `answer_points` | Required non-empty answer-point list. |
| `review_suggestion` | Required actionable review suggestion. |
| `attachments` | Optional list of essential image attachments; omission or an empty list preserves existing images during updates. |
| `source_path` | Required per attachment; local path to a readable PNG, JPEG, or WebP image. |
| `role` | Required per attachment; `prompt` for question-visible material or `solution` for answer-revealing material. |
| `provenance` | Required per attachment; `provided` for an accessible source image or `reconstructed` for a recreated rendering. |
| `caption` | Required non-empty attachment description, used in output and Markdown alt text. |

## Optional Attachments

Use attachments only when visual information is necessary to understand or
solve a saved question, or when the user explicitly requests image
preservation. A `solution` image is answer material and must not be exposed
during quiz or questions-only export flows.

Run validation before adding uncertain JSON:

```powershell
<python> <script> db validate --input "<prepared-json-file>"
```
