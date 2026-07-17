# 測試意圖契約

在修改測試前，為每個獨立行為建立一筆測試意圖。所有欄位都必須有可核對內容；未知資訊填入 `unknown` 並阻擋產生，不得自行補猜。

## 必要欄位

```yaml
id: UT-001
target:
  module: 模組相對路徑
  class: 完整類別名稱
  method: 方法名稱或 class-level
classification: confirmed-specification | current-behavior | suspected-behavior | conflict
rule: 一句可驗證的行為規則
inputs:
  - 具體輸入或前置狀態
expected_observation:
  - 可由公開行為觀察的具體結果
evidence:
  - kind: user-approval | requirement | api-contract | design-document | existing-test | production-code | runtime-observation
    location: 檔案與行號、文件章節、議題連結或對話核准位置
    supports: 此來源實際支持的內容
test_kind: unit | characterization
approval:
  status: pending | approved | rejected
  note: 使用者核准或拒絕的原文摘要
```

## 判定規則

- `confirmed-specification` 必須至少有一項 `user-approval`、`requirement`、`api-contract` 或可追溯的 `design-document`。
- `current-behavior` 的 `test_kind` 必須是 `characterization`。
- `suspected-behavior` 與 `conflict` 的核准狀態不得由代理自行改成 `approved`。
- `expected_observation` 必須描述可觀察結果，不得使用「正常」、「合理」、「沒有問題」等無法形成斷言的詞。
- 一筆意圖只描述一個行為；不同輸入分區或不同失敗模式要拆成不同意圖。

## 核准閘門

向使用者顯示完整意圖後，只有收到明確的核准編號或逐項核准，才能建立測試。沉默、繼續討論或只說「看一下」都不算核准。

