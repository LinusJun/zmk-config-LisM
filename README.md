# Prospector169 v4.7.2.1 显示修正版

修正原屏 L/R 与蓝牙信号图标裁切；JOYSTICK 层名使用适合的字体与尺寸。信号图标位置保持原样。

本分支为已完成软件构建与验证的 TEST 快照；**本版 UI／XIAO147 移植未做实机确认**。用户已经实测正常的基础版保存在 [v4.7.2 基准分支](https://github.com/LinusJun/zmk-config-LisM/tree/codex/esb-v472-tested-baseline)。

[下载原始完整固件包](firmware/LisM_Prospector169_v4721_TEST.zip?raw=true)。ZIP 保持原始字节不变，内含刷写说明与校验记录。接收器更新此版时，左右手继续使用 v4.7.2；不能与下述 v5.0 新协议混刷。

- 原屏版只刷对应 Prospector169 接收器，不要刷到 XIAO147。
- XIAO147 使用专用 JD9853A 320×172 固定横屏与 AXS5106L 触摸驱动。第三方蓝牙默认关闭，仅在设备页主动开启时使用。
- 仍保留 5 个电脑 BLE 配置位、Device＋H 键盘直连、Device＋分号回 Dongle。显示修正不改按键和 200ms 设置。

两个接收器在家／单位共用一套键盘的新功能，见独立 [v5.0 双 Dongle 测试分支](https://github.com/LinusJun/zmk-config-LisM/tree/codex/esb-v50-dual-dongle)。它是新协议，需要同时更新左右手和两台接收器。

原项目说明见 [README.upstream.md](README.upstream.md)。

UF2 刷写后自动启动偶发黑屏的问题尚未证明修复。若刷完黑屏或不能输入，先单按一次接收器 Reset；不要先清配对。
