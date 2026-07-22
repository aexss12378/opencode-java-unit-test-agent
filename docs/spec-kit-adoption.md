# 後端團隊規格驅動開發導入決策草案

- 狀態：討論整理，尚待團隊正式核准
- 適用對象：Java／Spring Boot 後端團隊
- 優先範圍：新專案

本文件描述預定導入的公司環境，不代表承載本文件之示範儲存庫的目前設定。

## 結論

團隊優先試行 GitHub Spec Kit，搭配 OpenCode、公司內部模型、公司私有 GitLab 與 Nexus 套件庫。

選擇 Spec Kit 的主要原因不是工具知名度，而是團隊目前最需要解決「需求不清楚，導致開發混亂」。Spec Kit 提供建立規格、釐清問題、產生技術計畫、拆解工作與檢查文件一致性的明確階段，較適合作為新專案的共同流程。

OpenSpec 暫不導入。同一個儲存庫不應同時使用兩套規格流程。未來若工作重心轉為既有系統的小幅修改，且團隊認為 Spec Kit 文件負擔過高，再以相同案例評估 OpenSpec 的規格差異管理方式。

## 已確認條件

- 應用程式語言：Java 17
- 建置工具：Maven
- 應用程式框架：Spring Boot，版本尚未決定
- 原始碼平台：公司私有 GitLab
- Agent 工具：OpenCode
- 模型服務：公司內部模型
- 套件管理：公司 Nexus 套件庫
- 規格核准者：目前由工程師負責
- 專案類型：既有專案與新專案都有，但新專案優先

## 工具選型

| 評估項目 | Spec Kit | OpenSpec | 本次判斷 |
| --- | --- | --- | --- |
| 新專案流程 | 提供完整且有順序的規格、計畫與實作階段 | 流程較輕量、允許彈性調整 | Spec Kit 較符合優先範圍 |
| 需求釐清 | 提供 `clarify` 與跨文件 `analyze` 階段 | 可探索與撰寫情境，但強調彈性而非固定閘門 | Spec Kit 較符合目前問題 |
| 既有系統修改 | 可以使用，但文件可能較多 | 以新增、修改、移除的規格差異為核心 | 未來再以實際案例評估 |
| OpenCode | 官方支援 | 官方支援 | 兩者皆可 |
| 安裝環境 | Python 3.11 以上；支援 uv、pipx 或 pip | 需要 Node.js 與 npm | 公司環境需再確認維運偏好 |

## 預定開發流程

```text
提出需求
  ↓
建立功能規格
  ↓
找出矛盾、缺漏與未決問題
  ↓
需求負責工程師回答並核准規格
  ↓
建立技術計畫與工作項目
  ↓
檢查規格、計畫與工作項目是否一致
  ↓
另一位工程師核准技術計畫
  ↓
Agent 實作
  ↓
Maven、自動化測試與 GitLab CI 驗證
  ↓
合併請求審查
```

對應的 Spec Kit 命令：

```text
/speckit.constitution
/speckit.specify
/speckit.clarify
/speckit.plan
/speckit.tasks
/speckit.analyze
/speckit.implement
```

試行初期只開放到 `/speckit.analyze`。在規格產出與人工核准流程穩定前，不開放 `/speckit.implement` 自動修改正式原始碼。

## 人工閘門

### 規格核准

規格符合以下條件後，才能開始技術設計與實作：

- 說明要解決的問題與使用情境。
- 明確列出本次範圍與不處理事項。
- 每條重要商業規則具有固定編號。
- 包含正常、邊界與錯誤案例。
- 驗收條件可以實際驗證。
- 未決問題為空。
- 指定一位需求負責工程師。
- 由非原規格作者的工程師完成審查。

如果需求涉及價格、退款、權限、法規或營運政策，工程師與 Agent 都不得自行代替業務決策。無法取得決策時，該需求維持阻塞狀態。

### 實作核准

- 規格合併請求與實作合併請求分開。
- 實作必須標示對應的商業規則與驗收條件編號。
- Agent 不得自行修改 Java、Spring Boot 或相依套件版本。
- 建置與驗證以 Maven Wrapper 為預定標準。
- 合併前至少執行 `./mvnw -B -ntp verify`。
- 測試失敗時，不得修改預期結果來迎合現況實作；規格與實作衝突時必須回報工程師。

## OpenCode 權限邊界

初期建議只建立必要角色，不建立複雜的多 Agent 流程。

### 規格 Agent

- 可以讀取專案、既有文件與規格。
- 可以提出釐清問題及草擬規格。
- 寫入檔案需要人工確認。
- 不得自行決定商業規則。
- 不得修改正式原始碼或建置設定。

### 規格審查 Agent

- 只能讀取與提出問題。
- 檢查模糊用語、矛盾、遺漏情境與不可驗證的條件。
- 比對規格與既有系統時，只能回報衝突，不能替團隊選擇答案。

## 公司私有環境

Spec Kit 可用於私有儲存庫，也能在隔離網路環境安裝。需要分開管理兩個資料邊界：

1. Spec Kit 在本機建立規格、計畫、工作項目與 OpenCode 命令檔。
2. OpenCode 將讀取內容送往公司內部模型；模型閘道的紀錄、保存與再利用政策仍須確認。

正式試行前應確認：

- OpenCode 只設定公司內部模型，不得自動退回外部模型。
- 模型閘道是否記錄提示、原始碼與回應，以及保存期限。
- 模型服務是否會使用輸入內容進行訓練或其他再利用。
- Agent 不得任意使用外部網路、外部 MCP 或讀取專案外目錄。
- Git 推送、建置設定與正式原始碼修改受到權限控制。
- 密碼、權杖、客戶資料與正式環境連線資訊不得寫入規格或提示。
- 社群擴充套件需完成原始碼、授權與供應鏈審查後才能使用。

## 透過 Nexus 安裝 Spec Kit

Spec Kit 不必使用 uv。公司若提供 Nexus PyPI 儲存庫，可以使用 pipx 建立獨立環境並安裝固定版本。

先請 Nexus 管理者提供：

- Python 3.11 以上與 pipx 的公司核准安裝方式。
- PyPI group 的 `/simple` 索引網址。
- 公司核准的 `specify-cli` 版本。
- Nexus 登入方式及公司憑證設定。
- 確認 `specify-cli` 與所有 Python 相依套件都能由內部索引取得。

索引網址通常為：

```text
https://nexus.company.example/repository/<pypi-group>/simple
```

安裝範本：

```bash
pipx install \
  --backend pip \
  --index-url https://nexus.company.example/repository/<pypi-group>/simple \
  "specify-cli==<公司核准版本>"
```

驗證：

```bash
pipx list
specify version
```

尚未建立專案目錄時：

```bash
specify init <專案名稱> \
  --integration opencode \
  --script sh
```

如果已經建立 Spring Boot 專案骨架，先切到專用分支並檢查工作目錄，再於專案根目錄初始化：

```bash
specify init --here \
  --integration opencode \
  --script sh
```

非空目錄若要求使用 `--force`，必須先確認分支與差異；不得直接在含有未提交變更的工作目錄強制覆寫。

安裝規則：

- 使用公司內部 PyPI group 作為單一套件入口。
- 不把帳號、密碼或權杖直接放進命令或索引網址。
- 使用公司 CA 憑證，不關閉 TLS 驗證。
- 不額外設定公開 PyPI 的 `extra-index-url`。
- 鎖定明確版本，不安裝浮動最新版。
- 若 Nexus 目前只有 Maven 儲存庫，需要管理者建立 PyPI hosted、proxy 與 group，或提供包含所有相依套件的離線 Wheel 組合。

## 建議試行方式

選擇一個有真實需求的新 Spring Boot 專案，先進行兩週試行：

1. 第一週只產生、釐清及審查規格，不進行 Agent 自動實作。
2. 第二週在規格核准後產生技術計畫與工作項目，再由工程師決定是否開放實作。
3. 記錄開發前發現的未決問題數量、規格審查時間、實作後需求變更次數與誤解造成的重工時間。
4. 試行結束後，由團隊決定保留、簡化或停止流程。

測試涵蓋率、測試成功與突變測試只能視為品質訊號，不能取代已核准的商業規格。

## 待確認事項

- Spring Boot 固定版本。
- GitLab 版本、方案及是否能強制合併請求核准。
- 團隊人數及規格審查輪值方式。
- 第一個新專案試行案例。
- Nexus PyPI group 索引網址與公司核准的 Spec Kit 版本。
- OpenCode 內部模型閘道的紀錄、保存與資料再利用政策。
- `/speckit.implement` 的開放條件與正式原始碼寫入權限。

## 官方資料

- [Spec Kit 安裝說明](https://github.github.io/spec-kit/installation.html)
- [Spec Kit 支援的 Agent 整合](https://github.github.io/spec-kit/reference/integrations.html)
- [Spec Kit 企業與隔離網路安裝](https://github.github.io/spec-kit/install/air-gapped.html)
- [OpenSpec 官方說明](https://github.com/Fission-AI/OpenSpec)
- [OpenCode Agent 與權限](https://opencode.ai/docs/agents)
- [GitLab 合併請求範本](https://docs.gitlab.com/user/project/description_templates/)
- [GitLab 合併請求核准](https://docs.gitlab.com/user/project/merge_requests/approvals/)
- [Nexus PyPI 儲存庫](https://help.sonatype.com/en/pypi-repositories.html)
- [Nexus PyPI 用戶端設定](https://help.sonatype.com/en/configure-pypi-with-nexus.html)
