# LisM 右手主机独立蓝牙实验版

从 `codex/lism-runtime-trimode` 分出，基底仍是 `keymap-editor` 的最新屏幕 dongle 功能。
`config/lism.keymap` 的八层、58 个位置和现有组合键未改动。
这是已构建的软件实验版，**尚未上板验证**；编译及主机测试不代表无重启切换、配对和射频已在实机成功。

## 使用方式

右手底排原来的 **F1 与 F2**（物理位置 39/40）在 80ms 内同按，保持约 **1.2 秒**后松开，
再于 **5 秒内**按下并松开右手顶排的一个键。所有层都按物理位置识别，拔掉 dongle 也能操作。
也支持保持 F1/F2 的同时选择；最终会等待右手所有物理键松开才开始切换。

| 顶排原始键位 | 切换目标 | 数据路径 |
| --- | --- | --- |
| Y | 屏幕 dongle | 左、右各自 ESB → dongle → 其原有 USB / BLE 1～5 |
| U | 独立蓝牙 1 | 左 BLE → 右；右 → 电脑 BLE 1 |
| I | 独立蓝牙 2 | 左 BLE → 右；右 → 电脑 BLE 2 |
| O | 独立蓝牙 3 | 左 BLE → 右；右 → 电脑 BLE 3 |
| P | 独立蓝牙 4 | 左 BLE → 右；右 → 电脑 BLE 4 |

短按、单独长按 F1/F2、超过 80ms 才重叠按下、或未到 1.2 秒就松开，仍发送原来的键位。
单独的 F1/F2 需要至多 80ms 的组合判断时间。已进入选择状态后，不按目标键会在 5 秒后退出；
按其他普通键会取消选择并正常发送该键。

独立模式由右手处理完整键位、鼠标组合键和 PAW3222 轨迹球。光标反向、Nav 纵向滚动、
Mark 横向滚动、Device 双轴滚动沿用 dongle 的处理配置。右手对电脑的蓝牙名是 `LisM Right`。
四组独立配置与屏幕 dongle 的配置各自保存。原 Device 层的 BT_SEL 0～3 对应这四组，
BT_SEL 4 在独立模式下没有第五组。正常 Device 层的 USB/BLE 输出选择仍是原 ZMK 行为。

切换不调用重启、蓝牙控制器重启、settings_reset 或批量删除绑定。它会主动断开旧连接，
清理原键位、HID 待发队列和轨迹球累计量，再建立新路径，所以有短暂停顿，**不是零丢键的无缝切换**。
右手至少留出 300ms 清理窗口；左手在右手断开后留出 3 秒重连窗口，再退回 dongle 寻找过程。
电脑的重新连接时间和实际暂停长度需要实测。切换期间请先松开其他按键。

## 首次登记与刷写

升级同包的 dongle、左手、右手三种固件；两套 LisM 使用相同角色文件，无需 settings_reset。
已有 dongle 绑定保留，左手新增独立 BLE identity 1，原 identity 0 继续与 dongle 配对。

1. 先只开启 A 套：左手，再右手；保持与 dongle 连接一段时间，让加密连接完成 ESB 会话及配套地址交换。
2. 关闭 A 套并等待断开，再按左、右的顺序开启 B 套，完成同样登记。不要一起打开同角色的新设备。
3. 每套右手第一次进入独立蓝牙后，在电脑配对 `LisM Right`。换另一组独立配置时重复对应电脑的首次配对。
4. 完成配套后，可以拔掉 dongle，使用同样快捷键选择独立设备。右手保存最后模式，重启后恢复。

只有通过加密、已登记 dongle 连接取得本套配套地址后，右手才接受进入独立模式的请求。
配套尚未取得时会留在 dongle 模式。身份交换只共享左右手地址，不复制蓝牙密钥、不硬编码共享密钥。
A/B 配套绑定到实际登记的左手身份；替换登记不会继承旧左手的独立身份。
左手的独立广播使用专用 UUID，不会被 dongle 当成第二套新的左手登记。

独立模式只接管这一套左右手及右手轨迹球；屏幕、外部 BLE 键鼠接入、独立 JP19/CirclePad 等仍属于 dongle。
以前讨论的“dongle 第三输出 ESB → 另一块 USB 接收器”不在本次实现内。

## 构建与验证

依赖和初始化方式见 [ESB 实验依赖](../trimode/README.md#构建)。默认清单和普通构建矩阵不启用本功能。
使用同一工作区、Zephyr SDK 和已安装的 Zephyr Python 依赖运行：

```sh
python tools/build_standalone.py --workspace /path/to/esb-workspace --target dongle
python tools/build_standalone.py --workspace /path/to/esb-workspace --target left
python tools/build_standalone.py --workspace /path/to/esb-workspace --target right
python tests/lism_standalone_test.py
python tests/lism_standalone_worker_test.py
python tests/lism_companion_test.py
python tests/lism_esb_standalone_test.py --tinycrypt /path/to/esb-workspace/modules/crypto/tinycrypt
```

构建输出在 `artifacts/`，脚本检查固件角色、四组配置、必要链接符号以及 MPSL 对 RADIO/TIMER0/RTC0 的中断所有权。
本次本地工具链需要显式 `--local-minimal-libc`，其环境限制沿用 ESB 实验版；标准 Zephyr SDK 的构建仍待另行验证。
主机测试直接运行实际 C 状态机、生成后的 BLE 接收/清理函数、加密配套服务和启用独立模式保护的 ESB 代码。
ASan/UBSan 开启，执行环境仅关闭 LeakSanitizer。测试包括饱和队列、断连重试、旧释放包隔离、按键释放屏障、
HID/input 清理栅栏、模式选择超时、A/B 配套隔离和存储失败；它们不模拟真实 BLE/MPSL 射频时序。

上板还需验证：两套分别登记、USB/BLE dongle 模式、拔掉 dongle 后四组独立蓝牙、反复往返切换、
轨迹球各层滚动、左/右单独掉电重连、按键/鼠标按钮按住时断线，以及持续运动与 Wi-Fi 干扰下的长期运行。
目前没有延迟、耗电或稳定性的实测数字。
