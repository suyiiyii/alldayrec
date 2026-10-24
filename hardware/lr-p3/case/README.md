# LR-P3 外壳 v5.5L 源码

[build123d](https://github.com/gumyr/build123d) 模型：`enclosure_p3_v5_5L.py` 是外壳本身（每个尺寸都是命名参数，标 `MEASURE` 的是估计值），`export_p3.py` 导出 STL 和 STEP，`check_p3.py` 检查内部零件和外壳的干涉、锁扣、挂绳孔和最薄壁厚。依赖锁在 `requirements-freeze.txt`（Python 3.12、build123d 0.10.0、cadquery-ocp 7.8.1）。

在本目录运行（需要 [uv](https://docs.astral.sh/uv/)，第一次装依赖要几分钟），`P3=v5_5L` 必须设：

```bash
P3=v5_5L uv run --no-project --python 3.12 --with-requirements requirements-freeze.txt python check_p3.py fdm   # 或 mjf
P3=v5_5L uv run --no-project --python 3.12 --with-requirements requirements-freeze.txt python export_p3.py      # -> out/p3_v5_5L/{fdm,mjf}/{base,lid}.{stl,step}, assembled.step
```

导出的 STL 和 assets 分支上的文件逐字节相同（STEP 文件头里有导出时间，每次都不一样）。改了过盈（`sw_grip`、`board_crush`、`mb_crush`）只需要重打盖子。
