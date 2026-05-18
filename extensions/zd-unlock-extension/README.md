# CS Bot ZD Unlock Extension

Chrome 扩展后台轮询 `csbot` 的解锁命令，并从白名单网络直接请求 9site 解锁接口。

## 安装

1. 打开 Chrome `chrome://extensions/`
2. 开启右上角 `Developer mode`
3. 点击 `Load unpacked`
4. 选择本目录：`extensions/zd-unlock-extension`
5. 点击扩展图标，确认状态为启用

## 使用

扩展安装后不需要打开 9site 标签页。只要 Chrome 运行，扩展会每 30 秒唤醒一次，并使用长轮询等待命令。

弹窗里可以修改：

- `Bot Base`: `https://arcshelp.zeabur.app`
- `Command Secret`
- `Unlock Value`
- 9site 请求头 JSON

## 测试

1. 群内发送：`Wesley333 短信解锁`
2. 打开扩展弹窗查看状态
3. 成功时显示 `解锁 wesley333 HTTP 200`

如果状态是 `轮询失败`，说明扩展访问不了 csbot。
如果状态是 `解锁失败` 或非 200，说明 9site 请求头、登录态或白名单网络有问题。
