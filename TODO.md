# TODO

## 固件

- [ ] 真机验证：长短信收齐前重启设备，确认从 SIM 存储恢复
- [ ] 真机验证：模组软复位后无需重启 ESP32 即可恢复收信
- [ ] 真机验证：SIM 拔插或飞行模式后恢复配置并成功收信
- [ ] 真机验证：AT+CMGD 返回错误时删除队列退避并最终清理
- [ ] 真机验证：执行一次保号流程（MIPCALL → MPING → MIPCALL 关闭）
- [ ] 评估：删除队列持久化到 NVS 防掉电回流

## Hub

- [x] Web ESLint 历史错误基线化后加入 CI 必过
- [x] 多版本 mock 设备同时接入测试
- [ ] 真机回归：完成 `docs/operations.md` 真机回归清单全部项目

## 第二阶段：异步 AT I/O（需真机）

- [ ] ADR：比较 FreeRTOS task / Arduino poll / 异步 HTTP
- [ ] `modem_io_core` 落地到真机：Serial1 单持有者 + 命令队列
- [ ] 发送状态机重写：等待 `>` + 写 PDU + Ctrl+Z 期间继续处理 URC
- [ ] 旧 `sendATCommand()` 和直接 UART 访问全部移除

## 第三阶段：生产加固（需真机 + 部署环境）

- [ ] CI 集成 arduino-cli 编译 + release job
- [ ] Per-device secret 替代全局 DEVICE_TOKEN
- [ ] OTA 签名验证
- [ ] 72h 故障 soak 测试
