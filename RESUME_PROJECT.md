# Local GPT-Image Agent — 简历项目成果

> 按实习/校招面试标准整理，可直接摘录到简历「项目经历」栏。

---

## 项目概述（一句话）

**从零构建了一个生产级 AI Agent 图像生成服务平台**，打通 MCP 工具调用 → HTTP Bridge 服务化 → 异步任务队列 → 多 worker 并发 → 可观测性 → 稳定性治理 → 压测量化全套链路，具备简历可写的工程深度与量化指标。

---

## 技术栈

`Python` `HTTP Server(stdlib)` `MCP` `ComfyUI/Flux` `Prometheus` `Docker` `OpenAPI` `multiprocessing` `Token Bucket`

---

## 简历条目（中文版，可直接摘录）

### 项目：Local GPT-Image Agent — AI 图像生成服务平台

**角色：** 独立设计 & 全栈实现 | **周期：** 3 周（P0→P1→P2 迭代交付）

- **服务化架构：** 基于 Python stdlib 实现 HTTP Bridge 服务，提供同步/异步双模式 API（`POST /generate-image` 同步 + `POST /submit` 异步 + `GET /task/{id}` 轮询），解耦 MCP 工具与 ComfyUI 引擎，形成单一可运维链路。

- **异步任务队列：** 设计 PENDING → RUNNING → SUCCEEDED/FAILED/CANCELLED 完整状态机，基于线程池实现多 worker 并行消费，任务事件结构化落盘 JSONL，支持 request_id 全链路追踪。

- **稳定性治理：**
  - 可选 API Key 鉴权（`X-API-Key`）+ Token Bucket 限流（可配置 capacity/refill rate）
  - 单任务硬超时（multiprocessing 强制 terminate）+ 可配置重试策略（max_retries / exponential backoff），attempt 信息写入任务记录
  - 任务取消接口（`POST /task/{id}/cancel`），PENDING 状态可靠取消，RUNNING 状态 best-effort 标记

- **可观测性体系：**
  - 结构化 JSON 日志（含 request_id / task_id / latency）
  - Prometheus 指标端点（`/metrics` text/plain 格式），暴露计数器 + 延迟直方图（sync_latency / task_latency）
  - 审计字段：workflow_sha256 / comfyui_version / bridge_version 自动注入 normalized_request

- **可复现设计：** 每次请求的完整参数快照（normalized_request）持久化到 JSONL，支持事后复盘与回归对比；支持 profile → workflow 配置驱动路由。

- **MCP 集成：** 将 `qwenpaw_flux_image_mcp.py` 改为默认走 Bridge 异步链路（submit + poll），MCP 调用方无需感知底层队列/worker/重试逻辑。

- **交付资产：** Dockerfile + docker-compose + OpenAPI 3.0 规范文档 + 压测脚本 + 压测报告。

- **压测结果（量化）：**
  - **并发 2，512×512，steps=6**
  - 成功率：**100%**
  - 平均延迟：**43.6s** | P50：**42.2s** | P95：**57.4s** | P99：**57.4s**

---

## Resume Bullets（英文版，适合外企/英文简历）

### Local GPT-Image Agent — AI Image Generation Service Platform

**Role:** Independent Design & Full-Stack Implementation

- Built a production-grade HTTP Bridge service in Python (stdlib-only) with dual sync/async APIs, decoupling MCP tool calls from the ComfyUI engine into a single observable pipeline.

- Designed a full task lifecycle state machine (PENDING→RUNNING→SUCCEEDED/FAILED/CANCELLED) with multi-worker thread-pool execution, structured JSONL event persistence, and end-to-end request_id tracing.

- Implemented stability controls: optional API key auth, configurable token-bucket rate limiting, per-task hard timeout via multiprocessing, retry policy with attempt tracking, and task cancellation API.

- Built an observability stack: structured JSON logging, Prometheus `/metrics` endpoint (counters + latency histograms), and automatic audit metadata injection (workflow SHA-256, ComfyUI version, bridge version).

- Integrated MCP tool (`generate_flux_image`) to default-route through the Bridge async path (submit + poll), hiding queue/worker/retry complexity from callers.

- Delivered Dockerfile, docker-compose, OpenAPI 3.0 spec, load test script, and quantitative load test report.

- **Load test (quantified):** 6 requests, 2 concurrent workers, 512×512, steps=6 → **100% success rate**, avg latency **43.6s**, P50 **42.2s**, P95 **57.4s**.

---

## 面试话术（口语版，用于面试中展开讲）

### 1. 为什么做这个项目？

> 我在实习中接触到 AI Agent 工具调用（MCP），发现从"能跑"到"可交付服务"中间缺一整块工程化能力——异步、观测、容错、压测。我就拿自己本地的 Flux 图像生成流程，把它升级成一个完整的服务平台，补齐后端工程信号。

### 2. 技术选型为什么用 stdlib？

> Bridge 层刻意只用 Python 标准库（`http.server`、`threading`、`multiprocessing`、`json`），不依赖 FastAPI/Flask。目的有两个：一是证明自己对底层并发模型和 HTTP 协议有掌控力，二是降低部署依赖（一个 Python 文件就能跑）。

### 3. 超时和重试怎么做的？

> 单任务超时用 `multiprocessing.Process` 做硬超时——子进程跑 ComfyUI 调用，主进程 `join(timeout)` 后如果还活着就 `terminate`。重试在 worker 线程侧做：失败后标记 `__attempt`，按 backoff 等待后重新入队执行。所有 attempt 信息都写入任务 JSONL，事后可复盘。

### 4. 可观测性体现在哪？

> 每个请求带 `request_id`，贯穿日志/task/响应。`/metrics` 同时支持 JSON 和 Prometheus text/plain 两种格式。任务的 `normalized_request.audit` 里自动注入 workflow hash、ComfyUI 版本、bridge 版本——这样出问题能精确复现当时的软件栈。

### 5. 这个项目对你能力提升最大的是什么？

> 最大的收获是"从能跑升级到可交付"的思维方式：异步队列怎么设计状态机、取消语义怎么处理边界、硬超时在 Windows 上怎么 pickle-safe、限流用 token bucket 怎么算容量——这些都不是刷 LeetCode 能学到的，是真正做服务才碰得到的问题。

---

## 适配不同岗位的关键词侧重

| 岗位方向 | 简历侧重点 | 关键词 |
|---|---|---|
| **后端开发** | 异步队列/状态机/限流/超时重试/压测 | task queue, rate limiting, timeout, retry, load test |
| **AI Agent / MCP** | MCP 工具→Bridge→引擎全链路 | MCP, Agent tool call, Bridge, ComfyUI |
| **SRE / 平台工程** | 可观测性/Prometheus/审计/Docker | Prometheus metrics, structured logging, Docker, audit |
| **外企 / 英文面** | 工程规范/文档/量化指标 | OpenAPI, load test report, quantified metrics |
