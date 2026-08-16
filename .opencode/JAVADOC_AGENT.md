# 通用 Maven Javadoc 代理

## 使用方式

- `/javadoc`：處理根 `pom.xml` 與其 `<modules>` 下所有標準 `src/main/java/**/*.java`。
- `/javadoc @module-a/src/main/java/example/Example.java`：只處理指定 Java 檔案，也可用來日後單檔重試。

流程會先更新 `origin`，以遠端預設分支最新提交建立 `opencode/javadoc/<UUID>` 分支及 `javadoc-worktrees/<UUID>` 共用 worktree。每個 Java 檔案由一個全新的 `javadoc-worker` 子代理負責，全部逐檔工作在同一輪交給 OpenCode 原生 Task 排程。

完成後先驗證差異只有 Javadoc，再執行 Maven `compile`；專案 POM 若設定 `maven-javadoc-plugin`，另執行 `javadoc:javadoc`。有變更時建立單一提交、推送並建立 GitHub Draft PR，成功後移除 worktree。流程不會把 PR 轉為 Ready 或合併。

## 模型與發布介面

主代理與逐檔子代理預設使用 `openrouter/qwen/qwen3.6-35b-a3b`，`temperature: 0.1`。需要更換時可修改兩個代理檔案的 frontmatter。

發布工具以 `PullRequestPublisher` 介面隔離平台實作；目前只有 `GitHubPublisher`，並依賴已登入的 `gh`。可新增另一個介面實作而不改動準備、逐檔寫入與驗證流程。

## 安全邊界

- 子代理無法使用一般編輯、bash、Git、網路、驗證或發布工具，只能對分派的單一檔案呼叫 `apply_javadocs`。
- 每批 Javadoc 在記憶體中全部驗證後才原子寫入；不同檔案平行完成時以狀態鎖避免紀錄互相覆蓋。
- 驗證會排除未完成檔案，並比較移除 Javadoc 後的原始位元；Java 程式、一般註解、POM、測試與其他文件的變更都會被拒絕。
- 規格與原始碼衝突時，該宣告不修改並列入 Draft PR 說明。
