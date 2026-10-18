# 日常改键分支

仓库：LinusJun/zmk-config-LisM；分支：`keymap-editor`；文件：`config/lism.keymap`。
这不是旧main构建：完整集成源码以2026-10-17层名配色交付为基线，保留双指、空鼠、
USR菜单、两套LisM、通用BLE键鼠、充电显示和灰调/Bright配色逻辑。
同步及编译不代表这些功能又完成了一轮实机验证；既有硬件限制仍然存在。

## 在网页中使用

在 https://nickcoutsos.github.io/keymap-editor/ 选择GitHub来源，授权自己的仓库，
选择上述分支和LisM布局。58个位置包含42个键盘键、JP19六项、CirclePad六项、
4waystick四方向（位置54..57，依次上/右/下/左）。

可修改每层各位置的绑定。不要删除、增加或重排层；轨迹球模式也依赖既有层编号。
NAV的`stick_scroll`是自定义连续滚动行为；若网页不能识别，不要删除其定义。
宏、Combo、Homerow默认参数、监听器等不属于本分支的普通改键范围。
守卫会忽略排版/注释变化，但拒绝这些配置丢失；拒绝时不发布缺功能的新包。

保存会提交到本分支，自动触发`Keymap Editor - complete firmware`。
等待regression、六目标firmware和package都绿色，从该次运行的Artifacts下载
`LisM-complete-firmware`。不要下载旧分支或其他旧工作流的包。
保护检查保证固定配置没有静默丢失，不保证任何新绑定都可用；拼错行为/参数会编译失败。

## 更新固件

普通键位修改一般只需更新dongle的`lism_dongle_xiao147.uf2`。
如果曾通过ZMK Studio改过键，已保存的运行时映射可能覆盖编译映射，须先核对，
不要为改键盲目刷settings reset。
仅当更新说明要求外围逻辑升级时才刷左右手；两套共享相同左手/右手UF2。
完整包包含JP19、CirclePad及单独的`reset_危险备用_默认不要刷`目录。
**普通升级绝不刷reset；reset会清除刷入目标的设置与配对。**

## 功能与构建边界

电脑USB/BLE输出、通用标准BLE键鼠、两套键盘自动登记等由独立程序控制，
不会因为仅修改每层绑定而修改源程序或清除配对。
并非支持任意蓝牙设备：蓝牙Classic、厂家私有多点协议和手柄输出不在支持范围内。
充电显示是保守的软件观测/推断，不是充电安全认证，也不修改硬件充电保护。
依赖以已用源文件核对后的固定提交构建；兼容配对Kconfig生成步骤纳入CI。
工作流仅有contents:read权限，不会自动向分支写回绘图或修改用户的keymap。
