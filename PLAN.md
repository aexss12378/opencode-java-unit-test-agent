# 舊版 Java 專案單元測試代理規劃

## 目標

以一個 OpenCode 主代理搭配一個專業技能，安全地分析舊版 Maven Java 專案，先區分正式規格與現有行為，經人工核准後建立單元測試，最後用實際執行結果與 Git 稽核驗證品質。

## 第一版架構

```text
使用者
  ↓
unit-test 主代理
  ↓
java-unit-testing 技能
  ├─ 專案與工具能力檢查
  ├─ Git 與既有測試基準
  ├─ 規格證據分類
  ├─ 人工核准測試意圖
  ├─ 產生單元測試
  ├─ Maven／JaCoCo／PIT 驗證
  └─ 測試審查與變更稽核
```

第一版不使用子代理。分析、產生與審查是同一條連續流程，共用同一份測試意圖與核准紀錄，避免代理交接造成規格遺失。

## 檔案

```text
repo-root/
├── AGENTS.md
├── PLAN.md
├── opencode.json
└── .opencode/
    ├── agents/
    │   └── unit-test.md
    ├── commands/
    │   └── unit-test.md
    └── skills/
        └── java-unit-testing/
            ├── SKILL.md
            ├── agents/
            │   └── openai.yaml
            ├── references/
            │   ├── review-checklist.md
            │   └── test-intent-contract.md
            └── scripts/
                ├── inspect_maven_project.py
                └── repo_change_guard.py
```

## 固定流程

```text
確認目標與信任邊界
→ 唯讀檢查 Maven 與測試能力
→ 保存 Git 與既有測試基準
→ 分類正式規格、現有行為、推測與衝突
→ 人工核准測試意圖
→ 只在 src/test/** 產生測試
→ 執行單一測試與受影響模組回歸測試
→ 在專案已具備能力時執行 JaCoCo 與限定範圍 PIT
→ 依固定清單審查
→ 稽核基準後的 Git 變更
```

## 安全邊界

- 主代理的編輯權限預設拒絕，只開放根目錄或模組下的 `src/test/**`。
- Shell 指令預設拒絕；只有 Git 唯讀指令與兩個受控檢查腳本可直接執行。
- Maven 指令一律要求人工確認，因為建置外掛與測試可以執行任意程式碼。
- 禁止外部目錄、網路查詢與子代理。
- 不得用 Shell 或產生程式繞過編輯限制。

## 完成條件

- 每個測試都對應一項已核准的測試意圖。
- 新增測試可編譯，指定測試與受影響模組測試都有實際結果。
- 執行後結果不得比執行前基準產生未解釋的退步。
- JaCoCo 與 PIT 只在已確認可用時執行；未執行必須列出原因。
- 基準後沒有 `src/test/**` 以外的新版本控制差異。
- 最終回報包含指令、結果、修改檔案、限制與尚未解決的規格衝突。

## 第一版限制

- 僅支援 Maven 與慣例 `src/test/**` 測試目錄。
- 不自動加入或升級 JUnit、Mockito、JaCoCo、PIT 或 Surefire。
- 不修改正式程式、`pom.xml`、建置設定，不建立提交或 PR。
- 不對整個大型專案執行無限制的突變測試。
- 若自我審查在實際使用中反覆漏掉弱斷言，再新增一個唯讀審查代理；第一版不預先加入。
