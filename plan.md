# plan.md：Amazon 图片上传工具

## 1. 项目目标
在 Windows 本机领取 CL 任务，通过本机已登录 Chrome，
按跨站国家上传图片并提交 A+，再把终态写回 CL 同一行。
Excel 导入只保留为联调入口。测试阶段连 `catest`，不连 `cadev`。

## 2. 链条骨架
联调（已有）：Excel → SQLite `queue` → 本地图片目录 → 本机 Chrome(CDP)
      → 图片管理页 / 描述页 → 本机状态

正式（第 10 节，未做）：`catest.ca_pid_amz_cross_site_task_all` 的 `New` 行
      → CAS 领取为 `Process` → 本机 SQLite → 入队校验
      → 下载 `IMAGES` → Chrome → 回写同一行

## 3. 当前进度

### 已完成
- [x] 阶段 0 骨架（入口、配置、日志）
- [x] Excel 读取与 SQLite `queue`：`sku` / `asin` / `country` / `market` / `picture` / `A+introduction` / `src` / `status`
- [x] 对话框或拖入单个 `.xlsx`；多个文件整批拒绝；取消不提示；占用、损坏、无权限说明原因
- [x] 七国端口、Seller Central 首页、独立用户目录；端口已在监听时不另开窗口
- [x] CDP 连接层。调试地址只绑定 `127.0.0.1`
- [x] 按 `country` 打开 `/imaging/manage`，再按 `market` 切站点。英、德、法、意、西有旗帜代码；美、加没有
- [x] 等待元素：短超时抛错，不卡住
- [x] 搜索后按文件夹数字图逐格上传。`01` 对应国家模块第一个框，缺号不前移。非数字文件名不读
- [x] 上传成功看临时图或新的亚马逊图片地址。文件进框不算成功
- [x] 西班牙实测：`845-530V00GY` 的 `01`、`02`、`03` 上传后，队列状态为完成；每格变化写入 `picture_log.md`
- [x] `src` 按顺序存到 `D:\aosom\data\picture\<国家>\<sku>\<站点>\`，文件名为 `01`、`02`、`03`。本地复制已验证，真实网址下载未跑
- [x] 描述列表只点第一条：名称与 SKU 相同、内容类型为 EBC、内容状态为「草稿」。点进后的标志是「商品描述详情」
- [x] 账号窗口能区分已登录、未登录、尚未打开
- [x] `load_config` 合并 `settings.local.json`，并按 `env` 选用 `environments` 里的 `cl_db`

### 进行中
- [ ] 本机 `task` 用 `main_country` 打开 Chrome、用 `cross_country` 切站点。代码已接上；`catest` 还没有这两列，没有实切

### 未开始
- [x] 第 10.1–10.3：连 `catest`、每 30 秒轮询 `New`、CAS 领成 `Process`、写入本机 `task`
- [ ] 入队校验，以及 `task_attempt`
- [ ] 把 CL 终态写回同一行（`Confirm` / `Error`）
- [ ] 登录失效时暂停该国并飞书告警


## 4. 模块边界
| 文件 | 职责 | 不许做 |
|---|---|---|
| main.py | 入口、单实例 | 不写业务 |
| cdp.py | 连接、发 CDP 命令 | 不写页面逻辑 |
| imaging_upload.py | 搜索、下滑、上传 | 不写 Excel / 回写 |
| excel_io.py | 读 Excel、得到关键词 | 不碰浏览器 |
| queue_store.py | SQLite 队列 | 不碰浏览器 |
| aplus_upload.py | A+ 上传 | 不复用 imaging_upload 的业务规则 |
## 5. 技术约束
- 语言：Python
- 浏览器控制：只用 CDP，不用 Selenium / Playwright
- 连接对象：本机已登录 Chrome，复用标签页
- 上传方式：DOM.setFileInputFiles，禁止点击系统文件框
- 不引入新框架

## 6. 业务规则
Excel 联调仍按下面几条。CL 任务的图片来源、国别和步骤以第 10 节为准。

- 关键词：SKU 存在用 SKU，否则 ASIN
- 图片目录：`D:\aosom\data\picture\<国家>\<sku>\<站点>\` 
- 位置映射：图片名 0 对应 MAIN，01 对应 PT01，02 对应 PT02，依次类推。不上传 FRONT、SIDE、BACK
- 每位置只放一张
- 缺号不前移。没有 01 时，02 仍进 PT02。
- 文件名都是数字，不处理 MAIN.png 这种名字。
- 扩展名：.gif .pjp .jfif .jpe .pjpeg .jpeg .jpg .png
- 多国：按国家代码循环

## 7. 等待与异常约定
- 等待 = 循环查元素 + `wait(0.25)` + 超时抛错
- 完成条件 = `readyState` 为 interactive/complete + 目标元素出现
- 找不到元素抛 `NeedsManual`，不许猜第一个
- 超时错误必须说明：等哪个 selector、等了多久
- 文件路径进入文件框 ≠ 上传成功；必须等到成功标志

## 8. 禁止事项
- 不重写连接层
- 不引入 Selenium / Playwright
- 不点击系统文件框
- 不在业务代码里写死 selector（集中放常量）
- 不跳过等待直接查 DOM
- 文件框定位必须按各自位置名，不许用全局第一个
- 不把「文件路径进框」当成「上传成功」
- 不写与本段无关的模块
- 不确定的选择器不许猜，列出来问

## 9. 每轮开工规则
每次让 AI 做一段前，要求它：
1. 先复述本段目标和边界
2. 列出缺的信息
3. 只改本段涉及文件
4. 给最小验证方法
5. 不碰未涉及模块
6. 结束一轮，追加一段至progress.md

## 10. CL 任务（设计文档第 5 节）

测试阶段都连 `catest`。`cadev` 不是当前目标。密码只放本机 `config/settings.local.json`，不写入本文件，也不提交。

### 10.1 连接
- [x] 放置 `config/settings.local.json`：`env` 为 `dev`，`environments.dev.cl_db.database` 为 `catest`，并填写 host、端口、账号、密码、`utf8mb4`
- [x] 启动时与 `config/settings.json` 合并。表名 `ca_pid_amz_cross_site_task_all`、轮询间隔、僵尸任务秒数、连接超时仍用 `settings.json` 里的 `cl_db`

### 10.2 CL 何时产生一行
CL 插入，本机不插入。两种触发执行步骤相同：

| 触发 | 条件 |
|---|---|
| 上架完成 | 帖子状态变为 Listed(A+已上传) |
| 图片更新 | 正常图片或 AI 打标图片发生更新 |

`ACTIONS` 建议为 `["site_images", "aplus"]`。缺省或无法解析时，本机仍按「先传图，再 A+」做，不因此记 `Error`。

### 10.3 轮询与领取
- [x] 按 `poll_interval_seconds` 读取 `TASK_STATUS = 'New'` 的行
- [x] 读取 `AMZ_CROSS_TASK_GUID`、`TASK_ID`、`SKU`、`ASIN`、`ACTIONS`、`IMAGES`、`TASK_STATUS`。`MAIN_COUNTRY`、`CROSS_COUNTRY` 在表上存在时一并读取。不靠 `ORG_UNITS_GUID` 定国家
- [x] CAS 领取。更新行数必须是 1，才算领到。当前 `catest` 还没有 `UPDATED_DATE`，所以只改 `TASK_STATUS`；该列出现后再刷新它：

```sql
UPDATE ca_pid_amz_cross_site_task_all
SET TASK_STATUS = 'Process'
WHERE AMZ_CROSS_TASK_GUID = :id AND TASK_STATUS = 'New'
```

`UPDATED_DATE` 列加上之后，同一次更新再写 `UPDATED_DATE = NOW()`。
- [x] 领到后复制进本机 SQLite 的 `task`。写入失败则回滚这次领取。还不开浏览器
- [x] 长任务定期刷新 `UPDATED_DATE`。`TASK_STATUS = 'Process'` 且超过 `stale_processing_seconds`（默认 1800）未更新的行，改回 `New`
- [ ] 本机不写 `ACTIONS`。重试次数和分步状态只留在本机

终态规则先记在这里，回写代码仍属未开始：

- 站点图成功，且 A+ 成功或因无草稿已跳过：`TASK_STATUS = Confirm`，清空 `ERROR_CODE`、`ERROR_MESSAGE`，刷新 `UPDATED_DATE`
- 不可重试，或本机重试耗尽：`TASK_STATUS = Error`，写入 `ERROR_CODE`、`ERROR_MESSAGE`，刷新 `UPDATED_DATE`
- 可重试：CL 保持 `Process`，本机为 `待重试`。不改回 `New`

### 10.4 字段规则
| 字段 | 规则 |
|---|---|
| `TASK_ID` / `AMZ_CROSS_TASK_GUID` | 日志和告警两者都打 |
| `SKU` / `ASIN` | 有 SKU 用 SKU，否则用 ASIN。都空则入队失败 |
| `MAIN_COUNTRY` | 打开该国 Chrome 网址。无法识别为七国代码则失败 |
| `CROSS_COUNTRY` | 在已打开的窗口里切站点。无法识别为七国代码，或账户列表里没有该站旗帜，则失败 |
| `ACTIONS` | 只读。默认两步都做 |
| `IMAGES` | JSON 数组，每一项是完整 `http://` 或 `https://` URL。`[0]` 对应 MAIN，`[1]` 对应 PT01，以此类推 |

同一 `TASK_ID` 再次可执行时不另起一条业务任务。本机按 `TASK_ID` 合并，已成功的站点图跳过，只重跑未成功步骤。

### 10.5 入队校验
空值直接 `Error`，不重试。不用笼统的 `TASK_INVALID`。

| 情况 | ERROR_CODE |
|---|---|
| `TASK_ID` 为空 | `TASK_ID_EMPTY` |
| `MAIN_COUNTRY` 或 `CROSS_COUNTRY` 无法识别为七国代码 | `COUNTRY_INVALID` |
| `SKU`、`ASIN` 都空 | `SKU_ASIN_EMPTY` |
| `IMAGES` 缺省或空数组 | `IMG_EMPTY` |
| `IMAGES` 某项为 null、空串或纯空白 | `IMG_ITEM_EMPTY`（`ERROR_MESSAGE` 带下标） |
| `IMAGES` 某项不是 `http://` 或 `https://` 开头 | `IMG_URL_INVALID` |
| `ASIN` 为空 | `ASIN_EMPTY` |

缺 `ASIN` 时入队即拦截，不先传站点图。

### 10.6 本机队列
在 `data/queue.sqlite3` 上扩展，不沿用现在只服务 Excel 的 `queue` 列当作 CL 任务表。

`task` 当前态：

| 列 | 含义 |
|---|---|
| `id` | 本机主键 |
| `task_id` | `TASK_ID`，唯一 |
| `amz_cross_task_guid` | `AMZ_CROSS_TASK_GUID` |
| `sku` / `asin` | 同 CL |
| `main_country` / `cross_country` | 同 CL |
| `actions` / `images` | JSON，保留原始值 |
| `status` | 见下表 |
| `attempt` | 已执行次数，从 1 开始 |
| `error_code` / `error_msg` | 最近一次失败 |
| `site_image_status` | `未执行` / `成功` / `失败` |
| `aplus_status` | `未执行` / `成功` / `失败` / `跳过` |
| `created_at` / `updated_at` | 本机时间 |

`task_attempt` 每次尝试追加一行：`task_id`、`attempt`、`action`（`site_images` 或 `aplus`）、`error_code`、`error_msg`、`detail`、`created_at`。

| 本机 status | 含义 |
|---|---|
| `待处理` | 已入队，尚未开始 |
| `处理中` | 正在执行 |
| `待重试` | 本次失败且未到上限 |
| `成功` | 对应 CL `Confirm` |
| `失败` | 对应 CL `Error` |
| `待人工` | 该国登录不可用，暂停 |

英国、德国、法国、意大利、西班牙各一条线程，每国同时只跑一条。进程退出时，本机 `处理中` 下次启动改为 `待重试`。CL 若仍是未过期的 `Process`，由本机继续；若已被回收为 `New`，再 CAS 领取后按 `TASK_ID` 合并 attempt 和分步状态。
