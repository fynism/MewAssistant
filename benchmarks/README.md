# T2Retrieval 中文检索测试

官方数据：[语料和问题](https://huggingface.co/datasets/C-MTEB/T2Retrieval)、[相关性标注](https://huggingface.co/datasets/C-MTEB/T2Retrieval-qrels)。

这是当前 SuperMew HTTP 接口的完整导入到检索测试：TXT 上传 → 清洗与分块 → 入库 → 混合检索/重排/合并 → 返回结果 → 标准 ID 对照评分。它不调用聊天 Agent，不评估生成答案。

## 1. 准备数据

在项目根目录的 PowerShell 中执行：

```powershell
uv run --no-project --with requests --with pyarrow python benchmarks/t2_retrieval.py prepare
```

首次下载完整官方 Parquet 文件，约 159 MB；下载缓存保存在 `data/benchmarks/downloads/`。pyarrow 只安装在 uv 临时环境，不修改项目依赖。

默认固定种子 42，从有标注、满足接口查询长度限制的问题中随机抽取 10 题。保留这些问题全部已标注相关文档，随机添加干扰文档，合计 80 份 TXT。数据和 manifest 保存在 `data/benchmarks/t2-smoke/`，manifest 记录源数据版本、问题、相关文档 ID、原始语料大小和抽样参数。

**这是自定义小语料库的流程测试。随机干扰文档较少，结果通常比全库任务容易，不能作为完整 T2Retrieval 基准成绩或业务知识库效果。** 10 题的均值波动也较大。正式评测应扩大问题集并保留完整检索库，另行配置测试环境的容量和吞吐量。

## 2. 启动项目

使用已有的本地或测试服务即可。若本地依赖和迁移已经配置好：

```powershell
uv run python -m backend.app
```

建议使用有至少 80 个剩余文件名额的测试账号。默认每账号最多 100 份文件；脚本会检查剩余额度。不要用这个脚本在正式知识库导入测试数据。

## 3. 上传并评测

另开一个 PowerShell，在项目根目录执行：

```powershell
uv run --no-sync python benchmarks/t2_retrieval.py run --username 你的测试用户名
```

终端提示输入密码，输入内容隐藏。也支持设置 `SUPERMEW_BENCHMARK_TOKEN` 为已有站内登录 JWT；个人 MCP API Key 不适用于站内调试接口。密码和 JWT 不写入文件。

测试其他部署地址：

```powershell
uv run --no-sync python benchmarks/t2_retrieval.py run --base-url https://你的测试域名 --username 你的测试用户名
```

脚本自动创建独立测试知识库，逐份上传，遇到 HTTP 429 遵照 Retry-After 等待。默认上传限制是每分钟 10 份，80 份至少需要数分钟，再加上向量化时间。全部文件 ready 后才开始评分；失败文件或服务错误会终止本次运行，不输出不完整的汇总分数。

上传超时或处理尚未结束时，可以重跑相同命令。脚本用本地 `upload-state.json` 找回知识库，根据服务端文件列表继续；不会自动删除测试知识库。测试结束后可在网页中删除该测试知识库。

## 4. 查看结果

`data/benchmarks/t2-smoke/results.json` 包含每道题的相关 ID、返回 ID、请求耗时，以及均值：

- Recall@5 / Recall@10：找回的不同相关文档数 / 已标注相关文档数。
- Hit@5 / Hit@10：前 K 条是否至少命中一篇相关文档。
- MRR@5 / MRR@10：第一篇相关文档的倒数排名。
- nDCG@5 / nDCG@10：使用二元相关性的折损排序分数。

接口返回的是分块，K 是返回分块的预算。同文档重复分块会占用位置，但只有第一次出现计相关增益，召回不会重复累加。每道题实际请求 `topK=10`，@5 取这次响应的前 5 条；因为服务端候选数量会受 topK 影响，这不等同于独立请求 `topK=5`。

请求耗时包含网络、冷启动、可能的限流等待，10 次串行请求不能用于评估 QPS 或稳定的 P95。脚本不强制或记录服务端模型配置；对比两次结果时，应记录服务版本、Embedding、重排、分块和合并配置，并使用相同 manifest。

## 更小的测试包

如果账号剩余额度不足，可重新选择输出目录和样本量：

```powershell
uv run --no-project --with requests --with pyarrow python benchmarks/t2_retrieval.py prepare --pack data/benchmarks/t2-small --queries 3 --docs 30
uv run --no-sync python benchmarks/t2_retrieval.py run --pack data/benchmarks/t2-small --username 你的测试用户名
```

若抽中问题的相关文档总数不小于文档预算，prepare 会明确报错；增加 --docs 或减少 --queries，不会丢弃相关文档来强行凑数。

## 扩大到 100 题、1,000 份文档

```powershell
uv run --no-project --with requests --with pyarrow python benchmarks/t2_retrieval.py prepare --pack data/benchmarks/t2-100q-1000d --queries 100 --docs 1000 --base-pack data/benchmarks/t2-isolated
```

`--base-pack` 保留上一轮所有问题和文档，再加入新问题的全部已标注相关文档及随机干扰文档。比较扩库影响时，应单独比较相同 10 道题；全部 100 题的均值用于描述扩大的问题集。

大样本必须使用隔离数据库、Milvus 集合、BM25 状态和 Redis 命名空间；测试账号的文件额度至少为 1,000。仅在该测试实例关闭上传和检索限流，避免限流等待混入速度指标。当前实测环境记录在 `data/benchmarks/t2-100q-1000d/test-environment.json`。

本轮使用单独目录安装的 CUDA PyTorch，通过测试进程的 `PYTHONPATH` 加载，项目原有 CPU PyTorch 保持原样。Embedding 模型仍为 BGE-M3，GPU 串行处理文档以控制显存。GPU 与 CPU 的速度不可直接比较；召回差异通过相同问题、相同评分规则和保留的基准文档分析。

逐文件强制刷盘在本机造成较大等待。因此扩大规模的召回实测采用离线批量建库：复用 `process_document` 的清洗、分块、向量化和元数据处理，每 100 份文件及结束时刷盘，核对所有文件与向量后才启动 HTTP 检索。仅私有测试进程延后刷盘，产品代码的持久化规则保持原样。该建库耗时不能作为现有上传接口的吞吐量。

上游重排服务另有 token/minute 限制。测质量时可在 `run` 命令中使用 `--query-interval 4`，每道题之间额外等待 4 秒；等待发生在请求计时之前。首次连续串行请求的结果保留在 `continuous-run/`，限速复测结果单独保留，避免将重排降级混同于检索库扩大的影响。
