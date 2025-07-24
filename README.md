# XARL Email Workflow API

## 项目简介

本项目基于 FastAPI 实现，自动处理已解析邮件（PDF及附件），并通过 Dify 工作流进行智能处理，支持 Webhook 通知。适用于自动化邮件归档、智能分析、企业流程集成等场景。

## 主要功能
- 扫描 `processed_emails/` 目录下新邮件（PDF及附件）
- 上传邮件及附件到 Dify 平台，触发工作流
- 工作流结果自动保存到 `workflow_responses/`
- 支持通过 Webhook 发送通知
- 提供 RESTful API 接口，便于集成与自动化

## 快速开始

### 1. 环境准备
- Python 3.11+
- Docker & docker-compose（推荐生产环境）

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 启动服务
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```
或使用 Docker：
```bash
docker-compose up -d
```

### 4. 主要目录说明
- `app/`：FastAPI 应用主代码
- `processed_emails/`：待处理邮件（PDF及附件）
- `workflow_responses/`：工作流处理结果
- `downloaded_emails/`：原始邮件下载目录
- `fonts/`：字体文件（如有 PDF 处理需求）

## 环境变量说明
（建议通过 `.env` 文件或 CI/CD secrets 管理）
| 变量名 | 说明 |
|--------|------|
| DIFY_BASE_URL | Dify API 基础地址 |
| DIFY_API_KEY | Dify API 密钥 |
| USER_ID | 用户标识（如邮箱/姓名）|
| PROCESSED_DIR | 已处理邮件目录（默认 processed_emails）|
| WORKFLOW_RESPONSES_DIR | 工作流结果目录（默认 workflow_responses）|
| WEBHOOK_URL | Webhook 通知地址 |

## API 接口文档

### 1. 健康检查
- **GET /health**
- **说明**：服务健康检查，K8s/Docker/监控可用
- **响应示例**：
```json
{"status": "ok"}
```

### 2. 处理所有新邮件
- **POST /process-emails**
- **说明**：扫描 processed_emails/ 下所有新邮件，上传并触发 Dify 工作流，返回处理结果
- **响应示例**：
```json
[
  {
    "folder_name": "Wed_16_Jul_2025_0305_Microsoft_Azure_Team_alex.ma@huameisoft.c_[广告]_AD_参加我们举办的_Microsoft_Azur",
    "workflow_status": 200,
    "workflow_response": {"data": ...},
    "webhook_status": 200,
    "webhook_response": {"errcode":0, "errmsg":"ok"},
    "error": null
  },
  ...
]
```

- **字段说明**：
  - `folder_name`：邮件文件夹名
  - `workflow_status`：Dify 工作流 HTTP 状态码
  - `workflow_response`：Dify 工作流返回内容
  - `webhook_status`：Webhook 通知 HTTP 状态码
  - `webhook_response`：Webhook 返回内容
  - `error`：如有异常，返回错误信息

### 3. Swagger/OpenAPI 文档
- 访问 `http://<host>:8080/docs` 查看自动生成的交互式 API 文档

## 部署建议
- 推荐使用 Docker 部署，便于环境隔离与自动化
- 生产环境建议用 Nginx 反向代理并配置 HTTPS
- 环境变量、密钥等敏感信息请用 .env 或 CI/CD secrets 管理

## 贡献与反馈
如有建议、问题或需求，欢迎提交 Issue 或 PR。 