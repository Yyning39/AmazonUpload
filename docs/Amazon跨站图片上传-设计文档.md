# Amazon 跨站图片上传 设计文档

本文描述本机程序 **Amazon 跨站图片上传**（仓库 `amz-piggyback-im`）的目标、与 CL 的分工、两条后台操作，以及任务、重试、失败通知和日志。后台点击顺序以[亚马逊后台上传站点图片+提交应用 A+](https://aosom.feishu.cn/wiki/KXnEwlzFgijD4pkhpHacUG0GnTh)为准。实现进度以代码为准，见第 11 节。

## 1. 目标

在 Windows 本机，用已登录的 Chrome，按国家完成两件事：

1. **传站点图**：把上游给出的图片上传到 Seller Central 图片管理页。
2. **A+ 提交应用**：找到类型为 EBC、状态为草稿的 A+，确认已应用 ASIN 后提交批准。

CL 负责欧洲五国跟卖上架，以及在规定时机触发本程序。本程序不实现上架，只执行上传和提交，并把站点图结果交回 CL。

## 2. 范围

| 侧 | 做 | 不做 |
|---|---|---|
| CL | 欧洲五国跟卖上架（与北美同一套逻辑）；在两个时机触发本程序；在上架页记录站点图上传是否成功 | 不操作 Seller Central 页面 |
| 本程序 | 接收任务、下载图片、按国家上传站点图、提交 A+、落库异常、重试、记日志、把最终失败交给 CL | 不创建 Listing，不删除后台图片，不上传 FRONT / SIDE / BACK |

国家站点与现有调试端口一致：US `9222`、CA `9223`、UK `9224`、DE `9225`、FR `9226`、IT `9227`、ES `9228`。欧洲五国指 UK、DE、FR、IT、ES。

## 3. 运行形态

程序跑在操作员的 Windows 本机，不是 Linux 服务。

- 每个国家使用独立 Chrome 用户目录和调试端口，调试地址只绑定 `127.0.0.1`。
- 浏览器控制只用 Chrome DevTools Protocol。上传用 `DOM.setFileInputFiles`，不弹出系统文件框。
- Chrome 与本程序必须在同一台机器。Seller Central 登录、验证码留在该 Chrome 里人工完成。
- 正式通道是 CL 任务表。CL 插入一行，本机轮询领取，终态写回同一行。CL 服务器不连接本机 `127.0.0.1`。任务正文以第 5 节为准。

Excel 选择/拖入只保留为联调入口，不作为自动触发通道。

## 4. 总流程

```text
CL 在触发点插入任务表一行（待处理）
  → 本机轮询并领取（改为处理中）
  → 复制到本机 SQLite 后执行
  → 按国家连接已登录 Chrome
  → 传站点图（需要时先下载图片）
  → 提交 A+（仅「Listed(A+已上传)」触发）
  → 更新同一行：成功，或失败并带上 error_code、error_msg
```

一次任务可以只做站点图，也可以先站点图、再 A+。A+ 不依赖本次站点图已经通过亚马逊审核。亚马逊审核是后台异步结果，RPA 不等待「已显示」或前台可见。

## 5. 任务

### 5.1 与 CL 的通道

正式通道采用 **CL 任务表轮询**。做法对齐渠道视频工具里已经跑通的模式：执行过程以本机 SQLite 为准，终态写回来源。视频工具的来源是原 Excel 的结果列；这里的来源换成 CL 任务表的同一行。

CL 在第 5.2 节的两个时机 `INSERT` 一行，状态为 `待处理`。本机按 `poll_interval_seconds`（默认 30 秒）查询 `待处理` 行。领取时执行：

```sql
UPDATE 任务表
SET status = '处理中', worker_id = :本机标识, updated_at = 当前时间
WHERE task_id = :id AND status = '待处理'
```

更新行数为 1 才算领到，避免两台电脑抢同一行。领到后把该行复制进本机 SQLite，再打开浏览器。

执行结束时 `UPDATE` 同一行，至少写回：`status`、`attempt`、`error_code`、`error_msg`、`site_image_status`、`updated_at`。可重试的失败在次数用尽前保持 `处理中`，并把最近一次错误写在这行上；达到上限或遇到不可重试错误时改为 `失败`。成功改为 `成功`，并清空 `error_code`、`error_msg`。不再另开一条失败消息。

`处理中` 且 `updated_at` 超过 `stale_processing_seconds`（默认 30 分钟）的行，由原 `worker_id` 在下次启动时改回 `待处理`。其他机器不抢占尚未过期的处理中任务。

任务表列：

| 列 | 含义 |
|---|---|
| `task_id` | CL 任务号，唯一 |
| `sku` / `asin` / `country` | 与第 5.3 节相同 |
| `trigger` | `listed_aplus` 或 `image_update` |
| `actions` | JSON 数组 |
| `images` | JSON 数组 |
| `status` | `待处理` / `处理中` / `成功` / `失败` |
| `attempt` | 本机已执行次数 |
| `error_code` / `error_msg` | 最近一次失败。成功时为空 |
| `site_image_status` | `未执行` / `成功` / `失败` |
| `worker_id` | 领取该行的本机标识 |
| `updated_at` | 最后一次变更时间 |

表所在库和表名由 CL 落库时确定。本机只按上述列读写。连接信息放在本机配置，不写入仓库。未经点名，不写正式站库。

上架页的「站点图上传状态」读 `site_image_status`。CL 也可以在行变成 `成功` 或 `失败` 时，把该值同步到帖子字段。

未采用的通道：

- 共享 Excel：文件被打开就写不回，触发也不实时。只保留现有导入窗口做联调。
- CL 拉取/回写 HTTP：列与上面相同。仅当本机不能持有数据库账号时，再改成 CL 提供拉取和回写接口，本机不直连库。
- CL 调用本机 HTTP：店铺电脑的 `127.0.0.1` 从 CL 服务器不可达。

### 5.2 CL 何时触发

| 触发 | 条件 | 本程序执行 |
|---|---|---|
| 上架完成 | 帖子状态变为 **Listed(A+已上传)** | 传站点图，然后 A+ 提交应用 |
| 图片更新 | 正常图片或 AI 打标图片发生更新 | 只传站点图 |

两种触发使用同一任务结构。用 `actions` 区分要做哪几步，避免靠帖子状态在本程序里再猜一次。

### 5.3 任务正文

```json
{
  "taskId": "CL 侧任务号",
  "sku": "SKU",
  "asin": "ASIN",
  "country": "ES",
  "trigger": "listed_aplus",
  "actions": ["site_images", "aplus"],
  "images": [
    "https://img-cl.aosomcdn.com/对象名.jpg"
  ]
}
```

| 字段 | 规则 |
|---|---|
| `taskId` | CL 任务号。重试、日志、失败通知都带它 |
| `sku` / `asin` | 至少有一个非空。搜索关键词：有 SKU 用 SKU，否则用 ASIN |
| `country` | `US` `CA` `UK` `DE` `FR` `IT` `ES`，或中文国名（美国、加拿大、英国、德国、法国、意大利、西班牙） |
| `trigger` | `listed_aplus` 或 `image_update`。只用于日志和回写，不单独决定步骤 |
| `actions` | `listed_aplus` 必须是 `["site_images", "aplus"]`；`image_update` 必须是 `["site_images"]` |
| `images` | 有 `site_images` 时必填，按展示顺序排列。没有 `site_images` 时可省略 |

`images` 的每一项是 CDN 地址或对象名。

- 以 `http://` 或 `https://` 开头：按该地址下载。
- 否则拼到 `https://img-cl.aosomcdn.com/` 后面再下载。
- 需求里的 `["1.jpg","2.jpg"]` 是顺序示意。实际上游应传示例这种对象名或完整 URL，例如 `https://img-cl.aosomcdn.com/icxjbZdPQoOz4_HddQDT4BP0guvZHi9Lye2ZjuJO9TEaosomicxjbZdPQoOz4_HddQDT4BP0guvZHi9Lye2ZjuJO9TE.jpg`。

同一 `taskId` 再次入队时不新增一行，沿用原记录并重新执行未成功的步骤。

### 5.4 本机队列

在现有 `data/queue.sqlite3` 上扩展，不再只用 Excel 的五个布尔列表达任务。

`task` 保存当前态：

| 列 | 含义 |
|---|---|
| `id` | 本机主键 |
| `task_id` | CL 任务号，唯一 |
| `sku` / `asin` / `country` | 与任务正文相同 |
| `trigger` | 触发类型 |
| `actions` | JSON 数组 |
| `images` | JSON 数组，保留原始值 |
| `status` | 见下表 |
| `attempt` | 已执行次数，从 1 开始 |
| `error_code` / `error_msg` | 最近一次失败 |
| `site_image_status` | 站点图：`未执行` / `成功` / `失败` |
| `aplus_status` | A+：`未执行` / `成功` / `失败` / `不适用` |
| `created_at` / `updated_at` | 本机时间 |

`task_attempt` 追加每次尝试，不覆盖历史：`task_id`、`attempt`、`action`（`site_images` 或 `aplus`）、`error_code`、`error_msg`、`detail`（该位置或该步骤的 JSON）、`created_at`。

任务状态：

| status | 含义 |
|---|---|
| `待处理` | 已入队，尚未开始 |
| `处理中` | 已占用，正在执行 |
| `待重试` | 本次失败且未到上限，等待下一次 |
| `成功` | `actions` 里的步骤都成功 |
| `失败` | 不可重试，或重试达到 N 次仍失败 |

同一时间只跑一个任务。处理中的任务在进程退出后，下次启动改回 `待重试`，避免一直占着队列。

## 6. 传站点图

页面路径：`https://<该国 Seller Central 域名>/imaging/manage`。

操作顺序与后台说明一致：

1. 打开该国 Seller Central，进入图片管理器（管理商品 → 管理图片 → 图片管理器）。程序直接打开 `/imaging/manage`，不点菜单。
2. 在搜索框输入关键词并回车。结果必须恰好 1 个商品，且该行 SKU 或 ASIN 与关键词一致。
3. 滚到该国模块。国家模块用 `ou-section-{国家代码}`。
4. 按 `images` 的顺序逐位上传。
5. 每一位都要看到上传成功标志，才算该位成功。文件路径进入文件框不算成功。

位置对应：

| `images` 下标 | 页面位置 | 说明 |
|---|---|---|
| 0 | MAIN | 主图。后台原文「上传 1-1」 |
| 1–7 | PT01–PT07 | 细节图 / 功能图，按顺序 |
| 8 起 | PT08、PT09… | 页面一开始没有这些位。上一位确认成功后，等待新位置出现再上传 |

FRONT、SIDE、BACK 在页面上存在，本次 `images` 数组没有位置名，不上传。

下载文件放到任务工作目录，文件名按位置主名保存（`MAIN`、`01`、`02`…），扩展名保持 URL 上的扩展名。允许的扩展名：`.gif` `.pjp` `.jfif` `.jpe` `.pjpeg` `.jpeg` `.jpg` `.png`。

整单站点图成功的条件：`images` 里每一张都在对应位置确认成功。任一下载失败、找不到该位置、或该位置未确认成功，则站点图失败，后面的位置不再上传。已成功的位置写入 `task_attempt.detail`。

不把亚马逊稍后的审核通过（前台可检查、格子显示「已显示」）当作 RPA 成功。当前页面成功标志沿用现有判断：该位置出现新上传图片，位置名已固定，且出现等待审核图标。

图片更新再次触发时，仍按 `images` 全量顺序上传。页面上该位已经有图时如何替换，见第 12 节，实现前先确认。

## 7. 提交 A+

仅当 `actions` 含 `aplus` 时执行。站点图失败则不再提交 A+，整单按站点图的错误结束。

后台顺序：

1. 打开该国「管理 A+ 商品描述」。
2. 用同一关键词搜索。命中记录的类型必须是 **EBC**，状态必须是 **草稿**。类型或状态不对，立即失败，不重试。
3. 进入该条商品描述。
4. 点击编辑。
5. 点击「下一步: 应用 ASIN」。已应用的 ASIN 必须是本任务的目标 ASIN（无 ASIN 时用 SKU 在该步能对上的那条）。对不上则立即失败，不重试。
6. 点击「下一步: 查看并提交」。
7. 点击「提交以供批准」，在弹窗中再次点击「提交以供批准」。
8. 页面提示提交成功，且状态变为 **已提交**，A+ 步骤成功。

A+ 页面选择器尚未从已登录页面采集。实现时先核对选择器再写死到常量，不猜第一个匹配元素。

## 8. 回写 CL

CL 上架页增加 **站点图上传状态**，只表示 RPA 传站点图的结果。

| 本程序 `site_image_status` | 回写到 CL |
|---|---|
| `成功` | 成功 |
| `失败` | 失败 |
| 尚未执行完 | 任务行保持 `处理中`，`site_image_status` 保持 `未执行`。上架页不把处理中写成成功或失败 |

A+ 结果不写入这个字段。A+ 失败仍写在同一任务行的 `error_code`、`error_msg` 上，对应步骤为 `aplus`。

回写就是第 5.1 节对同一行的 `UPDATE`。站点图成功或失败都写 `site_image_status`。中间的 `处理中` 不写成上架页的最终结果。

## 9. 异常、重试与失败通知

配置项 `max_attempts`，默认 **3**。次数按整单计算：同一次执行里站点图失败，记 1 次，不再在同一次里接着提交 A+。

每次失败都写入 `task.error_code`、`task.error_msg` 和一条 `task_attempt`。`error_msg` 要能看懂卡在哪一步，超时要写明在等什么、等了多久。

可重试（未到 N 次则 `待重试`，否则 `失败` 并通知）：

| errorCode | 场景 |
|---|---|
| `BROWSER_CONNECT_FAILED` | 连不上该国调试端口，或端口没有可控制页面 |
| `PAGE_NOT_READY` | 图片管理页或 A+ 页面导航、就绪超时 |
| `IMG_DOWNLOAD_FAILED` | 图片下载失败、超时或 HTTP 非成功 |
| `IMG_SEARCH_TIMEOUT` | 搜索结果未在时限内变成 1 条匹配商品 |
| `IMG_SLOT_FAILED` | 某一位找不到、不唯一，或上传后没有成功标志 |
| `APLUS_SEARCH_TIMEOUT` | A+ 搜索结果未在时限内就绪 |
| `APLUS_SUBMIT_FAILED` | 提交过程超时，或提交后状态没有变成已提交 |
| `UNKNOWN` | 未归类异常。`error_msg` 保留异常原文 |

不可重试（立刻 `失败` 并通知）：

| errorCode | 场景 |
|---|---|
| `TASK_INVALID` | 缺任务号、国家无法识别、要传图但 `images` 为空、动作与触发不一致 |
| `IMG_SEARCH_NOT_UNIQUE` | 搜索结果不是 1 件，或唯一一件的 SKU/ASIN 与关键词不一致 |
| `IMG_TYPE_UNSUPPORTED` | 扩展名不在允许列表 |
| `APLUS_NOT_FOUND` | 搜不到 A+ |
| `APLUS_NOT_EBC` | 类型不是 EBC |
| `APLUS_NOT_DRAFT` | 状态不是草稿 |
| `APLUS_ASIN_MISMATCH` | 已应用 ASIN 不是目标 |

达到失败终态时，写回 CL 任务行的内容至少包括：

```json
{
  "taskId": "CL 侧任务号",
  "sku": "SKU",
  "asin": "ASIN",
  "country": "ES",
  "action": "site_images",
  "attempt": 3,
  "errorCode": "IMG_SLOT_FAILED",
  "errorMsg": "PT01 上传未确认成功：等了 30.0 秒"
}
```

`action` 取本次失败的那一步。站点图失败时 CL 将上架页站点图状态记为失败。

## 10. 日志

日志目录用配置 `log_dir`（默认 `logs`），级别用 `log_level`。按天写文件。

每条业务日志带 `taskId`、`sku`、`asin`、`country`、`action`、`attempt`。要记下的节点：入队、开始、图片下载（URL 与本地路径，不写文件内容）、搜索、每一位上传结果、A+ 的每一步、重试原因、终态、回写/通知结果。

异常同时进日志和 `task_attempt`。日志给排查，队列表给重试和通知。页面选择器和 CDP 细节保持在代码常量里，不把整段 HTML 打进日志。

## 11. 与当前代码的差距

已经有的能力可以沿用：

- 七国端口、独立用户目录启动 Chrome、打开 `/imaging/manage`。
- 搜索框、国家模块、按位置名找文件框、`setFileInputFiles`、上传成功标志。
- Excel 导入与 SQLite 队列骨架。队列目前只有 `sku`、`asin`、`country`、`picture`、`A+introduction`、`status`。

尚未接到正式流程、需要按本文补上的部分：

| 项 | 现状 |
|---|---|
| 入口 | `main.py` 只打开 Excel 导入窗口。`pipeline.run` 未被入口调用，其中的准备、提交、回写仍是空函数 |
| 图片来源 | 上传逻辑读本地目录 `图片根目录/关键词/国家代码/`，按文件名 `MAIN`、`01`…`07` 找文件。还不会下载 CDN |
| MAIN | 上传循环里 MAIN 被记为「暂不上传」 |
| PT08 及以后 | 位置表只到 PT07 |
| 成功判定 | 搜索与逐位上传已写，端到端尚未重新跑通；英国国家模块未验证 |
| A+ | 无页面模块 |
| 日志 | `setup_logging` 是空函数 |
| 回写 | `write_status` 是空函数，且目标仍写成 Excel。正式回写改为更新 CL 任务表同一行 |
| CL 任务表 | 尚未轮询、领取或回写 |
| 重试与 errorCode | 无 |

模块边界保持不变：`cdp.py` 只连接和发命令；`imaging_upload.py` 只处理图片管理页；A+ 单独成模块，不写进图片上传文件；队列不操作浏览器。

建议实现顺序：

1. 任务入队、尝试记录、日志（不打开浏览器）。
2. 按 `images` 下载到任务目录。
3. 接上 MAIN，并在 PT07 之后等待后续位置。
4. 站点图终态回写 CL。
5. A+ 提交。
6. 重试上限与失败通知。

## 12. 待确认

1. 图片更新时，该位置已有图片，是逐位覆盖，还是先点后台「全部替换」。当前实现只往文件框放文件，没有替换已有图。
2. `images[0]` 是否始终是主图。本文按后台说明把第 1 张给 MAIN、其后给 PT01 起。若 CL 的数组不包含主图，映射要改。
3. A+ 在「应用 ASIN」一步，任务只有 SKU、没有 ASIN 时，用什么与页面上的已应用 ASIN 比对。
4. CL 任务表的库名和表名。列和状态语义以第 5.1 节为准。
