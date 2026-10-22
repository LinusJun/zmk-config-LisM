# LisM ESB v5.1 两套键盘 / 双 Dongle 测试版

基于 v5.0.1 双 Dongle 分支，新分支为 `codex/esb-v51-two-sets-dual-dongle`。
两套 LisM 都能选择 A/B，每台 Dongle 同时只接受一套键盘。原双 Dongle 分支保留。

**这是软件编译和原生模型验证过的测试版，尚未用两套实体键盘和两台实体接收器完成射频实测。**

## 下载及刷写

下载本分支 `firmware/LisM_ESB_v51_Two_Sets_Dual_Dongle_TEST.zip`，解压后使用“固件”目录。

| 设备 | 固件文件 |
| --- | --- |
| 第 1 套左手 | `LisM_ESBv51_TEST_Set1_Left_4waystick.uf2` |
| 第 1 套右手 | `LisM_ESBv51_TEST_Set1_Right_Trackball_BLE5_Hub.uf2` |
| 第 2 套左手 | `LisM_ESBv51_TEST_Set2_Left_4waystick.uf2` |
| 第 2 套右手 | `LisM_ESBv51_TEST_Set2_Right_Trackball_BLE5_Hub.uf2` |
| Dongle A / Prospector 1.69 | `LisM_ESBv51_TEST_Dongle_A_Prospector169.uf2` |
| Dongle B / XIAO 1.47 触摸屏 | `LisM_ESBv51_TEST_Dongle_B_XIAO147_Touch.uf2` |

两套各自的左右手必须刷相同 Set 编号。两台 Dongle 使用对应屏幕的 A/B 固件，接收器同时支持两套，无需刷成 Set1 或 Set2。
**不能把两套都刷成 Set1；不能混用 v5.0.x 的左右手或接收器。**
包内 `settings_reset_XIAO_nRF52840.uf2` 是可选清设置固件；只有需要清空旧 BLE 绑定/配置时使用。它会清除该设备保存的配对和设置，使用后再刷回对应角色。正常升级不必先清设置。

## 首次使用与自动连接

1. 刷好六台设备，把键盘分别标记为 SET 1、SET 2。
2. 打开任一台或两台 Dongle，再打开需要用的键盘左右手，松开按键和鼠标按钮，等待启动搜索与提交完成。
3. 没有保存过站点时，SET 1 先找 A，SET 2 先找 B；都能继续寻找另一台。以前保存过的站点优先。
4. **只有 A 开机：任一套开机都能自动寻找 A 并连接。只有 B 开机也一样。**
5. 两台都开时，各套可连接空闲的 A/B。同一空闲接收器被同时争用时，只允许一个有效 PREPARE 获得独占权，另一套会继续寻找另一台，或回退自己的直连 BLE。
6. 已有一套连接时，另一套不能抢占这台接收器；探测不会改变原套的按键状态或续租。两台都不可用时，右手回退直连 BLE。

占用的接收器采用静默拒绝，右手按搜索窗口换站；没有显式 BUSY 回复。搜索和事务包含释放屏障，不是瞬时连接，按住按键会推迟切换。原套离线后，接收器约 2.5 秒租约到期，封锁输入、尝试释放，并在约 0.6 秒后自动重启；之后可接受另一套。上述数值是软件时限，不是实测端到端保证。

正常 Dongle 数据路径仍是：

- 左手按键 / 摇杆 → ESB → 当前 Dongle。
- 右手按键 / 轨迹球 → ESB → 当前 Dongle。
- 右手只协调选择与左右手提交，不代传 Dongle 模式的左手输入。
- 直连 BLE 时：左手 → 私有 ESB → 本套右手 → 主机 BLE。

两套的私有链路地址、站点物理 pipe、帧标识均独立。右手 BLE 广播名称分别为 `LisM Set1` / `LisM Set2`，各自保留 5 个主机槽位。接收器界面显示当前 `SET 1` / `SET 2` 或 `FREE`。

## 手动选择

沿用原键表 Device 组合键：按住右手 Device 键约 200ms，再按：

- `F1`：选择 Dongle A。
- `F2`：选择 Dongle B。
- `H`：本套直连 BLE。
- `;`：返回 Dongle 自动搜索。

两套都支持相同操作。选择已占用的接收器不会挤掉另一套，会经过搜索/超时处理寻找可用目标。健康连接保持当前站点；接收器晚开机不会自动抢走正在使用的直连 BLE，需手动选择返回 Dongle。

## 这个测试版的接管边界

每次接收器启动只允许一个 `(Set, right wire boot, authority)` 所有者。只有发现响应本次 receiver boot 后的 FREEZE/PREPARE 能建立它；COMMIT 必须匹配 authority、epoch、目标站点及启动标识。

失效后会先不可逆退休，递增命令/指针代次，阻止新输入，主动尝试释放旧按键和按钮，再执行普通应用重启。**换套接管会让 USB、接收器 BLE 和屏幕短暂重连。不会清除保存的配对和设置，也不会主动进入刷机盘。** 中立报告是有限时的尽力发送，不能保证失联主机收到最后一帧；重启是旧 USB/SDC/行为队列与新所有者的隔离边界。

为避免 BLE 显示先恢复导致真正键盘连接反复超时，此版关闭“空闲或重启后接收器自动恢复 BLE 镜像”。原套从 Dongle 切到直连 BLE 时，可继续在原接收器显示；另一套寻找该台时，该显示会让出接收器并先重启。已经处于直连 BLE 的键盘，遇到新开机或重启的空闲接收器，不会自动恢复显示；可手动重新选择 Dongle。两套直连 BLE 的输入功能保留。

## 验证与复现

交付包“验证记录”包含构建配置、DTS、UF2 SHA256、生产源码 fingerprint、原生测试日志和构建审计。关键验证包含：实际四 pipe 收包和定向发现、套号/启动标识/CRC/来源隔离、原子独占、探测不续租、250ms 探测回复失效、owner 命令优先、退休后永不在同 boot 接管，以及实际协调器冷启动仅 A/仅 B/无 Dongle/首次争用流程。

保留原四向摇杆键表、右手轨迹球、5 个 BLE 槽位、XIAO 触摸/输入归属保护，以及 v5.0.1 WPM 显示队列修复。原生测试使用实际 C 函数和 LVGL，队列/时钟/MMIO/主机 I/O 使用模型；它们不能证明实体 RF 时序、丢包表现、功耗、切换延迟或屏幕实测效果。

GitHub Actions 的 7 个独立 target 为 `left_set1`、`right_set1`、`left_set2`、`right_set2`、`dongle_a`、`dongle_b`、`reset`。本地构建入口为：

```sh
python3 tools/build_upstream_esb.py --workspace /path/to/pinned-workspace --target right_set2
```

依赖版本由构建器固定并核对；KEYMAP SHA256 必须与 v5.0.1 一致。
