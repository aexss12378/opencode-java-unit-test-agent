---
description: 更新整個 Maven 專案或指定單一 Java 檔案的 Javadoc，驗證後建立 GitHub Draft PR
agent: javadoc-writer
subtask: false
---

執行通用 Javadoc 固定流程。

使用者輸入的範圍參數如下；空白表示整個 Maven 專案，否則只能是一個 `@檔案路徑`：

$ARGUMENTS

若參數被 OpenCode 展開成檔案附件，僅取附件的專案相對路徑作為範圍，內容必須從準備工具建立的新 worktree 重新讀取。
