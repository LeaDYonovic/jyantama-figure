# Mortal 网页人机验证说明

## 程序实际做了什么

Mortal 分析页使用 Cloudflare Turnstile。提交表单前，网页自己的验证控件会在
`cf-turnstile-response` 隐藏字段中写入一次性令牌。程序只读取“令牌是否已经存在”，
不会生成、复制、伪造或保存令牌。

默认流程如下：

1. 打开一个持久的可见 Chrome 窗口并载入 Mortal 分析页；
2. 填入牌谱链接、模型和结果页选项；
3. 等待网页正常签发 Turnstile 令牌；
4. 如果页面显示人机验证，由用户在该 Chrome 窗口中手动完成；
5. 令牌出现后提交表单，等待分析结果；
6. 下一局至少等待 `submit_interval` 秒；连续失败则等待 `submit_cooldown` 秒。

相关实现集中在：

- `batchmortal/verification.py`：令牌状态、验证拒绝和限流分类；
- `batchmortal/browser.py`：可中止等待、表单提交和失败退避；
- `main.py`：`--verification-timeout`、提交间隔和冷却配置；
- `desktop.py`：可见/后台 Chrome 的用户提示。

## 推荐设置

Windows 桌面版建议取消勾选“后台运行 Chrome”。分析时保留弹出的 Chrome；如果出现
验证提示，就在窗口中正常完成一次。不要同时开启多个分析程序。

推荐命令行配置：

```yaml
headless: false
verification_timeout: 180
retry: 1
submit_interval: 10
submit_cooldown: 120
prewarm_standby: false
unsafe_parallel_review: false
```

## 故障处理

- 一直没有令牌：关闭后台模式，确认 Chrome 窗口没有被遮挡，然后手动完成验证；
- 提示令牌过期或被拒绝：停止本批次，稍后从断点继续；不要连续刷新；
- 提示限流：停止或等待至少几分钟，之后减少单次分析数量；
- Chrome 被关闭：重新开始即可，已经成功写入的牌谱会自动跳过；
- 仍反复验证：不要更换 IP、伪造浏览器指纹或使用打码服务，应降低频率或改为本地部署 Mortal。

本项目不提供验证码绕过、令牌伪造或第三方打码功能。验证策略由网站决定，无法保证每次
都能自动通过；程序能保证的是在需要人工操作时给出明确提示，并安全保存已完成结果。
