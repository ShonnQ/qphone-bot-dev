<!-- 标题：<类型>(<单号>): 一句话   例：feat(FR-20261027-1): 接管场景分类 -->

## 对应产品单

Closes #<!-- Issue 号，例：Closes #7 -->
产品单：`FR-YYYYMMDD-N` / `PR-YYYYMMDD-N`

## 改咗乜

- 

## 点证明（实测，必填）

| 验法 | 命令 / 入口 | 实测结果 |
|---|---|---|
|  | `curl --noproxy '*' 127.0.0.1:8777/...` |  |

- trajectory id：
- 截图 / 附件：

## 影响层

- [ ] L0 确定性命令
- [ ] L1a 无障碍树
- [ ] L1b WebView CDP
- [ ] L2 视觉兜底
- [ ] 壳 UI
- [ ] 文档（详细设计 / 里程碑）

## 版本

| 文件 | 版本 |
|---|---|
| `shell.html` | |
| `serve.py` | |
| `docs/详细设计.md` | v0. |

## 风险 / 回滚

- 风险：
- 回滚：`git revert <sha>`（唔使改数据；运行产物 `trajectories/` 唔入库）

## 自检

- [ ] `/health` 三字段（target / model / provider）对齐
- [ ] 真跑过一单，唔靠文档推断
- [ ] `docs/详细设计.md` 与 `详细设计.html` 同步重生
- [ ] 里程碑当日节已追加
