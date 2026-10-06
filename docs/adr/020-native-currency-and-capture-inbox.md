# ADR-020：原币资金与持久输入确认箱

日期：2026-10-06；状态：接受；适用 Android 0.3 beta / Windows 1.1 rc。

金额使用固定 ISO 4217 最小单位整数，支持具有数值精度的币种；CNY=2、JPY=0、KWD=3、CLF=4。账户币种创建后固定，转账保存两端实际金额/币种，同币种须相等。手续费另记支出，退款须与原支出币种一致。原币分录与整数范围校验是财务事实，估值不改分录。

估值通过精确有理数换算，使用手工报价（1 原币单位相当于多少 CNY），按记录日取当天或之前最近报价。报表显示币种可选；只在最终展示最小单位处四舍五入，半值远离零。缺失报价使汇总显示不完整，完整报告返回明确错误，不伪造零金额。估值汇总使用任意精度整数，原币事件/余额仍受原有 int64 边界保护。

Schema 0001 保持不可变。0002 原子迁移保留旧 CNY UUID、分录、版本、审计、回执和变更序号，对旧转账补相等 CNY 到账字段。迁移前保留 SQLite 快照；旧备份先校验原始摘要，再在独立恢复目录升级，不改来源。老版本无法读取 Schema 2，使用新版双端备份迁移。

captured_inputs 保存通知/OCR/语音原文、建议 JSON、源事件摘要、待确认/已保存/已忽略状态和版本。更新同一通知只更新待确认草稿；已确认/忽略不会重新入账。退款等不同事件类型使用独立摘要；来源程序换通知标识可能造成两条草稿，需用户核对。OCR 同一图片摘要归并，并保持原图元数据。捕获、忽略不写资金；确认在同一资金事务里创建流水、附件和状态变更，有幂等回执和并发版本校验。

Android NotificationListenerService 仅绑定系统授权、显式启用且勾选的应用，无 SMS/Accessibility 接口。原生语音先检查服务与运行时麦克风权限；API 31 本机识别优先，普通服务先提示可能联网，无能力或失败时回退文字。语音音频不保存。

OCR 使用 bundled ML Kit Chinese 16.0.1，含中文及拉丁模型，首次断网可用。SDK 有独立第三方条款与诊断联网行为；不打包冒称为 MIT 的模型。移除启动初始化 Provider，先披露再按需初始化，并且每进程只初始化一次。图片和识别文字在设备处理，不自动转交 AI。原图进入完整备份。

APK 帮助由审阅 Markdown 与真实合成测试截图生成单文件 HTML，无 JavaScript、文件/内容访问或网络加载。构建与 CI 检查生成结果一致，Windows 配套提供同一离线手册。自动识别效果和实体手机服务可用性需设备实测；合成模拟器证据不替代真机认证。

参考：
- https://www.six-group.com/en/products-services/financial-information/data-standards.html
- https://developers.google.com/ml-kit/vision/text-recognition/v2/android
- https://developers.google.com/ml-kit/terms
- https://developers.google.com/ml-kit/android-data-disclosure
- https://developer.android.com/reference/android/speech/SpeechRecognizer
- https://developer.android.com/reference/android/service/notification/NotificationListenerService
