# plan.md：Amazon 图片上传工具

## 1. 项目目标
在 Windows 本机，把 Excel 中的商品任务，通过本机已登录 Chrome，
按国家上传图片到 Seller Central 图片管理页，并回写状态。

## 2. 链条骨架
Excel → SQLite 队列 → 本地图片目录 → 本机 Chrome(CDP)
      → Seller Central 图片管理页 → 回写状态

## 3. 当前进度

### 已完成
- [x] 阶段 0 骨架（入口、配置、日志、空模块）
- [x] Excel 读取与 SQLite 表结构
- [x] sku、asin 拆成两列；队列字段：sku / asin / country / market / picture / A+introduction / status（后两列为布尔）
- [x] 方式一导入：对话框或拖入单个 .xlsx；多个文件整批拒绝；取消不提示；占用/损坏/无权限说明原因
- [x] 七国端口 + Seller Central 首页映射
- [x] CDP 连接层（连接本机调试 Chrome，西班牙 9228 可列出标签并完成控制检查）
- [x] 独立用户目录启动 Chrome（不调试默认用户目录）
- [x] 按国家导航到 `/imaging/manage`
- [x] 等待元素机制（已消除「找不到搜索框」；短超时会抛错而非卡住）

### 进行中
- [ ] 上传位按各自位置名区分（MAIN、PT01 各匹配到 1 个文件框）（待验证）
- [ ] 搜索 + 逐位上传 + 返回每个位置的结果
- [ ] 搜索完成与上传成功的判断
- [ ] 01 → PT01 端到端验证（最后一次改代码后无新运行结果）

### 未开始
- [ ] 补齐 FRNT / SIDE / BACK / PS01 等额外位置的规则
- [ ] 多国验证（尤其英国的国家模块代码）
- [ ] 状态回写
- [ ] 数据从cl传入，而不是excel
- [ ] A+ 模块


## 4. 模块边界
| 文件 | 职责 | 不许做 |
|---|---|---|
| main.py | 入口、单实例 | 不写业务 |
| cdp.py | 连接、发 CDP 命令 | 不写页面逻辑 |
| imaging_upload.py | 搜索、下滑、上传 | 不写 Excel / 回写 |
| excel_io.py | 读 Excel、得到关键词 | 不碰浏览器 |
| storage.py | SQLite 队列 | 不碰浏览器 |
| aplus_upload.py | A+ 上传 | 不复用 imaging_upload 的业务规则 |
## 5. 技术约束
- 语言：Python
- 浏览器控制：只用 CDP，不用 Selenium / Playwright
- 连接对象：本机已登录 Chrome，复用标签页
- 上传方式：DOM.setFileInputFiles，禁止点击系统文件框
- 不引入新框架

## 6. 业务规则
- 关键词：SKU 存在用 SKU，否则 ASIN
- 图片目录：`D:\aosom\data\picture\<国家>\<sku>\<站点>\` 
- 位置映射：图片名01对应第一个框，图片02对应第二个框，依次类推，将所有图片依次顺序读入
- 每位置只放一张
- 缺号不前移。没有 02 时，03 仍进第三个框。
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
