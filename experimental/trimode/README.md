# LisM 始终 ESB 主从链路实验版

基于 `LinusJun/zmk-config-LisM` 的 `keymap-editor`，基线提交
`140d66bc5db8d2508af70f40aa3ecbe2656f0d25`。

本目录已经包含实际 ZMK 主从 transport 集成和可构建的三个 UF2 目标，
不再只有主机端状态机或独立射频测试程序。**尚未上板测试，不是已验证成品。**

## 当前输出和主从链路

| 主设备对电脑输出 | 左右手输入数据 | 当前实现 |
| --- | --- | --- |
| USB HID | 加密 ESB | 已接入完整主设备、左右手固件 |
| BLE 1～5 | 加密 ESB | 已接入；不随主机配置切回 BLE split |
| ESB → 新 XIAO USB 接收器 | 计划保持 ESB | **第三输出端点、接收器和屏幕入口尚未实现，本包不包含它们** |

**BLE 控制连接仍会保留。**它用于原来的自动登记、身份确认、会话密钥交换、
充电遥测及连接生命周期；现有外部 BLE 键鼠接收和 BLE 电脑连接也保留。
键盘矩阵、指针输入、电量事件和通过 ESB transport 下发的命令走 ESB。
这还不是“登记后关闭左右手全部 BLE”的版本，不能据此宣称比 BLE 省电。

## 已接入的代码

- `src/esb/radio.c`：SoftDevice Controller/MPSL 共享射频。主设备请求 4ms、
  左右手请求 3ms 时隙，提前 300µs 结束；调度器可以拒绝请求。
  高优先级回调不调用内核队列、加密或日志。RX 在时隙边界进入原子环形缓冲，
  协作线程处理协议。固定 RF 频道默认 37，可配置；没有自动跳频。
- `cmake/lism_esb.cmake`：对严格校验 SHA256 的 Nordic ESB 源码生成补丁，
  不改 SDK 检出目录。ESB 不注册/关闭 RADIO 中断；MPSL 始终拥有 RADIO、
  TIMER0、RTC0。构建脚本还从最终 ELF 验证三个中断向量。
- `src/esb/protocol.c`：AES-128-CCM、8 字节认证标签，明确编码的协议头和
  事件字段，不直接发送 C 结构体。头部也参与认证。每次加密 BLE 连接建立后，
  为已经登记、完成安全初始化的左右手生成新的随机会话密钥和会话号。
  密钥不编进 UF2、不写日志、不持久化；跨重连/重启拒绝旧会话。
- `src/esb/central.c` / `peripheral.c`：应用层确认、不可变包重试、重复抑制、
  批量事件、反向命令确认和初始按键状态快照。硬件 ESB ACK 不等于事件已入队，
  只有应用确认才推进发送序号；队列拥塞时继续同一包，已入队的批量前缀不重放。
- 接收数据进入原来的单一 split worker，复用 A/B 排他、代际检查、已应用按键位图、
  指针按钮释放和 input marker 清理栅栏。BLE 键盘数据入口被抑制，没有悄悄退回
  BLE 按键的机制；独立摇杆的原 BLE 通道保留。
- 失联租约为 1500ms；队列溢出或超时触发原来的断连/清键流程。相对指针事件
  超龄会终止并清理会话，避免把部分过期的输入同步组留下来。不会删除登记或绑定。

独立 `coex_probe/` 仍只是早期测试程序。`transition.c` 是未接入第三输出端点的
协调器原型；它的测试通过不代表第三输出模式已经完成。

## 构建

默认 `config/west.yml` 和常规构建矩阵不启用 ESB。实验依赖在
`integration/west.yml` 中固定了 ZMK、Zephyr、Nordic SDK 和 nrfxlib 提交。

在空工作目录初始化实验依赖：

```sh
mkdir -p /path/to/esb-workspace/esb-manifest
cp /path/to/zmk-config-LisM/experimental/trimode/integration/west.yml /path/to/esb-workspace/esb-manifest/
cd /path/to/esb-workspace
west init -l esb-manifest
west update
```

使用 Zephyr SDK，并安装该版本 Zephyr 的 Python 构建依赖后：

```sh
python /path/to/zmk-config-LisM/tools/build_esb.py --workspace /path/to/esb-workspace --target dongle
python /path/to/zmk-config-LisM/tools/build_esb.py --workspace /path/to/esb-workspace --target left
python /path/to/zmk-config-LisM/tools/build_esb.py --workspace /path/to/esb-workspace --target right
```

脚本沿用 `ci_firmware.py` 的完整功能参数和构建检查，输出在本目录的
`artifacts/`，没有任何自动刷写、清除设置或发布动作。
`--modules` 供本地已准备依赖的离线验证使用。
`--local-minimal-libc` 仅供本次 Debian GNU Arm 工具链环境：其 newlib 不支持
Zephyr 要求的锁接口，因此显式使用 minimal libc 和 `ESTALE=116` 兼容定义。
发布到真实硬件前，仍应以标准 Zephyr SDK 重新构建并上板验证。

`CONFIG_NRF_SECURITY=n` 避开不兼容的 NCS crypto 后端，保留 Zephyr 的
Mbed TLS PSA、Bluetooth SMP 和现有每设备安全策略；它不是关闭蓝牙加密。
ESB CCM 使用该 Zephyr 清单已固定版本的 TinyCrypt，不依赖运行时加密堆分配。

## 刷写和实机验证

本包三个 UF2 分别用于带屏主设备、左手和右手。两套 LisM 共用同一左手 UF2、
同一右手 UF2；角色槽位通过原有加密登记自动分配。必须同步升级三个角色，
旧版 BLE-only 左右手不能为这个 ESB 主设备提供按键数据。

不需要刷 settings_reset。本版本不主动删除旧配对；已有绑定能否直接复用仍需
实机确认。首次登记保持原来流程：A 左手、A 右手；关闭 A，待主设备确认断开，
再开启 B 左手、B 右手。不要同时登记两个同角色的新设备。

建议先完成以下上板验证，再作为日常固件使用：

1. USB 模式测试左右全部按键、轨迹球、鼠标按钮、电量和屏幕。
2. 切换 BLE 1～5，确认左右手继续输入，主设备不重启，外部 BLE 键鼠仍可工作。
3. 按住按键/鼠标按钮后关掉对应手，确认电脑端释放；再切换另一套 LisM，
   确认旧包或旧释放不影响新设备。
4. 主设备/左右手分别重启，测试自动重连、重新交换会话和两套登记保留。
5. 最后测 Wi-Fi 干扰、持续轨迹球负载、时隙拒绝、长时间运行和电流。
   目前没有延迟、耗电或 RF 稳定性的实测数字。

## 软件验证

```sh
python tests/lism_esb_transport_test.py --tinycrypt /path/to/esb-workspace/modules/crypto/tinycrypt
python tests/lism_esb_peripheral_test.py --tinycrypt /path/to/esb-workspace/modules/crypto/tinycrypt
```

测试直接编译实际接收端/发送端代码和 CCM 实现，使用有限容量的模拟队列，覆盖
参考加密向量、篡改、身份准入、碎片化密钥读取、丢确认、批量部分入队、重复命令、
旧会话、A/B 排他、队列溢出、指针过期和断线。另运行原有自动登记、代际/清理栅栏、
安全配置回归。ASan/UBSan 开启；因执行环境限制，仅关闭 LeakSanitizer。
这些测试不模拟真实射频或 MPSL 时序，不能替代上板测试。
