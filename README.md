# 社区居家养老服务系统 · 课程验收版

Python 标准库 HTTP 服务、SQLite、原生 HTML/CSS/JavaScript。保留原有四类角色，老人账号仅操作本人档案。课程版源码与原工程分开，原压缩包及旧 APK 保留。旧 APK 属于旧版客户端，已弃用作本版验收入口；本版通过响应式网页验收，未重建 Android 包。

## 启动

使用 Python 3.12 或更新版本。在本目录打开终端：

```powershell
python -m pip install -r requirements.txt
python server.py
```

访问 [本地页面](http://127.0.0.1:8080)。也可双击 `start.bat`；脚本不会下载或重装 Python。默认仅监听本机。启动执行增量迁移，**不要使用 `--reset`**。首次迁移前备份数据与附件；多次启动不应清空业务历史。`openpyxl` 和 `reportlab` 用于 Excel/PDF 导出。公网部署、HTTPS 与真实生产安全验收另行配置和验证。

| 角色 | 演示账号 | 初始密码 |
|---|---|---|
| 管理员 | admin | Admin123! |
| 老人 | elder | Elder123! |
| 服务人员 | provider | Provider123! |
| 游客 | guest | Guest123! |

以上为演示账号，已有数据库的账号修改以实际数据为准。登录后退出再切换账号。演示样本不代表真实业务规模。

仓库中的 `elderly_care.db` 由内置样例生成，用作自动化测试的基准。日常试用可执行 `python server.py --data-dir data`，将运行数据写入单独的目录。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -t . -v
```

自动化测试使用独立临时库。冒烟测试会写入订单并可能创建明确标记的测试资质，只对隔离服务器运行：

```powershell
python server.py --data-dir C:\care-acceptance-demo --port 8081
python api_smoke_test.py --base-url http://127.0.0.1:8081 --confirm-isolated
```

`--data-dir` 使用独立目录中的 `elderly_care.db` 和 `uploads`，首次创建带演示样本的测试库。冒烟脚本分别登录管理员、老人、人员，携带 Bearer token，核对人员区域与服务类型；有资质要求时经管理员审核测试资质；按预约、派单、出发、到达、完成、记录、管理员审核、老人评价执行。可通过 `CARE_ADMIN_PASSWORD`、`CARE_ELDER_PASSWORD`、`CARE_PROVIDER_PASSWORD` 环境变量指定已修改的密码。脚本拒绝公网 URL、代理和重定向，无默认远程地址。

## 数据保护与容量工具

**先停止服务和所有数据库/附件写入程序。** SQLite 事务与磁盘附件在没有统一写入锁时无法形成在线一致快照；本工具只承诺离线一致性，要求明确传入 `--server-stopped`。这不是停止服务的命令，传参前必须实际停机。

```powershell
python -m tools.operations backup --source . --destination C:\care-backups\before-upgrade --server-stopped
python -m tools.operations verify --snapshot C:\care-backups\before-upgrade
python -m tools.operations restore --snapshot C:\care-backups\before-upgrade --target C:\care-restored --server-stopped
```

备份用只读源连接和 `sqlite3.backup` 保存数据库，同时复制整个 `uploads`（体检及业务附件），生成大小和 SHA-256 清单，验证 SQLite 完整性、外键及附件复制前后内容。恢复先校验全部内容，再替换目标，默认拒绝非空目标。附加 `--replace` 才允许替换，原目标整个目录保留在同级 `*-before-restore-*` 路径；恢复失败尝试回滚目录重命名。恢复目标为数据目录，不要指定项目源码目录。校验和检测损坏，不是防篡改数字签名。

生成器只向**不存在的新文件**写合成数据，从 `--template`（默认随包库）只读复制表结构，不读取业务行，不向正常演示库灌数据：

```powershell
python -m tools.generate_data --database C:\care-load\elderly_care.db --elders 50000 --providers 800 --orders 300000 --health-records 300000 --seed 42
python server.py --data-dir C:\care-load --port 8082
python -m tools.load_check --base-url http://127.0.0.1:8082 --username admin --role admin --concurrency 8 --requests 100 --output C:\care-load\load-report.json
```

用 `--template` 可指定已迁移的隔离库以复制最新结构。生成器还生成关联派单、记录与评价；生成行均为课程合成数据，固定种子可重复。已有库启动只执行迁移，不再补写演示业务样本。负载工具提示输入密码，仅执行角色鉴权后的 GET 请求，报告机器、数据量、请求数、并发、持续时间、时延分位、错误及服务端可见锁错误。它测量本地读请求，不代表写压力、后台推送或真实在线用户。**2000 人同时在线仍是待验证目标**。

## 能力边界与验收

站内通知以前台轮询提供，不承诺锁屏或后台推送。个人健康阈值属于输入和提醒规则，不提供医疗诊断。外部短信、微信、联系人回执及智能设备未配置真实服务，保留未配置状态；设备导入和联系人确认明确标注课程模拟，不代表真实发送或硬件接入。

需求逐条操作、统计口径及验证边界见 [验收操作](docs/验收操作.md)、[改造方案](docs/design.md)、[接口约定](docs/api-contract.md)、[本次验证记录](docs/本次验证记录.md)。健康历史至少保留五年，老人、服务及应急历史永久保留；本版不自动清除业务历史，备份由管理人员定期执行并离线保管。

可选浏览器验证需 Node.js 和 Playwright：先执行 `npm install --no-save playwright`、`npx playwright install chromium`。启动 `python -u tests/browser_server.py` 获取临时服务器地址，将其设为 `CARE_BROWSER_URL`，再运行 `node tests/browser-acceptance.cjs`。使用本机浏览器时以 `CARE_BROWSER_EXECUTABLE` 指定可执行文件；`CARE_BROWSER_OUTPUT` 可指定截图和结果目录。该服务器仅修改临时副本。测试结束可向其 `/__test_shutdown` 发送带 `X-Test-Secret` 启动随机值的 POST 关闭，或在终端停止。浏览器测试检查角色切换、页面访问、表单持久化、通知与手机登录布局，不代替全部功能的人工验收。
