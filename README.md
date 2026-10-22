# LisM ESB v4.7.2 · 已实测基准

本分支保存一套 LisM 的已实测固件：左手 4waystick、右手轨迹球、原 Prospector 1.69 英寸屏接收器。用户反馈目前功能正常，剩余反馈为信号图标和长层名的显示问题；显示修正另存分支，不混入这个基准。

**本分支没有新增双 Dongle 功能。** 家里／单位两站切换在独立开发分支实现；需要回退时用这里的原始包。

## 下载与刷入

[下载原始已测试三台固件包](firmware/LisM_ESB_v472_TESTED.zip)（打开后点击下载）。三个文件名分别含 `Left`、`Right`、`Prospector169`，对应刷入左手、右手和接收器。

正常升级无需先刷 reset。包内可选 reset 会清空配对，只在确实需要时使用。左手只运行 ESB，UF2 比带 BLE 的右手小，这是预期结果。

## 功能

- Dongle 模式：左手、右手分别通过 ESB 直达接收器，保留 USB 与五个电脑 BLE 配置。
- 键盘直连：左手经 ESB 汇入右手，右手通过 BLE 连接电脑／手机／平板，拥有独立的五个配置。
- 开机优先探测接收器，未找到则回退键盘直连；接收器晚开机不会自动抢回输入。
- 接收器可显示直连模式下的层、WPM、电量和修饰键状态。
- 第三方 BLE 输入默认关闭；开启后重连保存的设备，仅在添加页搜索新设备，连接完成后停止扫描。
- 轨迹球位移积压处理、ESB 输入代际隔离、BLE 广播与扫描互斥、低频 RSSI 与状态图标。
- 保留现有键位和 200 ms 配置。

## 操作

| 按键 | 功能 |
|---|---|
| Device + H | 键盘直接 BLE |
| Device + 分号 | 回到 Dongle |
| Device + N / M / , / . / / | 键盘直连配置 1 / 2 / 3 / 4 / 5 |
| Device + E，`SCREEN_NEXT` | 接收器切换功能页 |

接收器的五个电脑配置继续使用原 Device 层控制，与右手的五个直连配置分别保存。设备页通过左手四向杆导航、Enter 确认。

## 验证与版本边界

原始下载包、依赖版本、UF2 SHA 和生产源码指纹均保留在包及 `verification/`。软件检查 30 项通过；接收器静态 RAM 为 201408 字节（76.83%），不是运行时峰值。

原始已测源码指纹为 `077a7987249e08ed51fed8ba5e6042a13a2d110a06ba4b2ff351396673751c9c`。本次只新增仓库说明、验证记录和下载入口；生产源码不改。

BLE 与 ESB 共存不保证端到端 1 ms。UF2 刷写后自动启动偶发黑屏尚未证实修复，遇到时先单按一次接收器 Reset，不要先清配对。

- [原屏显示修正 v4.7.2.1](../../tree/codex/esb-v4721-prospector-ui)：修复信号显示高度，长层名改小字号 JOYSTICK；仅软件验证。
- [XIAO147 v4.9 TEST](../../tree/codex/xiao147-esb-v49-test)：保留触摸，移植当前接收器功能；1.47 硬件仍待实测。
- [双 Dongle 开发分支](../../tree/codex/esb-v50-dual-dongle)：一套键盘、家里和单位两接收器；新协议 TEST。

## 从源码编译

依赖固定在 `config/west.yml`，构建工具为 `tools/build_upstream_esb.py`。可在本分支的 Actions 手动运行构建，产物与原始实测下载包区分；重新编译的字节不应直接视为当时已刷入的原始文件。

```sh
west init -l --mf config/west.yml config
west update --narrow --fetch-opt=--depth=1
west zephyr-export
python3 config/tools/build_upstream_esb.py --workspace "$PWD" --target dongle
```

其他目标为 `left`、`right`、`reset`。原作者说明保留在 [README.upstream.md](README.upstream.md)，许可证见 [LICENSE](LICENSE)。
