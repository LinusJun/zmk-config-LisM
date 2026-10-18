# CheerPod 错误 4：安全协商排查版

本版修复了通用设备之间认证提示相互干扰的问题，并把模糊的 `Pairing rejected (4)` 改为按实际拒绝路径显示原因。**尚未确认 CheerPod 配对问题已解决，本版不是兼容性修复完成的承诺。**

仅更新本包 dongle UF2；左右手和摇杆不需要重刷，不要刷 reset。USR1 单击移动、双击确认功能保留。

刷入后在 BLE Inputs 对 CheerPod 点 Retry，失败时直接看页面提示，不要求另开日志采样窗口：

- `Secure Connections required (4)`：本次确实因没有协商安全连接协议而被本地拒绝。
- `Encryption key size ... rejected (4)`：对端给出的加密密钥长度未通过本地检查。
- `Existing bond policy rejected (4)`：现有绑定保护拒绝了本次重新配对，可能是降低安全强度或覆盖受保护的旧绑定；不会自动删旧绑定。
- `Requested security not supported (4)`：对端不能达到本次要求的安全等级。
- `Peer rejected security (4/SMP ...)`：对端明确发回失败，括号内 SMP 数字是原始失败原因，不是 Zephyr 错误枚举。
- `Security requirements mismatch (4); Retry`：仍无足够证据细分原因，不能据此认定是旧协议。
- `Security failed (...); details lost; Retry`：诊断队列没有保留完整信息，不对原因作推断；诊断丢包本身不会中断配对。

保持现有 Secure Connections 限制、16 字节最低密钥长度、MITM 策略和旧绑定保护；不自动降低安全性或清除任何已保存配对。只采集公开配对能力字段和拒绝原因，忽略 PIN、密钥、随机数、确认值等数据，不开启全局 SMP DEBUG。

如果确认对端只能使用旧式配对，下一步需要单独评估并经确认后增加仅限所选通用外设的兼容选项，不能直接放宽键盘分体与电脑连接的安全策略。
