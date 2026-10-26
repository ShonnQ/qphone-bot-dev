# 壳基线存档

存档目的：老板讲「喺某一版基础加」，要即刻搵返嗰一版；`/tmp` 会清，所以入仓库。

| 版本 | 文件 | 特征 |
|------|------|------|
| v1.4（暗色） | `shell-dark-v1.4.html` | 默认暗黑 token；实况窗 `aspect-ratio:720/1600` + `object-fit:cover`（会裁左右，见 v1.5 修正）；遥测/会话头写死 `10532` |
| v1.5（亮色） | 现役 `shell.html` | 默认亮色 token（`:root`）+ `html[data-theme="dark"]` 返暗色；实况 `aspect-ratio:9/16` + `object-fit:contain`；`adb`/`chatSub`/`liveSize` 跟 target 与真实帧变 |

## 截图证据（2026-10-26）

| 文件 | 画面 | 证明咩 |
|------|------|--------|
| `flow-layers-L0.png` | 一单 L0 命中后的流程图 | 命中层绿框 + 「命中 · 416 ms」副标保留到下一单 |
| `flow-layers-L2.png` | 一单 L2 降级到视觉 | 上三层标 miss、L2 亮 |
| `hands-state-L0.png` | `打开设置`（L0）后嘅四条手 | adbd 绿、snapshothelper / WebView socket / Portal 虚线、云机 UI 绿 |
| `hands-state-L1a.png` | `点击网络和互联网`（L1a）后 | snapshothelper 绿、adbd 变淡（试过唔得）、其后两手虚线、云机 UI 绿 |
| `arch-four-hands.png` | 详细设计顶部架构图 | 四只手竖排在左、右边「云机 UI」大格并排，四条箭头对号入座 |
| `conv-panel.png` | 会话窗（亮色） | 聊天 + 输入框 + 遥测合成一块，同上面流程图隔离 |
| `steplog-scroll.png` | 实况窗下方步骤日志 | 按组件分行、内部可滚动 |
| `lstat-zoom.png` | 执行层占比四格放大 | 四格字号统一 9px、命中格加绿描边（`.it.cur`） |
