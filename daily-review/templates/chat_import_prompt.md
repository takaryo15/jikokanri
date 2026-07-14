# ChatGPT構造化インポート用プロンプト

次の振り返りを、schema_version 1.0 のJSONオブジェクト1つに整理してください。

- あなたは振り返りを整理する役割です。原文にない内容は追加しないでください
- JSONコードブロックを1つだけ出力し、JSON以外の説明は付けない
- 配列の項目は文字列にし、空の項目や重複を入れない
- today.main と tomorrow.main はそれぞれ最大3件
- 判断できない内容は unclassified に残す
- raw_text にはユーザーの原文を一文字も変えずに入れる
- 日本語を保持する

```json
{
  "schema_version": "1.0",
  "date": "YYYY-MM-DD",
  "raw_text": "ユーザーの原文",
  "today": {
    "main": [],
    "completed": [],
    "partial": [],
    "not_completed": []
  },
  "reflection": {
    "good": [],
    "problems": [],
    "causes": [],
    "change_next": []
  },
  "tomorrow": {
    "main": [],
    "other_tasks": [],
    "minimum": []
  },
  "journal": [],
  "unclassified": []
}
```
