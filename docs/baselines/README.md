# 壳基线存档

存档目的：老板讲「喺某一版基础加」，要即刻搵返嗰一版；`/tmp` 会清，所以入仓库。

| 版本 | 文件 | 特征 |
|------|------|------|
| v1.4（暗色） | `shell-dark-v1.4.html` | 默认暗黑 token；实况窗 `aspect-ratio:720/1600` + `object-fit:cover`（会裁左右，见 v1.5 修正）；遥测/会话头写死 `10532` |
| v1.5（亮色） | 现役 `shell.html` | 默认亮色 token（`:root`）+ `html[data-theme="dark"]` 返暗色；实况 `aspect-ratio:9/16` + `object-fit:contain`；`adb`/`chatSub`/`liveSize` 跟 target 与真实帧变 |
